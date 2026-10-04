# LanBridge 验证记录

核对日期：2026-10-04（Asia/Shanghai）。对应功能代码：本机提交 `95ba136`。

本文件记录当前版本的验证范围。早期迭代的测试计数、旧界面入口及已被替换的令牌转授权流程已移除，避免将历史结果误认为当前行为。

## 自动验证

- Python 完整回归：96 项通过。覆盖管理员鉴权、Host/Origin/CSRF、本机授权登录、DPAPI 凭据、运行锁、正常关闭与端口释放、网站保存、Cloudflare 配置预览与漂移检测、DNS 冲突、未知创建结果恢复及令牌管理。
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
