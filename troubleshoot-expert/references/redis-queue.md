# Redis 与消息队列故障定位

Redis 的问题通常表现为「变慢」或「连不上」，但根因分散在连接、内存、命令、集群四层。消息队列则多一层「积压与重复消费」。

## 1. 连接数打满

```bash
redis-cli -h <host> INFO clients
redis-cli -h <host> CLIENT LIST
redis-cli -h <host> CONFIG GET maxclients
```

看 `connected_clients` 是否贴近 `maxclients`；`CLIENT LIST` 按 `age`、`idle`、`cmd` 分组：

- 大量 `idle` 很大且 `cmd=client` 的连接：应用连接池泄漏或缩容后旧连接未释放。
- 大量 `cmd=subscribe`：订阅者未正确断开。
- 大量短连接：客户端未复用连接池，或每次请求新建连接。

修复方向：应用侧复用连接池、设置合理 `maxIdle`/`maxTotal`、确保异常路径关闭连接；服务端调整 `maxclients` 前先确认文件描述符上限。

**常见误判**：`TIME_WAIT` 多不等于 Redis 连接泄漏；它可能是客户端侧短连接造成的，需在客户端机器上看 `ss -s`。

## 2. 大 key 与热 key

**大 key**：单个 String 达 MB 级，或 Hash/List/Set/ZSet 元素过多。

```bash
redis-cli --bigkeys
redis-cli MEMORY USAGE <key>
redis-cli STRLEN <key>
redis-cli LLEN <key>
redis-cli HLEN <key>
```

危害：`DEL` 大 key 会阻塞主线程；网络传输占用带宽；主从同步与持久化抖动。

修复方向：拆分 key、使用 `UNLINK` 异步删除、限制单 value 大小、对大集合分页扫描（`SCAN` 而非 `KEYS`）。

**热 key**：某个 key 的 QPS 远高于其他 key，打满单个分片或单核。

```bash
redis-cli --hotkeys          # 需要 maxmemory-policy 为 LFU
redis-cli --latency-history
```

修复方向：本地缓存、key 加随机后缀分散、读写分离、限流。

**常见误判**：`--bigkeys` 基于采样，可能漏掉低频大 key；关键业务要用 `MEMORY USAGE` 精确确认。

## 3. 阻塞命令

Redis 单线程执行命令，任何慢命令都会阻塞所有客户端。

```bash
redis-cli SLOWLOG GET 100
redis-cli LATENCY HISTORY command
redis-cli INFO commandstats
```

高危命令：

- `KEYS *`：全库扫描，生产禁用，改用 `SCAN`。
- `FLUSHALL` / `FLUSHDB`：同步释放大量内存。
- 大 key 上的 `DEL`、`HGETALL`、`SMEMBERS`、`LRANGE 0 -1`。
- `BLPOP`/`BRPOP` 本身不阻塞其他客户端，但会占住连接；大量阻塞客户端会耗尽连接数。
- Lua 脚本执行时间长，阻塞整个实例。
- `WAIT` 会阻塞等待副本确认。

修复方向：禁用高危命令（`rename-command`）、拆分大 key、Lua 脚本限制执行时间、用 `UNLINK` 替代 `DEL`。

## 4. 内存与淘汰

```bash
redis-cli INFO memory
redis-cli CONFIG GET maxmemory
redis-cli CONFIG GET maxmemory-policy
redis-cli INFO stats
```

看 `used_memory` 与 `maxmemory` 的比值、`evicted_keys` 增速、`mem_fragmentation_ratio`。

常见根因：

- 未设置 `maxmemory` 或策略为 `noeviction`，写入报 `OOM command not allowed`。
- 淘汰策略与业务不匹配（缓存用 `allkeys-lru`，持久数据不能用淘汰）。
- 碎片率高（`mem_fragmentation_ratio > 1.5`）但 `used_memory` 稳定，可能是频繁增删导致。
- 持久化 fork 时 `used_memory_rss` 翻倍，容器 limit 不足会 OOM。

**常见误判**：`used_memory` 高不等于泄漏；先看 key 数量与业务增长是否匹配。`INFO memory` 的 `used_memory_dataset` 更接近真实数据占用。

## 5. 集群与主从

- `MOVED`：客户端未使用集群模式或槽位缓存过期；检查客户端是否支持 cluster 并刷新拓扑。
- `ASK`：槽位迁移中，客户端需跟随重定向。
- `CLUSTERDOWN`：槽位未全覆盖，检查 `CLUSTER INFO` 与 `CLUSTER NODES`。
- 主从延迟：`INFO replication` 的 `master_repl_offset` 与 `slave_repl_offset` 差值。
- 脑裂：网络分区后旧主仍在写入，恢复后数据冲突；需要 `min-replicas-to-write` 等保护。

## 6. 消息队列：积压与重复消费

以 Redis Stream 与常见队列为例：

```bash
redis-cli XLEN <stream>
redis-cli XINFO GROUPS <stream>
redis-cli XPENDING <stream> <group>
redis-cli XAUTOCLAIM <stream> <group> <consumer> <min-idle-time> 0 COUNT 100
```

排查顺序：

1. **积压量**：`XLEN` 与 group 的 `lag` 持续增长，说明消费速度小于生产速度。
2. **消费者存活**：`XINFO CONSUMERS` 看 `idle`，长时间 idle 的消费者可能已死但未摘除。
3. **待确认消息**：`XPENDING` 的 `min/max idle` 很大，说明消息被取出但未 ack，常见于处理异常或进程崩溃。
4. **重复消费**：ack 时机不对（先 ack 后处理）、可见性超时短于处理时间、消费者重启后重新投递。

修复方向：提高消费者并发或优化单条处理耗时；设置死信队列；处理完成后 ack；业务侧做幂等（唯一键、去重表、状态机）。

**常见误判**：只看队列长度不看 `lag`；多个消费组时总长度正常但某个组已严重积压。重复消费不一定是队列问题，先确认业务处理是否幂等。
