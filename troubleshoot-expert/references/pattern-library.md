# 故障模式库

固定五段格式：**症状｜最可能根因｜一条验证命令｜一条修复方向｜误判提示**。

用法：先按症状关键词检索，再执行「验证命令」。验证结果与「最可能根因」不符时，直接跳到下一条候选，不要为了套结论而解释输出。命令中的 `<...>` 是占位符，执行前替换。

## A. Kubernetes

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| Pod 一直 CrashLoopBackOff，日志为空 | 启动命令或入口脚本不存在 | `kubectl describe pod <pod>` 看 Events 的 exec 错误 | 修正 command/args 或镜像工作目录 | 只看日志会以为应用没输出 |
| CrashLoopBackOff 且最后一条日志是数据库连接失败 | 依赖未就绪或连接串错误 | `kubectl logs <pod> --previous` | 增加启动重试或初始化容器 | 把依赖故障误判为应用崩溃 |
| ImagePullBackOff 且 Events 含 no such host | 镜像仓库域名拼写错误或 DNS 不通 | `kubectl describe pod <pod>` 复制完整镜像地址核对 | 改成 CI 输出里的原始地址 | 误判为镜像不存在 |
| ErrImagePull 且含 manifest unknown | tag 不存在或已被覆盖 | `crane manifest <image>` 或仓库页面查 tag | 改用存在的 tag 并固定 digest | 只看 Pod 状态不看 Events |
| OOMKilled 但 JVM 堆未满 | 堆外内存或容器 limit 过低 | `kubectl describe pod` 看 Last State Reason | 按 limit 的 60% 到 75% 设 Xmx 并留堆外空间 | 把 OOMKilled 当 Java OOM |
| Pending 且 Events 为 Insufficient cpu | 资源请求超过节点可分配量 | `kubectl describe pod <pod>` 看 FailedScheduling | 降 request、扩节点或调整调度 | 去翻应用日志，那里不会有 |
| Pending 且 PVC 未绑定 | StorageClass 不存在或容量不足 | `kubectl get pvc` 看 STATUS | 创建 StorageClass 或扩容量 | 把调度问题当存储问题 |
| Evicted 且 Message 为 low on ephemeral-storage | 容器写本地磁盘过多 | `kubectl describe node <node>` 看 DiskPressure | 清理日志缓存并加 emptyDir 限额 | 误判为应用崩溃 |
| Readiness probe failed 但容器仍在运行 | 探针路径或端口错误 | `kubectl exec <pod> -- curl -s localhost:<port><path>` | 修正 path/port 或 initialDelay | 把摘流量当成重启 |
| Liveness probe failed 导致反复重启 | initialDelay 小于启动耗时 | `kubectl describe pod <pod>` 看探针参数 | 调大 initialDelay 或 failureThreshold | 把启动慢当死锁 |
| Service 访问不通且 Endpoints 为空 | selector 与 Pod label 不匹配 | `kubectl get endpoints <svc>` | 修正 selector 或 label | 直接怀疑网络插件 |
| Pod 内域名解析失败 | search 域错误或 CoreDNS 异常 | `kubectl exec <pod> -- nslookup <svc>` | 使用 FQDN 或修 CoreDNS | 能 ping IP 就以为 DNS 没问题 |
| Rollout 卡住且新 Pod ImagePullBackOff | imagePullSecrets 缺失 | `kubectl rollout status` 加 describe 新 Pod | 补 Secret 或修仓库权限 | 只看 Deployment 状态 |
| ConfigMap 更新后应用行为不变 | 挂载未刷新或进程未重载 | `kubectl exec <pod> -- cat <config-path>` | 重启 Pod 或应用内热加载 | 以为改了就立即生效 |

