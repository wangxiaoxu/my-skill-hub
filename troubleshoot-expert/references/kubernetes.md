# Kubernetes 故障定位

统一顺序：`kubectl get` 看状态 → `kubectl describe` 看事件 → `kubectl logs` 看应用 → 必要时进容器或看节点。**先看 Events，再看日志**，多数启动失败的原因写在 Events 里。

## CrashLoopBackOff

**读什么**

```bash
kubectl -n <ns> get pod <pod> -o wide
kubectl -n <ns> describe pod <pod>
kubectl -n <ns> logs <pod> --previous
```

**看哪个字段**：`Last State` 的 `Exit Code` 与 `Reason`；Events 里的 `Back-off restarting failed container`；`--previous` 日志的最后一个异常。

**常见根因**

- 启动时依赖未就绪（DB、Redis、配置中心）→ 应用直接退出
- 环境变量或 Secret 缺失 → 配置解析失败
- 启动命令/入口脚本路径错误 → `exec format error` 或 `no such file`
- 迁移脚本失败 → 进程退出码非 0
- 健康检查端口与监听端口不一致

**常见误判**：只看当前日志看不到崩溃前输出，必须加 `--previous`；退避时间会指数增长，`describe` 里的 `Restart Count` 增长慢不代表没在崩。

## ImagePullBackOff / ErrImagePull

**读什么**

```bash
kubectl -n <ns> describe pod <pod>
```

**看哪个字段**：Events 中的完整镜像地址与错误串，例如 `manifest unknown`、`unauthorized`、`no such host`。

**常见根因**

- 镜像仓库域名拼错（`EOF`、`no such host` 往往是域名问题，不是镜像不存在）
- tag 不存在或已被覆盖
- `imagePullSecrets` 缺失或无权限
- 私有仓库证书不被节点信任
- 多架构镜像缺少当前节点架构

**常见误判**：把 `ImagePullBackOff` 当成应用问题；实际容器从未启动，日志为空是正常的。复制 CI 输出里的镜像地址整串比对，不要手打。

## OOMKilled

**读什么**

```bash
kubectl -n <ns> describe pod <pod>
kubectl -n <ns> get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}'
```

**看哪个字段**：`Last State.Terminated.Reason=OOMKilled`、`Exit Code=137`；节点 `dmesg` 中的 oom-killer 记录。

**常见根因**

- `resources.limits.memory` 低于实际峰值
- 堆外内存（Direct Buffer、JNI、线程栈）未计入 JVM 堆，但计入容器 limit
- 缓存无上限、批量任务一次性载入
- 同一 Pod 多容器共享 limit

**常见误判**：JVM 堆没满不等于不会 OOMKilled；`-Xmx` 应按容器 limit 的 60%–75% 设置并留出堆外空间。Exit 137 也可能是 `SIGKILL`（如驱逐），要结合 Events 区分。

## Pending

**读什么**

```bash
kubectl -n <ns> describe pod <pod>
kubectl -n <ns> get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory
```

**看哪个字段**：Events 里的 `FailedScheduling` 原因：`Insufficient cpu/memory`、`node(s) had untolerated taint`、`pod has unbound immediate PersistentVolumeClaims`、亲和性不匹配。

**常见根因**

- 资源请求超过节点可分配量
- PVC 未绑定（StorageClass 不存在、容量不足）
- 节点选择器/亲和性/污点与容忍不匹配
- 集群达到 Pod 数量上限

**常见误判**：只看 Pod 状态不看 Events；Pending 是调度问题，不是应用问题，日志里什么都不会有。

## Evicted

**读什么**

```bash
kubectl -n <ns> describe pod <pod>
kubectl describe node <node>
```

**看哪个字段**：`Status.Reason=Evicted`、`Status.Message`（如 `The node was low on resource: ephemeral-storage`）；节点 `Conditions` 中的 `DiskPressure`、`MemoryPressure`。

**常见根因**

- 容器写本地磁盘过多（日志、临时文件、缓存）
- 节点内存压力触发驱逐
- emptyDir 或镜像层占满磁盘

**常见误判**：把 Evicted 当成应用崩溃；实际是 kubelet 主动驱逐，应用可能没有任何错误日志。

## 探针失败（Liveness / Readiness / Startup）

**读什么**

```bash
kubectl -n <ns> describe pod <pod>
kubectl -n <ns> get events --field-selector involvedObject.name=<pod>
```

**看哪个字段**：Events 中 `Liveness probe failed`、`Readiness probe failed` 的具体错误（`connection refused`、`context deadline exceeded`、`HTTP status 404`）；探针的 `initialDelaySeconds`、`timeoutSeconds`、`failureThreshold`。

**常见根因**

- 探针路径/端口与实现不一致
- 应用启动慢于 `initialDelaySeconds`
- 探针依赖下游，下游抖动导致误杀
- `timeoutSeconds` 小于正常响应时间

**常见误判**：Liveness 失败会重启容器，Readiness 失败只摘流量；把 Readiness 失败当崩溃会误判。探针不要依赖外部依赖，否则下游故障会放大成自我重启。

## Pod 内 DNS 解析失败

**读什么**

```bash
kubectl -n <ns> exec <pod> -- cat /etc/resolv.conf
kubectl -n <ns> exec <pod> -- nslookup <service>.<ns>.svc.cluster.local
kubectl -n <ns> get svc <service> -o wide
kubectl -n <ns> get endpoints <service>
```

**看哪个字段**：`resolv.conf` 的 `nameserver` 与 `search`；Endpoints 是否为空；`nslookup` 返回的 IP 是否是期望的 ClusterIP。

**常见根因**

- Service selector 与 Pod label 不匹配 → Endpoints 为空
- 跨命名空间访问少了 `.svc.cluster.local` 或命名空间
- CoreDNS 副本异常或网络策略阻断
- `ndots` 与短域名导致解析走错 search 域

**常见误判**：能 ping 通 IP 但域名不通，说明网络没问题而是解析问题；反过来，DNS 正常但连接超时，要看 NetworkPolicy 与端点。

## 通用排查命令顺序

```bash
kubectl get pods -o wide
kubectl describe pod <pod>
kubectl logs <pod> --previous
kubectl get events --sort-by=.lastTimestamp
kubectl top pod <pod>
kubectl exec -it <pod> -- sh
```

**注意**：`kubectl delete pod`、`rollout restart`、`scale` 都属于会改变线上状态的操作，执行前先说明影响并等待确认。
