# JVM 故障定位

JVM 问题先分四类：**堆内 OOM、堆外/原生内存、GC 停顿、线程阻塞**。先确认是哪一类，再选工具；不要一上来就调 `-Xmx`。

## 1. 七种 OutOfMemoryError

| 错误信息 | 真正缺什么 | 验证方式 | 修复方向 |
|---|---|---|---|
| `Java heap space` | 堆内对象过多或单次分配过大 | `jcmd <pid> GC.heap_info`；堆 dump 看支配对象 | 修泄漏；确认 `-Xmx` 与容器 limit 匹配；分批处理大数据 |
| `GC overhead limit exceeded` | 堆几乎满，GC 回收效率极低 | `jstat -gcutil <pid> 1000` 看 FGC 次数与 FGCT 增速 | 同上，且优先找泄漏而不是加堆 |
| `Metaspace` | 类元数据耗尽（动态生成类、热部署） | `jstat -gc <pid>` 看 MC/MU；`jcmd VM.metaspace` | 限制动态类生成；设置 `-XX:MaxMetaspaceSize` 并定位加载来源 |
| `Direct buffer memory` | 堆外直接内存耗尽 | `jcmd VM.native_memory summary`；看 `-XX:MaxDirectMemorySize` | 修 Netty/NIO 泄漏；显式释放 ByteBuffer；限制直接内存 |
| `unable to create new native thread` | 线程数或原生内存不足 | `jstack <pid>` 数线程；`ulimit -u`；`cat /proc/<pid>/status` 的 Threads | 降线程池上限；修线程泄漏；调整 ulimit（需评估） |
| `Requested array size exceeds VM limit` | 单次申请数组超过上限 | 异常栈定位分配点；检查长度计算是否溢出 | 分片处理；校验输入长度 |
| `Kill process or sacrifice child` | 操作系统 OOM killer 杀进程 | `dmesg -T` 找 oom-kill；容器 `Exit Code=137` | 降容器内存占用；核对 limit 与 JVM 堆外开销 |

**常见误判**：容器里发生 OOMKilled 时，JVM 往往来不及打印 `OutOfMemoryError`。看到 Exit 137 要先看内核日志与容器事件，而不是只翻 Java 日志。

## 2. 堆 dump 与对象定位

```bash
jcmd <pid> GC.heap_info
jcmd <pid> GC.class_histogram
jcmd <pid> GC.heap_dump /tmp/heap.hprof
jmap -histo:live <pid>
```

读法：

- `GC.class_histogram` 看实例数与总字节数，排前几位的类就是嫌疑对象。
- `jmap -histo:live` 会触发一次 Full GC，**生产环境慎用**，先说明影响。
- 堆 dump 用 MAT 或 VisualVM 打开，看 Dominator Tree，而不是只看直方图；直方图会把共享的大对象算多次。
- 对比两个时间点的直方图，增长最快的类才是泄漏源。

## 3. GC 停顿

```bash
jstat -gcutil <pid> 1000 20
jcmd <pid> GC.heap_info
```

看这些列：

- `YGC/YGCT`：年轻代回收次数与总耗时；`YGCT/YGC` 是平均停顿。
- `FGC/FGCT`：Full GC 次数与总耗时；FGC 持续增长且 `OU` 接近 `OU` 上限，说明堆真的不够或存在泄漏。
- `GCT` 占总运行时间的比例：超过 10% 通常已经影响吞吐。

常见根因：堆太小、对象晋升过快、大对象直接进老年代、元空间频繁扩容、显式 `System.gc()`、G1 的 Humongous 分配。

**常见误判**：只看平均停顿会掩盖长尾；要结合 GC 日志（`-Xlog:gc*`）看单次最长停顿。调大堆不一定减少停顿，可能让单次 Full GC 更久。

## 4. 线程与死锁

```bash
jcmd <pid> Thread.print
jstack -l <pid>
```

读法：

1. 先看 `Found one Java-level deadlock`，JVM 会直接列出互相等待的线程与锁。
2. 没有显式死锁时，统计 `java.lang.Thread.State`：大量 `BLOCKED` 指向同一把锁；大量 `WAITING` 在连接池或队列上说明下游慢。
3. 连续抓 3 次（间隔 5 秒），同一栈帧一直出现才是卡点；只抓一次容易把正常等待当阻塞。
4. `-l` 会额外打印 `Locked ownable synchronizers`，用于定位 `ReentrantLock`。

**常见误判**：线程数多不等于线程泄漏；要看线程名与栈是否重复。`TIMED_WAITING` 在 `sleep` 上是正常的。

## 5. 堆外内存

JVM 的 RSS 可能远大于堆，差额来自：Metaspace、线程栈、Direct Buffer、JNI、代码缓存、GC 自身结构。

```bash
jcmd <pid> VM.native_memory summary   # 需要启动时加 -XX:NativeMemoryTracking=summary
pmap -x <pid>
cat /proc/<pid>/status | grep -E "VmRSS|VmSwap"
```

判断顺序：

1. 堆使用稳定但 RSS 持续涨 → 优先怀疑 Direct Buffer 或 JNI 泄漏。
2. NMT 的 `Other` 或 `Internal` 持续增长 → 可能是原生库或线程栈。
3. RSS 涨但 NMT 各项稳定 → 看 `pmap` 的匿名映射，可能是 glibc arena 碎片。

**常见误判**：把 RSS 当堆使用量；把容器 OOMKilled 当成 Java 堆 OOM。两者修复方向完全不同。
