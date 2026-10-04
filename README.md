# LanBridge · 局域网网页公网管理平台

LanBridge 是运行在 Windows 的 Cloudflare 管理平台，用于将局域网网页发布到公网，并统一管理域名映射、人类验证、访问策略和连接器。支持网页管理、API 与 CLI 操作，以及配置预览、写后核验、凭据加密和操作审计。

## 打开平台

当前电脑已安装项目独立虚拟环境和 cloudflared。双击项目目录中的 `start.cmd`，启动后自动打开管理页面 **http://127.0.0.1:8890**。首次在页面设置管理员用户名和至少 12 位密码。也可以在 PowerShell 中运行 `start.ps1`。

```powershell
Set-Location <本项目目录>
.\start.ps1
```

管理台仅监听 `127.0.0.1:8890`，公网访问网关仅监听 `127.0.0.1:8891`，由 cloudflared 在本机连接。关闭平台会停止它自己启动的连接器。未配置系统服务或登录自启。Windows DPAPI 使用当前用户身份；请在当前用户终端运行，受限沙箱身份无法解密该用户的保险库。

**退出方式**：管理页面左侧选择“退出 LanBridge”，确认后停止管理台、访问网关和本平台启动的连接器；配置、凭据和云端资源保留。再次双击 `start.cmd` 可启动平台，连接器仍需按需要启动。“退出管理员”仅退出登录，服务和公网转发继续运行。终端启动时也可按 `Ctrl+C` 正常停止。关闭浏览器标签页不会停止平台。

新电脑需要 Python 3.12+，先运行 `install.ps1 -PythonPath <python.exe 完整路径>`。管理界面的 cloudflared 路径可留空：点击字段旁的“自动检测 / 下载”，先查系统 PATH，再查项目 `bin/`，找不到则从 Cloudflare 官方 GitHub 发布下载到 `bin/cloudflared.exe`。使用发布 SHA-256 和大小校验后检测版本、保存路径；失败可重试或手动指定路径。Windows 启动连接器时也会自动准备，已有明确路径不会被静默替换。二进制不提交到仓库。

## 接入第一个网站

1. 在“账户与配置”填写 Account ID、Zone ID、Zone 名称，例如 `example.com`。Zone 必须在该 Cloudflare 账户中，且状态为 Active。网站使用所配置域名下的子域名；已有冲突 DNS 时，平台拒绝覆盖。
2. 在本机凭据表单填写受指定账户和 Zone 限制的 API Token。不要把令牌发到聊天、写入源代码或放在命令参数中。
3. 添加网站，例如 `app.example.com` → `http://192.168.1.20:3000`。这台 Windows 电脑必须能访问该源站，源站 URL 不带路径。默认开启人类验证；可组合访问口令、国家/IP 白名单和限流。口令至少 12 位。

添加网站前必须已保存 Account ID、Zone ID、Zone 名称和写入 API Token；缺少任一项时，“添加网站”按钮禁用；各管理页面持续显示缺少的字段，并提供“前往账户与配置”入口，不会打开新增表单。点击可用按钮后先刷新配置状态，再打开表单。API/CLI 同样拒绝创建。此检查表示必填配置已保存，实际令牌权限及 Zone Active 状态在云端操作时核验。Tunnel 和 Turnstile 可在登记网站后创建，不作为新增网站的前置条件。已有网站仍允许编辑和停用。
4. 如启用人类验证，在“访问策略”点击“自动配置人类验证”。旧授权缺少权限时打开官方浏览器授权，完成后自动创建 Managed Turnstile Widget 并加密保存 Secret。手工配置的 Widget 需自行在 Cloudflare 管理允许域名；本平台不接管手工 Widget。新增人类验证域名后再次同步。
5. 在“连接器”创建Tunnel。平台在自定义名称后加随机标识，记录其 ID，读取连接令牌并加密保存。创建成功但令牌读取失败时，再点击会重试获取令牌。创建请求结果未知时，按唯一名称查找恢复；找不到或结果不唯一时停止，不盲目重复创建。
6. 点击“预览 Cloudflare 配置”，检查 ingress 和将创建的 DNS；点击“应用此预览并核验”。平台重新读取远端配置及 DNS，对比 revision 后才写入。只管理平台登记的网站路由，保留其他 hostname 路由和配置字段，末尾为 `http_status:404`。
7. 启动连接器，再点击“通过 API 核查”。使用外部网络打开公网域名，确认验证页面、源站网页、登录和 WebSocket 均正常。API 核查反映 Tunnel 边缘连接，不能替代实际公网端到端验证。