## B. JVM

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| OutOfMemoryError Java heap space | 堆内对象过多或单次分配过大 | `jcmd <pid> GC.heap_info` | 修泄漏或分批处理 | 只加 Xmx 不找根因 |
| GC overhead limit exceeded | GC 回收效率过低 | `jstat -gcutil <pid> 1000` | 找泄漏并核对堆大小 | 当普通堆 OOM 而忽略 GC 指标 |
| Metaspace OOM | 动态生成类过多 | `jcmd <pid> VM.metaspace` | 限制动态类生成并设 MaxMetaspaceSize | 只调堆参数 |
| Direct buffer memory OOM | 堆外直接内存未释放 | `jcmd <pid> VM.native_memory summary` | 修 Netty/NIO 泄漏并限制直接内存 | 堆使用率正常就排除 |
| unable to create new native thread | 线程数或 ulimit 不足 | `jstack <pid>` 统计线程数与栈 | 降线程池上限并修泄漏 | 只调 Xss |
| Full GC 频繁且老年代居高不下 | 内存泄漏或对象晋升过快 | `jstat -gcutil <pid> 1000` 看 FGC 与 OU | 堆 dump 对比两个时间点 | 只加堆导致单次停顿更长 |
| 线程大量 BLOCKED 在同一把锁 | 锁竞争或死锁 | `jcmd <pid> Thread.print` 连抓三次 | 缩短临界区或换锁粒度 | 把正常等待当死锁 |
| CPU 100% 且 GC 线程占比高 | 分配速率过高或 GC 压力大 | `top -H -p <pid>` 对照 jstack 线程 | 降分配速率或调 GC 参数 | 直接扩容 |
| RSS 持续涨但堆稳定 | Direct Buffer 或 JNI 泄漏 | `pmap -x <pid>` 加 NMT | 定位原生分配点 | 把 RSS 当堆使用量 |
| 堆 dump 文件很小或为空 | dump 路径不可写或磁盘满 | `jcmd <pid> GC.heap_dump <path>` 看报错 | 换可写路径并留足空间 | 以为没发生过 OOM |

## C. Python

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| ModuleNotFoundError | 虚拟环境或解释器不一致 | `python3 -c "import sys; print(sys.executable)"` | 用同一解释器安装依赖 | 在系统 Python 里装了包 |
| ImportError cannot import name | 循环导入 | `python3 -X importtime -c "import <module>"` | 拆分模块或延迟导入 | 只调整 import 顺序掩盖问题 |
| AttributeError NoneType | 上游返回 None | 在调用前打印返回值与类型 | 补空值分支或修上游 | 只看报错行 |
| KeyError | 字段缺失或结构变化 | 打印完整键集合与原始 payload | 做字段校验与默认值 | 用 get 吞掉错误 |
| UnicodeDecodeError | 编码与预期不符 | `file -i <file>` 或打印前 32 字节 | 显式指定 encoding | 依赖系统默认编码 |
| RecursionError | 递归无终止或数据成环 | 打印递归深度与入参 | 加终止条件或改迭代 | 简单调高递归上限 |
| sqlite3 database is locked | 长事务或跨线程复用连接 | `lsof <db>` 看持有者 | 缩短事务并每线程独立连接 | 用重试掩盖锁设计问题 |
| 内存持续增长 | 全局缓存或闭包持有 | `tracemalloc` 对比两次快照 | 加淘汰策略并注销回调 | 只看 RSS 就认定泄漏 |
| asyncio Event loop is closed | 事件循环提前关闭 | 检查 create_task 的生命周期 | 在循环关闭前 await 任务 | 加 sleep 掩盖 |
| gunicorn worker timeout | 同步阻塞或死锁 | `py-spy dump --pid <pid>` | 改异步或加显式超时 | 只调 timeout 参数 |

## D. Node

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| JavaScript heap out of memory | 堆内对象过多 | `node --heapsnapshot-near-heap-limit=1 app.js` | 对比快照找 retained 大对象 | 只调 max-old-space-size |
| 接口整体变慢但 CPU 不高 | 事件循环被同步任务阻塞 | `monitorEventLoopDelay` 看 max 延迟 | 拆分长任务或用 worker_threads | 怀疑下游服务 |
| UnhandledPromiseRejection | Promise 缺 catch 或漏写 await | `node --async-stack-traces app.js` | 补错误处理并修调用链 | 加全局 handler 吞错 |
| EADDRINUSE | 端口被占用 | `lsof -i :<port>` | 换端口或停旧进程 | 反复重启 |
| ENOSPC | 磁盘或 inode 满 | `df -h` 与 `df -i` | 清理日志与临时文件 | 只看容量不看 inode |
| Cannot find module | 依赖未安装或路径大小写不符 | `node -e "require.resolve('<module>')"` | 重装依赖并核对路径 | 在错误目录执行 |
| EACCES | 文件权限或端口权限 | `ls -l <path>` | 修权限或改用非特权端口 | 直接用 root 运行 |
| 内存涨且 external 增长 | Buffer 或 ArrayBuffer 泄漏 | `process.memoryUsage()` 定时对比 | 释放流与 Buffer 引用 | 只看 heapUsed |

