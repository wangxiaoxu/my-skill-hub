import json
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_schedule.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def call_cli(*args, **kwargs):
    argv = [sys.executable, str(SCRIPT)] + list(args)
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


class ReviewScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.records = Path(self.tempdir.name) / "records.json"
        shutil.copy(str(FIXTURES / "records_initial.json"), str(self.records))

    def tearDown(self):
        self.tempdir.cleanup()

    def plan(self, days=7, today="2026-10-26"):
        result = call_cli(
            "--records",
            str(self.records),
            "--days",
            str(days),
            "--today",
            today,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def advance(self, card_id, grade, today="2026-10-26"):
        result = call_cli(
            "--records",
            str(self.records),
            "--advance",
            card_id,
            "--grade",
            str(grade),
            "--today",
            today,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_plan_is_deterministic(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)

    def test_plan_buckets_and_overdue(self):
        plan = self.plan()
        self.assertEqual(plan["totalDue"], 3)
        self.assertEqual(plan["overdue"], 1)
        self.assertEqual(plan["schedule"][0]["count"], 2)
        self.assertEqual(plan["schedule"][2]["count"], 1)
        first_day = {card["id"]: card for card in plan["schedule"][0]["cards"]}
        self.assertIn("c1", first_day)
        self.assertIn("c3", first_day)
        self.assertEqual(first_day["c3"]["overdueDays"], 3)
        self.assertEqual(plan["schedule"][1]["count"], 0)

    def test_successful_grade_grows_interval(self):
        first = self.advance("c1", 5, today="2026-10-26")
        self.assertEqual(first["after"]["interval"], 1)
        self.assertEqual(first["after"]["repetitions"], 1)
        self.assertEqual(first["after"]["ease"], 2.6)
        self.assertEqual(first["after"]["mastery"], 0.8)
        self.assertEqual(first["after"]["due"], "2026-10-27")

        second = self.advance("c1", 5, today="2026-10-27")
        self.assertEqual(second["after"]["interval"], 6)
        self.assertEqual(second["after"]["repetitions"], 2)
        self.assertEqual(second["after"]["ease"], 2.7)
        self.assertEqual(second["after"]["mastery"], 1.0)
        self.assertEqual(second["after"]["due"], "2026-11-02")

        third = self.advance("c1", 4, today="2026-11-02")
        self.assertEqual(third["after"]["interval"], 16)
        self.assertEqual(third["after"]["repetitions"], 3)
        self.assertEqual(third["after"]["ease"], 2.7)
        self.assertEqual(third["after"]["due"], "2026-11-18")

    def test_failed_grade_resets_repetitions(self):
        payload = self.advance("c2", 2, today="2026-10-26")
        after = payload["after"]
        self.assertEqual(after["interval"], 1)
        self.assertEqual(after["repetitions"], 0)
        self.assertEqual(after["ease"], 2.18)
        self.assertEqual(after["mastery"], 0.55)
        self.assertEqual(after["due"], "2026-10-27")

    def test_advance_persists_to_records(self):
        self.advance("c1", 5)
        saved = json.loads(self.records.read_text(encoding="utf-8"))
        card = [item for item in saved["cards"] if item["id"] == "c1"][0]
        self.assertEqual(card["due"], "2026-10-27")
        self.assertEqual(card["repetitions"], 1)

    def test_window_boundary_excludes_day_after_window(self):
        payload = {
            "version": 1,
            "cards": [
                {"id": "edge-in", "title": "窗口内", "due": "2026-11-01"},
                {"id": "edge-out", "title": "窗口外", "due": "2026-11-02"},
            ],
        }
        self.records.write_text(json.dumps(payload), encoding="utf-8")
        plan = self.plan(days=7, today="2026-10-26")
        ids = [
            card["id"]
            for day in plan["schedule"]
            for card in day["cards"]
        ]
        self.assertIn("edge-in", ids)
        self.assertNotIn("edge-out", ids)
        self.assertEqual(plan["schedule"][6]["count"], 1)

    def test_invalid_grade_exits_2(self):
        result = call_cli(
            "--records",
            str(self.records),
            "--advance",
            "c1",
            "--grade",
            "6",
            "--today",
            "2026-10-26",
        )
        self.assertEqual(result.returncode, 2)

    def test_grade_without_advance_exits_2(self):
        result = call_cli("--records", str(self.records), "--grade", "4")
        self.assertEqual(result.returncode, 2)

    def test_unknown_card_exits_2(self):
        result = call_cli(
            "--records",
            str(self.records),
            "--advance",
            "missing",
            "--grade",
            "4",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("找不到卡片", result.stderr)

    def test_missing_records_exits_2(self):
        result = call_cli(
            "--records",
            str(Path(self.tempdir.name) / "missing.json"),
            "--today",
            "2026-10-26",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("不存在", result.stderr)

    def test_help(self):
        result = call_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main(verbosity=1)