## API 令牌权限

| 凭据 | 权限与范围 |
| --- | --- |
| 只读 API Token（可选） | 指定账户 Cloudflare Tunnel Read；指定 Zone 的 Zone Read、DNS Read |
| 写入 API Token | 指定账户 Cloudflare Tunnel Edit / Cloudflare One Connector: cloudflared Write；指定 Zone 的 DNS Edit、Zone Read；如自动管理 Widget，再加指定账户 Turnstile Edit |
| Tunnel 连接令牌 | 平台创建 Tunnel 后通过管理 API 获取，独立用于 cloudflared，不能替代管理 API Token |
| Turnstile Site Key / Secret | Site Key 为公开配置；Secret 只用于后端 Siteverify 校验 |

没有单独只读 Token 时，读取使用写入 Token，其也需拥有读取相关资源的权限。获取 Tunnel 连接令牌、读取 Widget 配置始终使用写入 Token；启用人类验证时预览也会核对 Widget 允许域名，因此写入 Token 需要 Turnstile Edit。Cloudflare 控制台的权限显示名称可能变化，请按下列官方接口的权限表选择对应权限。

## 访问网关与保护

```text
访客 HTTPS → Cloudflare → Tunnel → 127.0.0.1:8891
                                       ↓ 国家/IP/限流
                                       ↓ Turnstile + 可选口令
                                       ↓ 登记的局域网网页
管理员 → 127.0.0.1:8890 → 共用业务核心 → Cloudflare API
CLI run.py ────────────────────────┘
```

- 网页无需嵌入 Turnstile。网关显示独立验证页，后端调用 Siteverify，检查 success、hostname 和 action；失败、缺少密钥或服务不可用时拒绝放行。
- 通过验证后设置 Secure、HttpOnly、host-only Cookie，绑定网站 ID、域名、IP、策略版本及过期时间；验证 Token 本身由 Cloudflare 一次性校验。
- API 写入或没有网页 Accept 头的未验证请求返回 401；WebSocket 在握手前执行相同访问控制。修改策略或停用网站会拒绝新请求，并在约 2 秒内关闭既有 WebSocket。已经开始的普通 HTTP 响应不强制中断。
- 网关不提供管理 API；其 `/.lanbridge/` 路径只用于验证。平台管理 Cookie 和验证 Cookie 不会转发到源站；源站 Cookie 的 Domain 移除，使其作用于当前公网域名。
- 国家和客户端 IP 只信任 loopback 连接器提供的 Cloudflare 请求头。两端监听本机，Uvicorn 禁用通用转发头信任。持有本机用户权限的进程属于信任边界。
- 国家/IP 白名单、每来源请求限流在本地网关执行，不是 Cloudflare WAF 规则。请求计数保存在内存，最多 10000 个计数桶，重启清空；不会自动变成永久封禁。
- 管理员密码使用 scrypt；管理员会话保存在 SQLite，8 小时过期，支持注销和密码修改后全部失效。管理 API 同时检查 Host、Origin 与 CSRF。
- 凭据在数据库中使用 Fernet 加密，主密钥由 Windows DPAPI 绑定当前用户，数据目录收紧 ACL。Unix 使用受限本机密钥文件，Windows 为本项目主要运行目标。

## API 和 CLI

网页/API/CLI 使用同一份 `lanbridge/service.py` 业务核心。管理 API 路径：