## E. MySQL

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| Deadlock found when trying to get lock | 加锁顺序不一致 | `SHOW ENGINE INNODB STATUS` 看 LATEST DETECTED DEADLOCK | 统一加锁顺序并缩短事务 | 简单加重试 |
| Lock wait timeout exceeded | 长事务或索引缺失导致锁范围过大 | `SELECT * FROM information_schema.innodb_trx` | 结束空闲事务并补索引 | 只看 PROCESSLIST 的 Sleep |
| ALTER TABLE 卡住且全表查询排队 | 空闲事务持有 MDL | `SELECT * FROM performance_schema.metadata_locks WHERE lock_status = 'PENDING'` | 找出并结束持锁事务 | 误以为 DDL 本身慢 |
| Too many connections | 连接泄漏或池过大 | `SHOW STATUS LIKE 'Threads_connected'` | 修泄漏并合理设置池大小 | 直接调大 max_connections |
| 查询突然变慢且 type=ALL | 缺索引或隐式类型转换 | `EXPLAIN <SQL>` 看 type 与 rows | 补索引并统一字段类型 | 只看 SQL 文本 |
| 主从延迟持续增长 | 大事务或从库无主键 | `SHOW REPLICA STATUS` 看 Seconds_Behind_Source | 拆大事务并补主键 | 看到延迟为 0 就放心 |
| 磁盘被 binlog 占满 | binlog 未清理 | `SHOW BINARY LOGS` | 设置过期时间并清理 | 只删数据文件 |
| Packet too large | max_allowed_packet 太小 | `SHOW VARIABLES LIKE 'max_allowed_packet'` | 调大参数并检查大字段 | 以为是网络问题 |
| Duplicate entry for key | 唯一索引冲突 | 看错误里的索引名与冲突值 | 做幂等或改写入逻辑 | 直接忽略错误 |
| 从库 SQL 线程停止 | 主从数据不一致 | `SHOW REPLICA STATUS` 看 Last_Error | 按错误修复数据后重启复制 | 直接跳过错误 |
| 大量 Using temporary 且磁盘涨 | 排序或分组结果过大 | `EXPLAIN <SQL>` 看 Extra | 补索引或缩小结果集 | 只扩 tmpdir |
| 连接空闲但事务未提交 | autocommit=0 或事务边界错误 | `SELECT * FROM information_schema.innodb_trx` 看 trx_started | 修事务边界 | 把 Sleep 当空闲 |

## F. Redis 与消息队列

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| max number of clients reached | 连接泄漏或池配置过大 | `CLIENT LIST` 按 idle 分组 | 复用连接池并修泄漏 | 只调 maxclients |
| DEL 大 key 时延迟尖刺 | 同步释放大对象 | `MEMORY USAGE <key>` | 改用 UNLINK 并拆分 key | 把尖刺归因于网络 |
| 单分片 CPU 打满 | 热 key | `redis-cli --hotkeys`（需 LFU） | 本地缓存或打散 key | 平均 QPS 看起来不高 |
| KEYS 导致全实例阻塞 | 全库扫描命令 | `SLOWLOG GET 100` | 禁用 KEYS 改用 SCAN | 慢日志没开就查不到 |
| OOM command not allowed | maxmemory 达上限且 noeviction | `INFO memory` 看 used_memory | 设置合理淘汰策略 | 直接扩容 |
| 持久化时延迟抖动 | fork 复制页表 | `INFO persistence` 看 latest_fork_usec | 降实例内存或换持久化方式 | 归因于客户端 |
| Stream 积压持续增长 | 消费速度小于生产速度 | `XINFO GROUPS <stream>` 看 lag | 扩消费者或优化处理耗时 | 只看 XLEN |
| 消息被重复消费 | ack 时机错误或可见性超时过短 | `XPENDING <stream> <group>` 看 idle | 处理完成后 ack 并做幂等 | 只查队列不查业务 |
| 消费者 idle 很大但仍在组内 | 消费者进程已死未摘除 | `XINFO CONSUMERS <stream> <group>` | 清理死消费者并加健康检查 | 以为消息丢了 |
| CLUSTERDOWN | 槽位未全覆盖 | `CLUSTER INFO` 与 `CLUSTER NODES` | 修复节点并重新分配槽位 | 只重启单个节点 |

