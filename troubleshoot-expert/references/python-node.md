# Python 与 Node 运行时故障

两类运行时的问题形状不同：Python 更常见的是异常栈与循环引用，Node 更常见的是事件循环阻塞与未处理的 Promise。

## Python：traceback 分层读法

### 从下往上读

```text
Traceback (most recent call last):
  File "app/api.py", line 42, in handler
    return service.load(user_id)
  File "app/service.py", line 88, in load
    return repo.get(user_id)
  File "app/repo.py", line 31, in get
    return self.session.execute(sql)
sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server")
```

1. **最后一行**是异常类型与消息，先读它。
2. **往上一帧**是直接抛出点（`repo.py:31`）。
3. 继续往上找**第一处业务代码**，那才是需要修的地方；框架内部帧通常只是转抛。
4. 如果出现 `During handling of the above exception, another exception occurred`，说明异常被二次包装；真正根因在**第一段** traceback。
5. `The above exception was the direct cause of the following exception` 表示显式 `raise ... from ...`，因果链是「上→下」。

### 常见异常与定位

| 异常 | 优先检查 |
|---|---|
| `ModuleNotFoundError` | 虚拟环境是否激活、包是否装在当前解释器、`sys.path` 是否被脚本目录污染 |
| `ImportError: cannot import name` | 循环导入；把 import 移到函数内或拆分模块 |
| `AttributeError: 'NoneType' object has no attribute` | 上游函数返回 `None`；用 `--` 打点或断点确认返回值 |
| `KeyError` | 配置/JSON 字段缺失；打印完整键集合而不是只打印值 |
| `UnicodeDecodeError` | 文件或响应编码；显式 `encoding=`，不要依赖系统默认 |
| `RecursionError` | 递归终止条件；树形结构是否有环 |
| `sqlite3.OperationalError: database is locked` | 长事务或未提交写事务；连接是否跨线程复用 |

### 循环引用与内存泄漏

Python 的引用计数回收不了环，`gc` 模块能回收大多数环，但带 `__del__` 的对象在旧版本里可能进入 `gc.garbage`。定位步骤：

```python
import gc, tracemalloc

tracemalloc.start()
# ... 复现操作 ...
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:20]:
    print(stat)
```

常见泄漏源：

- 模块级缓存/字典只增不减（`lru_cache(maxsize=None)`、全局 registry）
- 闭包持有大对象，回调注册后从不注销
- 异常对象持有 traceback，traceback 又持有栈帧与局部变量
- 线程/协程池持续创建但未回收
- C 扩展分配的内存不受 `tracemalloc` 统计，需要用 `gc.get_objects()` 与进程 RSS 对比

**常见误判**：看到 RSS 上涨就认定 Python 泄漏；分配器 arena 碎片、glibc 缓存、第三方 C 库都会让 RSS 高于活跃对象。先看活跃对象数量是否同步增长。

## Node：堆快照与内存

```bash
node --heapsnapshot-near-heap-limit=1 app.js
node --inspect app.js
```

- `--inspect` 后连接 Chrome DevTools，在 Memory 面板抓 Heap Snapshot；对比两次快照的 **Retained Size**，而不是 Shallow Size。
- `process.memoryUsage()` 区分 `heapUsed`、`heapTotal`、`external`、`arrayBuffers`；`external` 增长通常是 Buffer/ArrayBuffer 泄漏。
- `--max-old-space-size` 只限制老生代堆，不限制 Buffer 与原生内存。

常见泄漏源：

- 全局数组/Map 作为缓存，无淘汰策略
- 事件监听器重复注册（`EventEmitter` 默认最多 10 个警告就是提示）
- `setInterval` 未 `clearInterval`，回调闭包持有大对象
- Promise 链中持有的请求上下文未释放
- 流未消费完或未销毁

## Node：事件循环阻塞

症状：健康检查超时、接口 RT 整体抬升、CPU 不高但请求排队。

```javascript
const { monitorEventLoopDelay } = require("perf_hooks");
const h = monitorEventLoopDelay({ resolution: 20 });
h.enable();
setInterval(() => console.log(h.mean / 1e6, h.max / 1e6), 5000);
```

定位：

1. 同步 `fs` 调用、`crypto` 同步 API、大 JSON `JSON.parse/stringify`、复杂正则回溯都会阻塞事件循环。
2. `node --prof app.js` 生成 `isolate-*.log`，再 `node --prof-process isolate-*.log` 看 tick 分布。
3. 长任务拆成 `setImmediate` / `await` 让出事件循环；CPU 密集任务移到 `worker_threads`。

**常见误判**：把事件循环阻塞当成下游慢；如果所有接口同时变慢且下游指标正常，优先怀疑本进程阻塞。

## Node：unhandledRejection / uncaughtException

```bash
node --async-stack-traces app.js
```

- `unhandledRejection` 表示 Promise 被拒绝但没有 `.catch`；默认行为随 Node 版本变化，不能依赖「只打警告」。
- `uncaughtException` 后进程状态不可信，正确做法是记录日志并退出，由进程管理器重启。
- 异步栈默认只显示 Promise 链的尾部，`--async-stack-traces` 能补全调用链。
- 常见根因：`await` 漏写、`forEach` 里用 async 回调、事件监听器里返回被拒绝的 Promise。

**常见误判**：只加全局 `process.on("unhandledRejection")` 而不修根因，会把错误吞掉，表现为「偶发请求无响应」。
