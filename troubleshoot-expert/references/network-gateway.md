# 网络与网关故障定位

网络问题最怕「一上来就抓包」。正确顺序是分层排除：`DNS → TCP → TLS → HTTP → 网关 → 上游`。每一层都有独立的失败特征，先确定层，再决定要不要抓包。

## 1. 分层定位法

| 层 | 失败表现 | 一条验证命令 |
|---|---|---|
| DNS | `no such host`、`NXDOMAIN`、解析到错误 IP | `dig <name>` 或 `getent hosts <name>` |
| TCP | `connection refused`、`connection timed out` | `nc -vz <host> <port>` 或 `health_probe.py <host>:<port>` |
| TLS | `certificate has expired`、`handshake failure` | `openssl s_client -connect <host>:443 -servername <host>` |
| HTTP | 状态码异常、响应头缺失 | `curl -v <url>` |
| 网关 | 502/504/421、上游地址错误 | 网关 access log + 上游 health |
| 上游 | 应用报错、RT 高、连接池满 | 上游日志与指标 |

`health_probe.py` 会把失败直接分成 `dns / tcp / tls / http / timeout`，适合作为第一步：

```bash
python3 scripts/health_probe.py https://api.example.com/health --timeout 3 --json
```

## 2. 502 / 504 / 421

### 502 Bad Gateway

含义：网关从上游收到了无效响应，或根本连不上上游。

排查顺序：

1. 上游进程是否存活、端口是否监听：`health_probe <upstream-host:port>`。
2. 网关配置的上游地址与端口是否与实际上游一致（发布后端口变了很常见）。
3. 上游是否在返回响应前崩溃（看上游日志最后几条与 OOM/重启记录）。
4. 连接复用是否把请求发到了已关闭的连接（keepalive 与上游 idle timeout 不匹配）。

**常见误判**：502 出现就重启网关；如果上游已经挂了，重启网关只会短暂缓解。

### 504 Gateway Timeout

含义：网关等上游响应超时。

1. 对照三处超时：客户端超时、网关 `proxy_read_timeout`、上游自身超时。最短的那个先触发。
2. 看上游 RT 分位数，而不是平均值；长尾慢请求最容易触发 504。
3. 上游是否在等锁、等数据库、等下游，导致线程占满。
4. 是否是特定大请求（导出、批量查询）超过超时。

**常见误判**：把 504 当成网络丢包；多数 504 是上游处理慢，不是链路问题。

### 421 Misdirected Request

含义：请求被发到了不适合它的连接或主机。

常见根因：

- HTTP/2 连接复用（connection coalescing）把不同域名的请求复用到同一连接，而服务端不接受该 `Host`。
- 网关/负载均衡的 `Host` 白名单不包含当前访问域名（例如通过 NodePort、临时域名或 IP 访问）。
- TLS SNI 与 HTTP `Host` 不一致。
- 反向代理改写了 `Host`，上游按错误主机路由。

排查：

```bash
curl -v -H "Host: <expected-host>" https://<ip>/health
openssl s_client -connect <ip>:443 -servername <expected-host>
```

**常见误判**：看到 421 就怀疑证书；421 是应用层的主机校验，证书问题通常报 TLS 错误。

## 3. DNS 陷阱

- **search 域**：容器内短域名会依次拼接 `/etc/resolv.conf` 的 search 域，可能解析到错误命名空间。
- **fake-ip 代理**：本地代理可能把域名解析成 `198.18.x.x` 之类的假 IP，导致直连内网失败。
- **多解析器不一致**：不同机器使用不同 DNS，结果不同；用 `dig @<dns>` 指定解析器对比。
- **缓存**：TTL 未过期时，变更不会立即生效；先确认解析结果与预期。
- **IPv6 优先**：客户端优先走 AAAA，但服务只监听 IPv4，表现为「偶发连不上」。

判断方法：`getent hosts <name>` 与 `dig <name>` 结果不一致时，先看 `nsswitch.conf` 与 hosts 文件。

## 4. TLS 问题

```bash
openssl s_client -connect <host>:443 -servername <host> -showcerts
```

看：

- `Verify return code`：非 0 表示证书链或有效期问题。
- 证书 `Not After`：是否过期。
- SAN 是否包含访问域名。
- 服务端支持的协议与加密套件是否与客户端匹配。
- 是否需要客户端证书（mTLS）。

常见根因：证书过期、链不完整（缺中间证书）、访问 IP 而非域名导致 SNI 不匹配、客户端过旧不支持新协议。

**不要**用 `curl -k` 或关闭校验来「验证」问题已解决；那只是跳过验证。

## 5. 代理与环境变量

```bash
env | grep -i proxy
```

注意：

- `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 都可能影响命令行工具与 SDK。
- 不同工具大小写敏感性不同；`http_proxy` 与 `HTTP_PROXY` 可能只认一个。
- 内网域名必须加入 `NO_PROXY`，否则请求被送到代理，表现为超时或 502。
- 排查时显式绕过：`curl --noproxy '*'`。
- 容器、CI、IDE 各自可能注入不同的代理变量，不要假设环境一致。

**常见误判**：浏览器能访问、命令行不能，通常是代理变量或 DNS 差异，不是服务端问题。

## 6. 超时链

一次请求可能经过：客户端 → CDN → 网关 → 服务 A → 服务 B → 数据库。每层都有超时，最终表现为最外层错误。

排查方法：

1. 列出链路每一跳的超时配置。
2. 找到最短超时，它决定了用户看到的上限。
3. 用带 `trace_id` 的请求在每层日志中确认停在哪一跳。
4. 区分「连接超时」与「读超时」：前者是网络/端口，后者是处理慢。

## 7. keepalive 与连接复用

- 网关到上游的 idle timeout 大于上游的 keepalive timeout 时，网关会复用已被上游关闭的连接，产生偶发 502。
- 解决方向：让上游 keepalive timeout 大于网关 idle timeout，或网关开启重试与连接健康检查。
- HTTP/2 的多路复用会放大 421 与流控问题，必要时降级到 HTTP/1.1 做对照。

## 快速检查表

- [ ] 先分层：DNS、TCP、TLS、HTTP 哪一层失败
- [ ] 502 是否确认上游存活与端口
- [ ] 504 是否对照了链路最短超时
- [ ] 421 是否核对了 Host 白名单与 SNI
- [ ] 是否排除了代理环境变量干扰
- [ ] 是否确认 keepalive 超时匹配
- [ ] 是否用域名而非 IP 验证 TLS