## G. 网络与网关

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| 502 Bad Gateway | 上游连接被拒绝或进程崩溃 | `python3 scripts/health_probe.py <upstream>` | 修上游并核对端口 | 直接重启网关 |
| 504 Gateway Timeout | 上游响应超过网关读超时 | 对照上游 RT 与网关 timeout 配置 | 优化慢接口或调超时 | 把 504 当网络丢包 |
| 421 Misdirected Request | Host 或 SNI 不在白名单 | `curl -v -H "Host: <domain>" https://<ip>/health` | 把域名加入白名单或禁用连接复用 | 误判为 TLS 证书问题 |
| NXDOMAIN | 域名不存在或 search 域错误 | `dig <name>` 与 `cat /etc/resolv.conf` | 使用 FQDN 并修 DNS 配置 | 直接改 hosts 掩盖 |
| 域名解析到错误 IP | 代理 fake-ip 或 DNS 污染 | `dig @<dns> <name>` 对比多个解析器 | 绕过代理或改 DNS | 只看一个解析结果 |
| TLS handshake failure | 证书过期或协议不匹配 | `openssl s_client -connect <host>:443` | 续期证书并统一协议版本 | 直接关闭校验 |
| 证书域名不匹配 | SNI 与证书 SAN 不一致 | `openssl s_client -servername <host> -connect <host>:443` | 换正确证书 | 用 IP 访问掩盖 |
| Connection reset by peer | 对端主动断开或中间设备重置 | `curl -v <url>` 看 RST 时机 | 检查 keepalive 与空闲超时 | 只重试 |
| 内网访问走了代理 | 代理环境变量未排除内网 | `env` 查看 proxy 与 NO_PROXY | 设置 NO_PROXY 或加 --noproxy | 以为服务挂了 |
| 偶发超时且各层都正常 | MTU 黑洞或长连接空闲回收 | `ping -s <size> -M do <host>` | 调整 MTU 或 keepalive | 只加超时时间 |
| 413 Payload Too Large | 请求体超过网关限制 | 看网关配置与请求 Content-Length | 调大限制或分片上传 | 只改应用不改网关 |
| 502 且上游日志无请求 | 网关与上游之间未建立连接 | `health_probe` 分层看 DNS/TCP/TLS | 修网络策略或端口 | 只看网关日志 |

## H. 系统与其他

| 症状 | 最可能根因 | 一条验证命令 | 一条修复方向 | 误判提示 |
|---|---|---|---|---|
| No space left on device | 磁盘满或 inode 满 | `df -h` 与 `df -i` | 清理文件并加监控 | 只看容量 |
| Too many open files | fd 泄漏或 ulimit 过低 | `lsof -p <pid>` 统计 | 修泄漏并调 ulimit | 只调参数 |
| 负载高但 CPU 不高 | iowait 或 D 状态进程 | `top` 看 wa 与 `ps -eo stat,comm` | 定位磁盘或网络 IO | 直接加 CPU |
| 服务重启后好了但很快复发 | 根因未解决 | 对比重启前后同一指标 | 找根因并加监控 | 把重启当修复 |
| 时间戳对不上 | 时区或时钟偏移 | `date` 与 `timedatectl` 对比各机器 | 统一 NTP 与时区 | 按本地时间硬排 |
| cron 任务未执行 | 时区、环境变量或权限 | 看 cron 日志与 `systemctl status cron` | 用绝对路径并显式设置环境 | 手动跑成功就以为没问题 |
| 权限被拒绝 | 文件属主或 SELinux | `ls -l` 与 `getenforce` | 修属主或策略 | 直接用 root 绕过 |
| 端口已被占用 | 旧进程未退出或配置冲突 | `lsof -i :<port>` | 停旧进程或换端口 | 反复重启 |
| systemd 服务反复重启 | 启动命令或依赖失败 | `systemctl status` 与 `journalctl -u <unit>` | 修启动条件并加限流 | 只看应用日志 |
| 容器内时间与宿主不一致 | 时区配置或挂载缺失 | `kubectl exec <pod> -- date` | 补 TZ 环境变量或挂载 | 以为应用算错 |
