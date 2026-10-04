# LanBridge 验证记录

核对日期：2026-10-04（Asia/Shanghai）。包含响应完整性修复及此前的平台功能。

本文件记录当前版本的验证范围。早期迭代的测试计数、旧界面入口及已被替换的令牌转授权流程已移除，避免将历史结果误认为当前行为。

## 自动验证

- Python 完整回归：102 项通过。覆盖管理员鉴权、Host/Origin/CSRF、本机授权登录、DPAPI 凭据、运行锁、正常关闭与端口释放、网站保存、Cloudflare 配置预览与漂移检测、DNS 冲突、未知创建结果恢复、令牌管理及大响应完整性。
- HTTP/WebSocket 集成：实际临时本机源站，覆盖路径和查询参数、二进制响应、Cookie 隔离、重定向、源站地址切换、HTTP/1.0 源站 GET/POST、请求体限制及访问控制。
- 暂停/恢复：验证暂停后 HTTP 返回 503、WebSocket 握手拒绝，其他网站继续转发；恢复后原源站可访问、旧验证会话失效。编辑其他字段保持暂停，DNS/发布记录保留；暂停不依赖 Cloudflare 写入凭据。鉴权、CSRF、不存在的网站及无效状态参数均被拒绝。
- 前端：31 项导航/状态测试及 3 项验证页面流程检查通过。Node 测试运行器显示 32 个条目，其中验证流程文件作为一个条目计数。
- JavaScript 语法检查及 `git diff --check` 通过。
- 测试存在一条 Starlette TestClient/httpx 弃用警告，未导致断言失败。

Cloudflare、OAuth 和 Turnstile 云端接口在自动测试中使用受控替身，真实转发源站为测试启动的临时本机服务。上述测试不使用正式云端凭据，也不修改正式 Cloudflare 资源。

## 页面验证

使用隔离的模拟 API 在浏览器中检查：

- 创建隧道、未知创建结果恢复、缺少连接令牌、已配置及运行中五种状态；按钮含义、禁用状态和当前导航一致。
- “网站管理”的暂停/恢复按钮及“已暂停”状态能随响应切换。
- 1440px 桌面与 390px 窄屏下页面无横向溢出，检查过程中没有 JavaScript 页面错误。

模拟页面验证不等同于真实公网浏览器端到端验证。部署时管理服务已重启，并恢复连接器此前的运行状态。

## 文档与接口核对

README、项目 SKILL 和本记录统一使用 `/admin`、`/client`、网站管理、Cloudflare 连接器和操作日志名称。文档区分临时暂停与取消启用，说明自动 Turnstile 配置、OAuth 直接接入、独立 API Tokens Write 管理及 force_new 行为。CLI 命令、管理 API 路径、MCP 工具与实际源码核对。

## EncoderPLL 响应完整性

旧生产日志没有路径或请求 ID，不能将历史 h11 异常逐条关联到共享 JSON，也不能证明历史源站为何提前断流。隔离的真实 TCP/Uvicorn 测试中，源站声明 5,307,218 字节、只发送 1,048,576 字节后断开：旧 BaseHTTPMiddleware 响应中继复现了 `Too little data for declared Content-Length`，掩盖上游断流异常。修复使用纯 ASGI 访问检查、明确的响应资源生命周期，并在发送已声明长度的响应前核验完整原始字节；该场景现在返回完整 502，关联日志记录 RemoteProtocolError 与实际读取量。

新增测试使用真实 HTTP/1.1 源站及 Uvicorn，覆盖 5,307,218 字节 JSON 的快速与慢速消费者、gzip 原始字节和解压后的 JSON、HEAD、源站截断、76,800,008 字节 CSV（300,000 条记录），以及消费者在源站接收期间和下游发送期间断开后的连接、临时文件释放。访问控制保持开启；隔离测试使用测试服务自己的验证会话。

正式公网验收使用已有真人验证会话，在浏览器中同源 fetch、读取完整响应并计算 SHA-256，未关闭验证或绕过认证。原始共享 ID 为 `2f0b73453bb04728b8feb495a3ba4a78`：

| 结果 | 源站字节 | 网关读取 / 发送字节 | 公网浏览器解码后字节 | 请求 ID |
| --- | ---: | ---: | ---: | --- |
| result.json | 5,307,218 | 5,307,218 / 5,307,218 | 5,307,218 | 9cf7b66f0d1a54fd |
| output.csv | 76,796,831 | 76,796,831 / 76,796,831 | 76,796,831 | c567cfb60e794eb5 |

两段网关传输均 complete=true、无上游或发送异常。JSON 完整解析，CSV 包含标题及 300,000 条数据记录；公网与源站哈希一致：

- JSON：`c1960b0cc73e7d47c1401bcabebf78c0481d84c312a38082d8c721be5523fbfc`
- CSV：`c93337bc135d4b69b804517255581bbae385349656bcb81a40d896e59cfc8ed1`

网关保留源站 Content-Length 和原始编码。公网 JSON 经 Cloudflare 转为 zstd、长度头未提供；浏览器报告的上述长度是解压后字节数，不等于公网压缩线缆字节。公网 CSV 未压缩，Content-Length 与实际读取量相等。临时验收页受正常网站策略保护，验收后通过正式启动入口重启移除，不属于发布源码。

恢复正式启动入口和连接器后，再访问 `https://mc.yuboyi.me/client`：页面自动加载共享快照，显示 300,000 个样本及分析统计，导出按钮可用；没有点击额外加载按钮，浏览器未报告 JavaScript 错误或警告。该页面当前自动加载的是最新共享快照，原始 ID 的 JSON 和 CSV 则由上述独立完整性验收覆盖。

## 验证边界

- 不以“已保存配置”代替云端权限核验；不以 Tunnel 边缘连接代替公网端到端验证。
- 本记录不推断正式账户当前权限、DNS 缓存、真实访客国家或具体源站应用状态。发布后仍需从外部网络核验域名解析、Turnstile、源站登录、API 写入及 WebSocket。
- 网页写死内网 URL、Host/CORS/CSP、OAuth 回调或 Cookie 限制时，可能需要调整源站应用配置；网关不重写 HTML/JavaScript。

## 复现

在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test_navigation.js tests/test_gate.js
node --check ui/app.js
node --check ui/client.js
node --check ui/local-login.js
git diff --check
```

测试临时目录及浏览器截图不属于项目源码，不上传到仓库。数据、凭据、日志、虚拟环境和 bin 工具目录保持忽略。