| 方法 / 路径 | 操作 |
| --- | --- |
| GET `/api/bootstrap` | 初始化和登录状态 |
| POST `/api/setup`, `/api/login`, `/api/logout`, `/api/password` | 管理员账户与会话 |
| POST `/api/shutdown` | 已登录管理员关闭本机平台，需要 Origin 与 CSRF 校验 |
| GET `/api/state` | 配置、网站、凭据存在状态、连接器、上次边缘核验、审计 |
| POST `/api/settings`, `/api/credentials` | 设置与加密保存凭据 |
| POST `/api/sites` | 新建/编辑网站；包含 id 时编辑，停用用 enabled=false |
| POST `/api/sites/{id}/probe` | 局域网源站探测 |
| POST `/api/cloudflare/create-tunnel` | 创建Tunnel / 重试读取连接令牌 |
| POST `/api/cloudflare/turnstile` | 创建/更新平台拥有的 Widget 域名 |
| POST `/api/cloudflare/preview` | 读取并预览配置，返回 revision |
| POST `/api/cloudflare/apply` | 提交 `{ "revision": "预览返回值" }`，写后核验 |
| POST `/api/cloudflare/check` | 查询实际 Tunnel 边缘状态 |
| POST `/api/connector/start`, `/api/connector/stop` | 管理本平台的连接器进程 |

除初始化与登录外，管理 API 需要 Cookie 会话。所有 POST 需要 `Origin: http://127.0.0.1:8890`，已登录的 POST 还需要 `/api/login` 返回的 `X-CSRF-Token`。POST 发送 JSON，无字段的操作发送 `{}`。未提供任意 Cloudflare API 透传接口。

```powershell
.\.venv\Scripts\python.exe run.py capabilities
.\.venv\Scripts\python.exe run.py status
.\.venv\Scripts\python.exe run.py configure config.example.json
.\.venv\Scripts\python.exe run.py configure-secret cf_write_token
.\.venv\Scripts\python.exe run.py create-tunnel
.\.venv\Scripts\python.exe run.py save-site my-site.json
.\.venv\Scripts\python.exe run.py turnstile
.\.venv\Scripts\python.exe run.py preview
.\.venv\Scripts\python.exe run.py apply --revision <预览版本>
.\.venv\Scripts\python.exe run.py serve
```

配置示例需先填入自己的账户、Zone 和域名。所有密钥通过隐藏输入。CLI 输出 `lanbridge-result/v1` JSON，失败退出码 1。运行中的平台持有独占锁，此时从管理台/API 修改；CLI `status` 可只读，其他 CLI 操作需先停止平台。使用另一个 `--data-dir` 可以独立配置另一个账户和端口。

网站 JSON 示例：

```json
{
  "name": "内网工作台",
  "hostname": "app.example.com",
  "origin": "http://192.168.1.20:3000",
  "human_check": true,
  "passcode_required": false,
  "enabled": true,
  "allowed_countries": ["CN", "HK"],
  "allowed_ips": [],
  "requests_per_minute": 180,
  "session_minutes": 60
}
```

## 变更、失败和运行限制

- Cloudflare 多步骤写入不是原子事务；检查与 PUT 之间仍存在外部并发修改窗口。避免同一 Tunnel 的多人同时维护。API 超时可能已有部分写入，平台记录 `publish_incomplete`，不会盲目删除资源或宣称成功；重新预览可核对并继续。DNS 已创建但 ingress 未发布的情况也按此流程处理。
- 修改本机策略立即影响网关；新增、启用或停用路由仍需预览并应用。停用后本机立即拒绝请求，应用后移除该 hostname ingress；保留 DNS 注册，不自动删除云端资源。
- 固定域名更新不需要重启 cloudflared。连接器异常退出会显示退出码，当前版本需手动再次启动；不安装系统看护、自启动或远程 Agent。
- 支持 HTTP/HTTPS 全路径、查询参数、二进制响应、常见 Cookie/源站绝对重定向，以及 WebSocket 文本与二进制。请求体上限 32 MiB，HTTP 读取超时 120 秒，WebSocket 消息上限 8 MiB。
- 源站必须解析到 RFC1918、本机或 ULA IPv6 地址，连接前再解析并固定 IP，拒绝公网/链路本地/元数据地址和本平台端口。HTTPS 保持证书校验，源站自签名证书需要在系统中受信任。
- “任意局域网网页”并不保证第三方网页无需调整即可完全兼容：写死局域网 URL、跨域 API、OAuth callback、CSP、Cookie 或 Host 校验的应用，需要把其公开 URL 配置为公网域名；平台不重写 HTML/JavaScript。非 HTTP 服务不在当前范围。
- 无真正公网账户实测时，不声称公网发布已完成。配置 Cloudflare 后，应使用外部网络验证真实 Turnstile、应用登录、接口写入和 WebSocket。

