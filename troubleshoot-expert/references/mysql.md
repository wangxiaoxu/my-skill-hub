# MySQL 故障定位

先区分四类：**锁等待、慢查询、连接池耗尽、主从延迟**。它们的表象都可能是「接口变慢」，但查的视图完全不同。

## 1. 死锁

```sql
SHOW ENGINE INNODB STATUS\G
```

只看 `LATEST DETECTED DEADLOCK` 段：

1. `TRANSACTION` 两段的 `TRX ID`、`TRX STARTED`、`WE ROLL BACK` 标出被回滚的事务。
2. `WAITING FOR THIS LOCK` 与 `HOLDS THE LOCK(S)` 给出等待与持有的索引记录。
3. 判断锁顺序是否相反：事务 A 先锁 `id=1` 再锁 `id=2`，事务 B 反过来，就是典型交叉死锁。

常见根因：

- 业务代码多表/多行更新顺序不一致
- 事务过长，锁持有期间夹带 RPC 或慢查询
- 缺少合适索引，范围更新升级为大量行锁甚至间隙锁
- `REPLACE INTO`、`INSERT ... ON DUPLICATE KEY UPDATE` 在唯一索引上产生间隙锁

修复方向：统一加锁顺序；缩短事务；补索引减少扫描范围；必要时降低隔离级别（需评估一致性影响）。不要用「重试」掩盖根因。

## 2. MDL（metadata lock）阻塞

典型现象：一条 `ALTER TABLE` 卡住，随后该表所有查询排队，应用全面超时。

正确排查顺序：

```sql
SELECT * FROM performance_schema.metadata_locks WHERE lock_status = 'PENDING';
SELECT * FROM performance_schema.metadata_locks WHERE lock_status = 'GRANTED';
SELECT * FROM information_schema.innodb_trx ORDER BY trx_started;
SELECT * FROM performance_schema.threads WHERE processlist_id = <id>;
```

关键点：**不要只看 `SHOW PROCESSLIST` 的 `Sleep`**。一个开启了事务但当前没有执行语句的连接，在 `PROCESSLIST` 里就是 `Sleep`，可它仍然持有 MDL，足以把 DDL 和后续查询全部堵住。必须去 `information_schema.innodb_trx` 看 `trx_state` 与 `trx_started`。

常见根因：

- 应用使用长事务（`autocommit=0` 后忘记提交）
- 连接池把连接借出后执行了 DDL 之外的语句却没有结束事务
- 备份/导出工具开启一致性快照并长时间持有
- 迁移脚本在 `initContainer` 里执行，卡住会拖死整个发布

修复方向：找到并结束空闲事务（先确认业务影响）；DDL 使用在线变更工具并设置锁等待超时；应用层保证事务尽快提交。

## 3. 慢查询

```sql
SHOW VARIABLES LIKE 'slow_query_log%';
SHOW VARIABLES LIKE 'long_query_time';
EXPLAIN <SQL>;
EXPLAIN ANALYZE <SQL>;   -- MySQL 8.0.18+
```

看这些字段：

- `type`：`ALL` 全表扫描，`index` 全索引扫描，`range` 范围扫描，`ref/eq_ref` 通常可接受。
- `rows`：预估扫描行数；与返回行数差距大说明索引选择性差。
- `Extra`：`Using filesort`、`Using temporary` 往往是大结果集排序/分组的信号；`Using index condition` 是好事。
- `filtered`：过滤后比例，过低说明扫描浪费。

常见根因：缺索引、隐式类型转换（字符串列与数字比较）、函数包裹索引列、`LIKE '%x'` 前导通配、深分页 `LIMIT 100000, 20`、大事务批量更新。

**常见误判**：`EXPLAIN` 的 `rows` 是估算值，不等于实际；用 `EXPLAIN ANALYZE` 或慢日志的 `Rows_examined` 对照。

## 4. 连接池耗尽

应用侧症状：获取连接超时、RT 阶梯式上升、线程池打满。

```sql
SHOW STATUS LIKE 'Threads_connected';
SHOW STATUS LIKE 'Threads_running';
SHOW VARIABLES LIKE 'max_connections';
SHOW PROCESSLIST;
```

排查顺序：

1. 看 `Threads_connected` 是否贴近 `max_connections`。
2. 按 `Command` 与 `Time` 分组，找长时间 `Sleep` 的连接（连接泄漏）或长时间 `Query`（慢 SQL 占住连接）。
3. 应用侧看连接池指标：活跃连接、等待队列、获取连接平均耗时。
4. 检查是否有未关闭的 `Connection`/`Cursor`、事务未提交、批处理占用连接过久。

修复方向：修泄漏、缩短事务、把慢 SQL 移出事务、合理设置池大小（不是越大越好，超过数据库承载会互相拖慢）。

## 5. 主从延迟

```sql
SHOW REPLICA STATUS\G   -- 8.0.22+；旧版本用 SHOW SLAVE STATUS\G
```

看：`Replica_IO_Running`、`Replica_SQL_Running`、`Seconds_Behind_Source`、`Relay_Log_Pos`、`Last_Error`。

常见根因：

- 大事务/批量写入，从库单线程回放慢
- 从库无主键表导致行更新全表扫描
- 从库磁盘或 CPU 瓶颈
- 网络抖动导致 IO 线程重连
- 主库 binlog 格式与从库参数不一致

**常见误判**：`Seconds_Behind_Source` 为 0 不代表没延迟，它只反映 SQL 线程与 IO 线程的时间差；长事务执行中可能长期显示 0。要结合 `pt-heartbeat` 或业务写入时间戳验证。

## 6. 磁盘与 binlog

- `binlog` 未清理会占满磁盘，先看 `SHOW BINARY LOGS` 与 `expire_logs_days`/`binlog_expire_logs_seconds`。
- `max_allowed_packet` 过小会让大字段写入报 `Packet too large`。
- `tmpdir` 空间不足会让 `Using temporary` 的查询直接失败。
- 磁盘满时 InnoDB 可能进入只读，错误信息通常很明确，不要先去怀疑锁。

## 通用只读检查顺序

```sql
SHOW PROCESSLIST;
SHOW ENGINE INNODB STATUS\G
SELECT * FROM information_schema.innodb_trx ORDER BY trx_started;
SELECT * FROM performance_schema.metadata_locks WHERE lock_status = 'PENDING';
SHOW STATUS LIKE 'Threads_%';
```

`KILL` 连接、`ALTER TABLE`、改参数都属于破坏性操作，执行前必须说明影响并等待确认。