## 测试与文件

运行 `.\.venv\Scripts\python.exe -m pytest -q`。测试覆盖真实 Windows DPAPI、鉴权与 CSRF、Turnstile 校验、Cookie 隔离、策略绑定、限流、局域网源站约束、Cloudflare 配置漂移及冲突、部分失败审计、实际本机 HTTP/WebSocket 转发。Cloudflare/Turnstile 云端 API 在自动测试中使用受控替身，未使用用户的云端凭据。

源码：`lanbridge/`；界面：`ui/`；CLI：`run.py`；本机数据：`data/`；测试：`tests/`。`data/` 和 `.venv/` 被 Git 忽略。DPAPI 保险库不能仅复制到另一个 Windows 用户下使用；迁移账户时重新配置凭据。

## 官方接口依据

- [Cloudflare API 创建远程 Tunnel、配置 ingress 和 DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/)
- [Tunnel 连接令牌](https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/)
- [cloudflared 运行参数](https://developers.cloudflare.com/tunnel/reference/run-parameters/)
- [Turnstile Widget API](https://developers.cloudflare.com/turnstile/get-started/widget-management/api/)
- [Turnstile 服务端验证](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
- [cloudflared 源码及许可证](https://github.com/cloudflare/cloudflared)


## 四种调用入口

- **CLI**：`run.py capabilities`、`status`、`ensure-connector` 等，共用业务核心。
- **API**：管理台的本机 `/api/`，使用管理员 Cookie、Origin 和 CSRF 校验。`POST /api/connector/ensure` 的 JSON 为 `{}`（使用已保存路径）或 `{"path":""}`（自动检测）。
- **MCP**：`mcp_server.py` 提供 stdio 工具；连接正在运行的本机 API。支持状态、网站保存、连接器准备/启停、Tunnel 创建、Turnstile 同步和预览/发布。
- **SKILL**：项目的 `skills/lanbridge/SKILL.md` 提供调用流程，可按需复制到代理支持的技能目录；不自动修改你的全局代理配置。

MCP 客户端配置中的 command 指向本项目 `.venv/Scripts/python.exe`，args 是 `mcp_server.py` 的绝对路径。通过客户端安全环境配置提供 `LANBRIDGE_ADMIN_USERNAME` 和 `LANBRIDGE_ADMIN_PASSWORD`；可选 `LANBRIDGE_ADMIN_URL` 默认 `http://127.0.0.1:8890`。不要将真实密码放入仓库内的配置文件。桥接器只连接 loopback，不读取本机保险库，不通过 MCP 参数接收 Cloudflare 令牌或访客密码。普通网站保存支持 MCP；需要新建访客口令时在管理界面完成。平台需要先启动，MCP 桥接器不自行创建管理员或启动公网连接。

官方安装来源：[Cloudflare cloudflared 下载说明](https://developers.cloudflare.com/tunnel/downloads/)。

## 自动配置业务 API Token

账户与配置页提供“自动配置 API Token”。首次打开 Cloudflare 用户 API Tokens 页面，使用 **Create Additional Tokens** 模板创建具有 **API Tokens Write** 权限的授权令牌。它不同于 Account API Tokens Write；当前入口使用用户令牌 API。

输入授权令牌后，平台从 API 获取权限组 ID，自动创建限定当前 Account ID 的 Tunnel Write 和限定当前 Zone ID 的 DNS Write、Zone Read 业务令牌；可选择 Turnstile Write。业务令牌加密保存，不返回页面。授权令牌默认仅用于本次请求，勾选记住后独立加密保存，也可从界面移除本机副本。授权令牌可管理用户 API 令牌，应妥善保管。

写入和只读令牌分别管理。只读令牌自动创建 Tunnel Read、DNS Read 和 Zone Read 权限，限定当前账户及 Zone，不改变写入令牌。再次点击创建入口会更新该类型已经托管的令牌。新建会替换本机使用的凭据，旧令牌不会从 Cloudflare 删除。

“修复当前写入/只读令牌权限”用于已经保存的用户令牌：先用该令牌验证 ID，再由授权令牌读取远端详情，新增当前账户及 Zone 所需权限，保留原有权限（包括拒绝策略）、名称、有效期、生效时间、IP 条件和状态，不更换令牌值。它不会移除原有写权限来强制变成纯只读。无法验证、账户令牌、其他用户令牌或无法读取权限时停止操作；可改用创建新令牌。有效期、IP 限制或原有拒绝策略造成的失败不会自动解除，更新后请重试业务操作核验。

手动替换或移除凭据会解除对应托管。两种令牌分别记录未知创建结果并阻止重复 POST，页面展示唯一名称供核对；可通过手动配置接入已有令牌。移除本机授权不撤销 Cloudflare 中的令牌。

- CLI：`run.py provision-token`，通过隐藏提示输入授权；`--target read` 配置只读令牌，默认 write；`--repair-existing` 修复对应的当前令牌；`--remember` 保存授权，`--without-turnstile` 不授予 Turnstile。CLI 写操作要求先停止管理服务。
- API：`POST /api/cloudflare/provision-token`，需要管理员会话、Origin 和 CSRF；字段 `authority`、`remember`、`human_check`、`target`（write/read）、`repair_existing`（默认 false）。`POST /api/cloudflare/forget-token-authority` 移除本机授权。
- MCP：`lanbridge_provision_token`，仅使用管理台已加密保存的授权，可传 `human_check`、`target`、`repair_existing`，不接受令牌文本。

参考：https://developers.cloudflare.com/fundamentals/api/how-to/create-via-api/

自动创建新令牌入口始终新建并加密保存，即使已有托管令牌；成功后切换本机凭据，旧令牌不会删除。API/MCP 使用 `force_new: true`，CLI 使用 `--force-new`；不可与 `repair_existing` 同时使用。创建结果未知时仍禁止重复创建。

## 浏览器授权接入 Cloudflare

账户与配置 → 浏览器授权并自动配置。官方 `cf` CLI 1.0.0-beta.12 使用 PKCE 本机回调；用户在浏览器确认一次 Tunnel Write、DNS Write、Zone Read、Turnstile 和账户读取权限。平台直接使用 OAuth 授权访问 API，不创建子令牌，不需要 API Tokens Write 或 Account API Token Provisioning。授权后先核对实际范围、域名名称、账户归属及 Active 状态，再替换本机凭据；失败保留原凭据。

CLI：`run.py serve --authorize-cloudflare` 在启动平台后发起同一浏览器授权流程，进度及取消操作仍由管理界面提供。

授权和刷新凭据通过 Windows DPAPI 加密保存。每次 API 操作前检查有效期，接近到期时通过官方 CLI 自动刷新。刷新使用独立的受保护临时目录，结束删除临时文件；不影响用户其他 CLI 登录，不保存账户密码，秘密不返回管理页面或日志。撤销授权、资源权限变化或网络异常可能需要重新授权。

Node.js 22.18+ 必需；首次使用可通过 npm/pnpm 从官方 npm 源安装 CLI 到忽略的 bin/cf-runtime。官方本机回调等待约 2 分钟，但无需等到超时：等待期间可点击“取消授权”立即结束请求，或“重新打开授权页”结束旧请求并发起新授权。旧页面随即失效；取消保留原凭据。API 对应 `POST /api/cloudflare/browser-authorize-cancel` 和 `POST /api/cloudflare/browser-authorize-restart`，同样要求管理员会话、Origin 和 CSRF。授权期间修改账户、域名或凭据会停止本次配置。

浏览器授权包含 `challenge-widgets.write`。启用人类验证的网站在授权完成后自动创建或同步专属 Widget，原子保存 Site Key 与加密 Secret Key。已有旧授权在点击“自动配置人类验证”后补充一次浏览器授权，随后自动完成配置。创建结果未知时按专属名称核对并恢复，禁止盲目重复创建；不改动其他项目的 Widget。手动更换写入令牌会清除本机 OAuth 凭据，切回令牌模式。

API：`POST /api/cloudflare/browser-authorize`（管理员会话、Origin、CSRF），后台执行；`GET /api/state` 的 browser_auth 返回进度，不含秘密。参考 [官方 CLI 授权说明](https://developers.cloudflare.com/cf/get-started/)。
