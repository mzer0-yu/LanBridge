# LanBridge 使用指南

[返回项目首页](../README.md)

本地新增功能：[阿里云独立域名自动接入](domain-onboarding.md)。先预览 DNS，用户确认后迁移与切换，激活后自动加入 LanBridge。

## 打开平台

安装项目依赖后，双击项目目录中的 `start.cmd`，启动器只显示管理台端口（默认 8890），可以修改后启动，成功后自动保存，按“启动后打开管理台”选项决定是否打开浏览器。转发网关端口（默认 8891）在管理台的“本机与安全”中修改；网关冲突不会阻止进入管理台。首次在页面设置管理员用户名和至少 12 位密码。也可以在 PowerShell 中运行 `start.ps1`。

**转发列表页**：`http://127.0.0.1:8890/client` 无需登录，展示已启用网站的公网地址、局域网源站和发布状态，每 15 秒刷新。根地址跳转到此页面。只读接口为 `GET /api/client/routes`，不提供凭据或管理操作；默认仅接受本机访问；显式添加 LanBridge 公网入口后，也可从入口域名访问。管理 API 路径和 MCP 的服务基地址保持不变。

管理员可在“账户与配置 → 公网访问”选择“启用转发列表页面 /client”，点击保存后立即生效，重启后保留选择。默认启用；关闭后，所有 LanBridge 公网入口的 `/client` 页面和 `/api/client/routes` 接口均不可访问，公网根地址转到管理台。本机转发列表、管理台及其他网站转发不受影响。临时管理 Token 不能修改此开关。

已有管理员账户时，登录页提供“在其他浏览器中确认登录”。展开后自动显示确认码，可选择系统默认浏览器，或本机已安装的 Chrome、Edge；切换浏览器会生成新的确认码，此时页面保持原位。记住确认码后，点击“前往 Chrome 确认登录”（按钮随所选浏览器变化）打开确认页；选择会在当前浏览器记住。在确认页登录管理员账户、核对两边的确认码并点击“允许登录”，发起请求的浏览器会自动登录。Chrome 已登录 LanBridge 管理员账户时无需再次输入密码；尚未登录时可使用 Chrome 保存的密码。

本机授权请求有效期为 5 分钟，绑定发起浏览器的 HttpOnly Cookie，只能领取一次。可随时取消；拒绝、超时、重新发起、平台重启或管理员密码修改后，旧请求失效。授权页面必须由已登录的管理员确认，不能仅凭访问本机地址取得权限。

```powershell
Set-Location <本项目目录>
.\start.ps1
```

本机管理服务仅监听 `127.0.0.1:8890`，网站转发网关仅监听 `127.0.0.1:8891`，由 cloudflared 在本机连接。关闭平台会停止它自己启动的连接器。未配置系统服务或登录自启。Windows DPAPI 使用当前用户身份；请在当前用户终端运行，受限沙箱身份无法解密该用户的保险库。

**启动端口冲突**：无需进入管理台，重新打开启动器，修改被占用的端口即可。端口范围为 1024–65535，两个端口必须不同。管理台端口冲突会阻止启动；网关端口冲突时管理台仍启动，在“本机与安全”中修改网关端口并保存可立即恢复，也可释放占用后点击“重试启动网关”。网关正常运行时修改端口仍下次启动生效；修改网关端口后，若已有 Tunnel，需在“网站转发”重试发布以同步云端路由。启动失败不会保存新端口，也不会停止其他占用端口的程序。

无需对话框时可运行 `start.ps1 -NoDialog`；命令行也支持 `.venv\Scripts\python.exe run.py serve --admin-port 9000 --gateway-port 9001 --open-browser`。

**后台运行**：默认启动器显示启动进度和原位错误，管理台就绪后按“启动后打开管理台”选项决定是否打开浏览器，并关闭启动器。服务在后台运行，不保留 CMD；`start.cmd` 可能短暂闪过终端窗口，但不常驻。需要前台终端诊断时使用 `start.ps1 -NoDialog`。

**管理台端口与重启**：在“账户与配置 → 本机与安全”中分别选择“管理台端口”或“网关端口”。管理台端口保存为待生效设置，当前地址继续可用；下次启动或点击“重启 LanBridge”后应用。填回当前管理端口并保存可取消待生效修改。启动器会默认使用待生效管理端口，遇到占用仍可在启动前修改。

重启仅允许本机已登录的管理员执行，临时管理 Token 和公网入口均不可执行。确认后会短暂中断转发，正常关闭旧实例，再启动新进程；原先运行的连接器自动恢复，原先未启动的连接器保持停止。管理台就绪后自动打开浏览器，登录会话保留。重启前若新管理端口已被占用，会拒绝请求并保留当前服务；启动时遇到竞争占用则尝试恢复旧管理端口并提示，待生效设置仍保留。若两个端口均不可用或启动进程失败，重新打开启动器选择可用管理端口。修改网关端口后仍需同步云端路由。

旧进程首次加载这项功能，需要先“退出 LanBridge”，再通过启动器重新启动一次；页面会提示旧版本，新端口设置和重启按钮暂不可用。之后即可在管理台操作。关闭启动器仅关闭窗口，不会停止服务。

**退出方式**：管理页面左侧选择“退出 LanBridge”，确认后停止管理台、访问网关和本平台启动的连接器；配置、凭据和云端资源保留。再次双击 `start.cmd` 可启动平台，连接器仍需按需要启动。“退出管理员”仅退出登录，服务和公网转发继续运行。终端启动时也可按 `Ctrl+C` 正常停止。关闭浏览器标签页不会停止平台。

新电脑需要 Python 3.12+，先运行 `install.ps1 -PythonPath <python.exe 完整路径>`。管理界面的 cloudflared 路径可留空：点击字段旁的“自动检测 / 下载”，先查系统 PATH，再查项目 `bin/`，找不到则从 Cloudflare 官方 GitHub 发布下载到 `bin/cloudflared.exe`。使用发布 SHA-256 和大小校验后检测版本、保存路径；失败可重试或手动指定路径。Windows 启动连接器时也会自动准备，已有明确路径不会被静默替换。二进制不提交到仓库。

## 接入第一个网站

1. 确认域名已接入 Cloudflare 且状态为 Active。公网入口可填写已接入的根域名（如 `example.com`）或其子域名（如 `app.example.com`），所属域名必须匹配；无需提前复制账户或域名 ID。
2. 在“账户与配置”点击“浏览器授权并自动配置”，在 Cloudflare 浏览器页面确认授权。唯一可用域名自动配置，多个域名时在管理台选择；账户 ID、Zone ID 和名称自动填写。平台核验权限与资源后保存 OAuth 凭据，无需手动复制 API Token。也可选择“通过 API Token 接入”，粘贴已有写入 Token 后读取可访问的账户与域名，选择默认域名并接入；Account ID、Zone ID 和名称自动保存。读取失败时可手动配置，详见下文。
3. 在“网站转发”添加网站，例如 `app.example.com` → `http://192.168.1.20:3000`。这台电脑必须能访问源站，URL 不带路径。网页表单默认开启人类验证，允许国家预填 CN、HK、US、JP；可修改或留空不限。CLI/API 不传 allowed_countries 时默认不限。可组合至少 12 位访问口令、IP 白名单和限流。
4. 保存启用人类验证的网站时，平台自动创建或更新专属 Managed Turnstile Widget，并加密保存 Secret，无需再点击同步域名。配置失败则保留原网站配置。关闭人类验证在保存后立即生效。检测到手工配置的 Widget 时，自动配置会停止并提示；本平台不会接管它的允许域名，需先自行处理或改用平台托管 Widget。
5. 在“Cloudflare 授权与连接”中完成账户授权。浏览器授权成功后自动配置隧道并加密保存连接令牌：复用本机已配置的隧道；没有配置时创建专用隧道，不接管账户内其他隧道。自动配置失败时保留授权，在授权区域点击“继续自动配置”，无需再次授权；创建结果不确定时先核对原任务，避免重复创建。同一区域下方的“隧道连接”默认折叠，需要重新获取令牌时再展开。手动 API Token 接入仍使用现有创建/恢复入口。默认 Tunnel 名称为 LanBridge，创建时追加随机标识。
6. 在管理台保存网站后，新增、启用或停用导致的路由变更会自动检查、发布并核验。修改源站与访问策略即时生效，无需云端发布。自动发布失败时，本机配置仍已保存，网站转发显示失败原因，可在网站转发中重试。CLI 直接保存仍不自动发布；API / MCP 保存与管理台一致。网站转发的“云端发布检查”也可手动检查路由，预览具体变更或核验已有配置。平台对比预览 revision 后写入并核验，保留其他 hostname 路由及配置字段；冲突 DNS 不覆盖。
7. 启动连接器并点击“通过 API 核查”。使用外部网络访问公网域名，验证真实人类验证、源站网页、应用登录、API 和 WebSocket。边缘连接核查不能替代端到端验证。

新增网站前必须已保存 Account ID、Zone ID、Zone 名称及写入凭据；OAuth 接入也满足本机凭据存在检查。缺项时新增按钮禁用，页面提示具体字段。该检查不代表云端权限已核验，实际权限及 Zone Active 状态在云端操作时检查。已有网站仍可编辑、停用或暂停；Tunnel 不要求提前创建。保存开启人类验证的网站需要完成 Widget 配置，失败不会保存网站变更。

## 人类验证与浏览器记忆

启用人类验证的网站首次访问仍需验证。默认记住当前浏览器 1 天，编辑网站的“记住人类验证（天）”可设为 0–30 天。记忆期内同一浏览器再次访问，或手机在 Wi-Fi 与移动网络间切换，不因短会话过期或 IP 改变而重复要求人类验证。记忆期按首次验证计算，访问不会无限续期。

设为 0 时使用原验证会话时长；网站口令始终按会话时长单独核验，即使浏览器的人类验证仍有效也不能跳过口令。IP/国家限制、网站暂停和请求限流持续生效。访问策略、验证配置或浏览器身份变化、Cookie 清除以及记忆期到期时，可能需要重新验证。只修改网站名称或重复保存不使凭证失效。

记住浏览器不代表免除异常检查。每个网站优先按有效验证凭证中的浏览器标识统计，换 IP 仍累计；没有可识别凭证时按来源 IP 统计：一分钟内累计 60 次限流拒绝、五分钟内 5 次实际口令/人类验证失败，或一分钟内探测 8 个不同的已知敏感路径且源站返回 403/404，会撤销当次请求携带的有效验证凭证并限制访问 5 分钟；WebSocket 访客发送超出已有消息/字节预算也会触发。24 小时内再次触发，限制升为 10、15 分钟。普通浏览、一般 404、同一敏感路径的重复请求不会单独触发扫描规则；验证服务故障及过期/重复令牌不计为验证失败。

限制期间返回 429 和 Retry-After，不能通过再次验证跳过等待，正在使用的 WebSocket 也会关闭。被撤销的人类验证凭证换 IP 或重启服务后仍无效；同一浏览器已签发的旧记忆与短会话也失效；按 IP 触发网络级限制时，该来源旧凭证也失效，验证请求若在处理中触发限制，不会签发新凭证。冷却结束后需重新验证取得新凭证。正常访问不续期，也不额外写数据库；统计和撤销记录有容量上限，极端容量耗尽时对相关网站进入短期保护并撤销旧凭证。这些规则覆盖上述可观测异常，不代替源站登录权限或 Cloudflare 边缘防护。

网页/API、写请求和 WebSocket 建连使用网站配置的每分钟额度；GET/HEAD 且路径为已知脚本、样式、图片或字体后缀时使用独立的 4 倍额度。更改 Accept 或 Sec-Fetch-Dest 不能增加额度，写请求不能通过资源后缀获得静态额度；资源分类不代表源站处理一定便宜，原全局速率、并发、字节预算仍生效。同一 IP 在该网站的全部类别保留 8 倍总额度，避免更换凭证无限增加额度。

已验证浏览器触发限制时，其他同 IP 的有效浏览器不会因浏览器级规则一起被撤销；但该 IP 发起新验证会暂时受限，清除 Cookie 也不能立即领取新凭证。网络级或容量保护仍可能影响同 IP/网站的其他访问。签名凭证中的随机标识只用于本网站的限流和撤销，不建立跨网站身份。旧凭证未带标识时沿用 IP 判定，下一次成功验证领取新标识。

访客页面根据 Retry-After 显示剩余等待秒数，期间禁用重试；时间到后允许手动重试，不自动提交或刷新。总览的“访问防护”显示本进程以来的人类验证成功次数、短会话失效后使用人类记忆放行的请求数和异常限制次数；不代表人数，不含逐访客轨迹，重启清零。普通请求只在内存累计，异常处置仍持久保存。统计仅完整管理员可见。

原有网站未保存此字段时使用 1 天默认值，旧浏览器需要再完成一次验证，才能取得新的记忆凭证。已明确保存的记忆期限保留，7 天仍可手动选择；未设置该字段的网站使用新的 1 天默认值。缩短默认值后，旧凭证也按新期限核验，不会等待原七天期限结束。不同浏览器和网站的凭证不互通。“仅异常时验证”仍未实现。

## 暂停、恢复与停用

网站编辑框的“转发协议”可分别勾选 HTTP / HTTPS、WebSocket / WSS，至少选择一项。保存后本机网关立即执行，无需重新发布或重启连接器；现有 WebSocket 随策略版本更新关闭。仅选择 WebSocket 时不转发普通网页或 HTTP API，但访问验证页仍可用。新网站默认两项开启；旧网站未记录该字段时同样保留两项。API/CLI 网站字段为 `protocols: ["http", "websocket"]`；编辑时省略该字段保留原选择，不支持的协议或空列表会被拒绝。

在“网站转发”点击“暂停转发”或“恢复转发”，即时切换本机网关，无需重新发布。暂停保留域名、DNS、Tunnel 路由、源站及访问策略，不影响其他网站。新 HTTP 请求返回 503，WebSocket 握手被拒绝，已有 WebSocket 在约 2 秒的策略检查后关闭；已开始的 HTTP 响应不强制中断。恢复后受保护网站的访客需重新验证。尚未发布的路由仍需先发布，暂停或恢复不会自动发布路由、启动连接器。

“编辑”中的“启用网站”与临时暂停不同：取消启用立即在本机拒绝请求，保存时自动发布会移除云端 ingress，DNS 保留。重新启用后如路由已被移除，保存时自动重新发布。编辑其他字段不会自动取消暂停状态。网站列表和 `/client` 显示“已暂停”；入口页暂停时不提供可点击的访问链接。

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
- 普通网站不开放平台管理 API；只有显式 LanBridge 入口在访问验证后进入远程管理模式。`/.lanbridge/` 路径只用于验证。平台管理 Cookie 和验证 Cookie 不会转发到普通源站；源站 Cookie 的 Domain 移除，使其作用于当前公网域名。
- 国家和客户端 IP 只信任 loopback 连接器提供的 Cloudflare 请求头。两端监听本机，Uvicorn 禁用通用转发头信任。持有本机用户权限的进程属于信任边界。
- 国家/IP 白名单、每来源请求限流在本地网关执行，不是 Cloudflare WAF 规则。请求计数保存在内存，最多 10000 个计数桶，重启清空；不会自动变成永久封禁。
- HTTP 源站响应声明 Content-Length 时，网关先完整接收原始编码字节并核对长度，再发送源站状态与响应头；不完整的响应返回完整的 502，不以删除长度头掩盖截断。前 1 MiB 使用内存，更大的响应暂存到受保护的数据目录，发送结束或断开后关闭并删除临时文件。这会增加首字节等待时间与磁盘占用；没有声明长度的响应继续流式发送。Content-Encoding 与对应的原始字节保持一致。
- 响应包含 X-LanBridge-Request-ID；大响应、发送异常及未完成传输在运行错误日志中记录请求路径、上游读取字节、下游发送字节和完成状态。日志不记录查询参数、Cookie 或响应正文；发送完成只说明网关完成发送，公网完整性仍需在客户端核验。
- 管理员密码使用 scrypt；管理员会话保存在 SQLite，8 小时过期，支持注销和密码修改后全部失效。管理 API 同时检查 Host、Origin 与 CSRF。
- 凭据在数据库中使用 Fernet 加密，主密钥由 Windows DPAPI 绑定当前用户，数据目录收紧 ACL。Unix 使用受限本机密钥文件，Windows 为本项目主要运行目标。

## API 和 CLI

网页/API/CLI 使用同一份 `lanbridge/service.py` 业务核心。管理 API 路径：

| 方法 / 路径 | 操作 |
| --- | --- |
| GET `/api/bootstrap` | 初始化和登录状态 |
| POST `/api/setup`, `/api/login`, `/api/logout`, `/api/password` | 管理员账户与会话 |
| POST `/api/shutdown` | 已登录管理员关闭本机平台，需要 Origin 与 CSRF 校验 |
| GET `/api/state` | 配置、网站、凭据存在状态、连接器、上次边缘核验、操作日志 |
| POST `/api/settings`, `/api/credentials` | 设置与加密保存凭据 |
| POST `/api/sites` | 新建/编辑网站；包含 id 时编辑，停用用 enabled=false |
| POST `/api/sites/{id}/pause` | 临时暂停/恢复，JSON 为 `{"paused":true}` 或 `{"paused":false}`；仅适用于已启用网站 |
| POST `/api/sites/{id}/probe` | 局域网源站探测 |
| POST `/api/cloudflare/create-tunnel` | 创建、核对恢复隧道或重新读取连接令牌 |
| POST `/api/cloudflare/browser-authorize-setup` | 本机继续已保存授权的自动隧道配置，不重新浏览器授权 |
| POST `/api/cloudflare/turnstile` | 创建/更新平台拥有的 Widget 域名 |
| POST `/api/cloudflare/preview` | 读取并预览配置，返回 revision |
| POST `/api/cloudflare/apply` | 提交 `{ "revision": "预览返回值" }`，写后核验 |
| POST `/api/cloudflare/check` | 查询实际 Tunnel 边缘状态 |
| POST `/api/connector/ensure` | 检测或下载 cloudflared，`{}` 使用保存路径，`{"path":""}` 自动检测 |
| POST `/api/connector/start`, `/api/connector/stop` | 管理本平台的连接器进程 |

公共只读接口 `/api/bootstrap`、`/api/client/routes`、`/api/local-login/browsers`，以及初始化、登录和本机授权登录的发起/轮询/取消接口不要求管理员会话。其余管理接口需要管理员 Cookie 会话；本机授权登录的领取还须匹配发起浏览器 Cookie，确认授权须已登录管理员。所有 POST 需要 `Origin: http://127.0.0.1:8890`，受会话保护的 POST 还需要 `/api/login` 返回的 `X-CSRF-Token`。POST 发送 JSON，无字段的操作发送 `{}`。未提供任意 Cloudflare API 透传接口。

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

配置示例需先填入自己的账户、Zone 和域名。所有密钥通过隐藏输入。普通业务命令输出 `lanbridge-result/v1` JSON，失败退出码 1；`capabilities`、`instances` 和 `control` 使用各自的 JSON 结构，`serve` 是持续运行的服务命令。运行中的平台持有独占锁，此时配置修改使用管理台/API；CLI `capabilities` 不访问数据，`status` 可只读查看本机配置，普通 CLI 配置命令需先停止平台。`instances`、`open-existing`、`control` 是可在运行期间调用的实例管理入口。`status` 不代表连接器实时运行状态，应通过管理 API 核查。使用另一个 `--data-dir` 可以独立配置另一个账户和端口。

Agent 无需操作启动器窗口，直接调用项目 `.venv` 中的 Python 和 `run.py`。后台启动、结果确认、准确选择实例及关闭/重启示例见 [Agent 启动与实例控制](agent-startup.md)；项目 [Agent Skill](../skills/lanbridge/SKILL.md) 同样指向该说明。

网站 JSON 示例：

```json
{
  "name": "内网工作台",
  "hostname": "app.example.com",
  "origin": "http://192.168.1.20:3000",
  "human_check": true,
  "passcode_required": false,
  "enabled": true,
  "paused": false,
  "allowed_countries": ["CN", "HK", "US", "JP"],
  "allowed_ips": [],
  "requests_per_minute": 180,
  "session_minutes": 60,
  "human_remember_days": 1
}
```

## 变更、失败和运行限制

- Cloudflare 多步骤写入不是原子事务；检查与 PUT 之间仍存在外部并发修改窗口。避免同一 Tunnel 的多人同时维护。API 超时可能已有部分写入，平台记录 `publish_incomplete`，不会盲目删除资源或宣称成功；重新预览可核对并继续。DNS 已创建但 ingress 未发布的情况也按此流程处理。
- 修改本机策略立即影响网关；新增、启用或停用路由仍需预览并应用。停用后本机立即拒绝请求，应用后移除该 hostname ingress；保留 DNS 注册，不自动删除云端资源。
- 固定域名更新不需要重启 cloudflared。连接器异常退出会显示退出码，当前版本需手动再次启动；不安装系统看护、自启动或远程 Agent。
- 支持 HTTP/HTTPS 全路径、查询参数、二进制响应、常见 Cookie/源站绝对重定向，以及 WebSocket 文本与二进制。请求体上限 32 MiB，HTTP 读取超时 120 秒，WebSocket 消息上限 8 MiB。
- 网关每进程最多 128 个 HTTP 转发、32 个 WebSocket、8 个验证请求；同一访客 IP 分别最多 16、4、2 个。超限立即拒绝，不建立等待队列。全局请求入口使用每秒 100 次、突发 200 次的令牌桶；网站/IP 的分钟限额采用固定窗口，记录最多 10,000 个身份，满额时拒绝新的身份而不清空活跃记录。WebSocket 分钟限额只限制握手，不限制连接内的每条消息。
- 验证正文最多 4 KiB，接收期限 10 秒；上传接收总期限 120 秒。声明长度的下载最多 256 MiB，同一进程在接收及发送阶段合计预留最多 512 MiB，缓冲接收总期限 300 秒。超限返回 503；断流、超时或磁盘失败返回 502，不返回不完整的成功响应。单次下游发送阻塞最多 120 秒。无长度的响应保持流式发送，长连接仍受并发限制。
- WebSocket 带 Origin 的浏览器连接仅允许网站自身 HTTPS 来源；不带 Origin 的客户端仍需通过相同访问策略。生产服务器与源站客户端的 WebSocket 接收队列均限制为 2，消息超过 8 MiB 拒绝。
- HTTP 源站连接按网站、固定 IP、Host 和 TLS SNI 隔离复用；每池最多 16 个活动请求、8 个空闲连接，最多 32 个池。空闲 30 秒后由周期任务回收；修改源站后旧池只完成已开始的请求，不再接收新请求。连接满额立即返回 503。每请求保留独立 Cookie 容器；NTLM/Negotiate 请求使用独立连接。代理不自动重试业务请求。
- WebSocket 每连接、每方向最多持续 200 条消息/秒，突发 400 条；字节预算为持续 2 MiB/秒、突发 8 MiB。超限关闭连接（1008）。此限制可能影响高速消息流，需要按应用需求评估；网站/IP 的分钟请求限额仍只针对握手。
- 转发诊断位于 `data/gateway.log`，运行错误位于 `data/runtime.log`，各自最多 5 MiB 当前文件及 3 个备份，合计约 40 MiB。诊断包含请求标识、路径、传输长度及耗时，不记录查询参数、Cookie 或验证正文。正常退出给予活动请求最多 8 秒清理时间，随后取消并关闭连接池。
- 这些限制控制应用资源消耗，不保证遭受 DDoS 时仍可用；大量已验证访客、WebSocket 消息流、源站昂贵接口或带宽攻击仍可能造成拒绝服务。公网保护依赖 Cloudflare 边缘与源站应用自身限制。参见 [安全与效率检查历史](history.md)。
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

## 使用授权令牌管理 API Token（可选）

这是独立于浏览器 OAuth 接入的可选令牌管理流程。账户与配置页提供“自动配置 API Token”；需要此流程时，打开 Cloudflare 用户 API Tokens 页面，使用 **Create Additional Tokens** 模板创建具有 **API Tokens Write** 权限的授权令牌。它不同于 Account API Tokens Write；当前入口使用用户令牌 API。

输入授权令牌后，平台从 API 获取权限组 ID，自动创建限定当前 Account ID 的 Tunnel Write 和限定全部已接入 Zone ID 的 DNS Write、Zone Read 业务令牌；可选择 Turnstile Write。业务令牌加密保存，不返回页面。授权令牌默认仅用于本次请求，勾选记住后独立加密保存，也可从界面移除本机副本。授权令牌可管理用户 API 令牌，应妥善保管。

写入和只读令牌分别管理。只读令牌自动创建 Tunnel Read、DNS Read 和 Zone Read 权限，限定当前账户及 Zone，不改变写入令牌。网页“自动创建新 API Token”始终创建新令牌并替换该类型的本机凭据，旧令牌不会从 Cloudflare 删除。API/MCP/CLI 未指定 force_new 时，已有受托管用户令牌会更新；没有时才新建。

“修复当前写入/只读令牌权限”用于已经保存的用户令牌：先用该令牌验证 ID，再由授权令牌读取远端详情，新增当前账户及 Zone 所需权限，保留原有权限（包括拒绝策略）、名称、有效期、生效时间、IP 条件和状态，不更换令牌值。它不会移除原有写权限来强制变成纯只读。无法验证、账户令牌、其他用户令牌或无法读取权限时停止操作；可改用创建新令牌。有效期、IP 限制或原有拒绝策略造成的失败不会自动解除，更新后请重试业务操作核验。

手动替换或移除凭据会解除对应托管。两种令牌分别记录未知创建结果并阻止重复 POST，页面展示唯一名称供核对；可通过手动配置接入已有令牌。移除本机授权不撤销 Cloudflare 中的令牌。

- CLI：`run.py provision-token`，通过隐藏提示输入授权；`--target read` 配置只读令牌，默认 write；`--repair-existing` 修复对应的当前令牌；`--remember` 保存授权，`--without-turnstile` 不授予 Turnstile。CLI 写操作要求先停止管理服务。
- API：`POST /api/cloudflare/provision-token`，需要管理员会话、Origin 和 CSRF；字段 `authority`、`remember`、`human_check`、`target`（write/read）、`repair_existing`、`force_new`（后两项默认 false）。`POST /api/cloudflare/forget-token-authority` 移除本机授权。
- MCP：`lanbridge_provision_token`，仅使用管理台已加密保存的授权，可传 `human_check`、`target`、`repair_existing`、`force_new`，不接受令牌文本。

参考：[通过 Cloudflare API 创建令牌](https://developers.cloudflare.com/fundamentals/api/how-to/create-via-api/)。

需要强制新建时，API/MCP 使用 `force_new: true`，CLI 使用 `--force-new`；不可与 `repair_existing` 同时使用。创建结果未知时禁止重复创建，先核对云端资源。

## 浏览器授权接入 Cloudflare

账户与配置 → 浏览器授权并自动配置。官方 `cf` CLI 1.0.0-beta.12 使用 PKCE 本机回调；用户在浏览器确认一次 Tunnel Write、DNS Write、Zone Read、Turnstile 和账户读取权限。平台直接使用 OAuth 授权访问 API，不创建子令牌，不需要 API Tokens Write 或 Account API Token Provisioning。授权后先核对实际范围、域名名称、账户归属及 Active 状态，再替换本机凭据；失败保留原凭据。

CLI：`run.py serve --authorize-cloudflare` 在启动平台后发起同一浏览器授权流程，进度及取消操作仍由管理界面提供。

授权和刷新凭据通过 Windows DPAPI 加密保存。每次 API 操作前检查有效期，接近到期时通过官方 CLI 自动刷新。刷新使用独立的受保护临时目录，结束删除临时文件；不影响用户其他 CLI 登录，不保存账户密码，秘密不返回管理页面或日志。撤销授权、资源权限变化或网络异常可能需要重新授权。

Node.js 22.18+ 必需；优先使用项目 `bin/node-runtime/node.exe`，其次查找 PATH 与 Windows 常用安装目录，并核验实际版本。项目内运行时可避免启动器后台服务继承旧 PATH 而找不到 Node.js。首次使用可通过 npm/pnpm 从官方 npm 源安装 CLI 到忽略的 bin/cf-runtime。官方本机回调等待约 2 分钟，但无需等到超时：等待期间切换授权浏览器会结束旧请求并按新选择重新打开授权页；仅关闭浏览器不会取消后台等待。也可点击“取消授权”立即结束请求，或“重新打开授权页”结束旧请求并发起新授权。旧页面随即失效；取消保留原凭据。API 对应 `POST /api/cloudflare/browser-authorize-cancel` 和 `POST /api/cloudflare/browser-authorize-restart`，同样要求管理员会话、Origin 和 CSRF。授权期间修改账户、域名或凭据会停止本次配置。

浏览器授权包含 `challenge-widgets.write`。启用人类验证的网站在授权完成后自动创建或同步专属 Widget，原子保存 Site Key 与加密 Secret Key。已有旧授权在点击“自动配置人类验证”后补充一次浏览器授权，随后自动完成配置。创建结果未知时按专属名称核对并恢复，禁止盲目重复创建；不改动其他项目的 Widget。手动更换写入令牌会清除本机 OAuth 凭据，切回令牌模式。

API：`POST /api/cloudflare/browser-authorize`（管理员会话、Origin、CSRF），后台执行；`GET /api/state` 的 browser_auth 返回进度，不含秘密。参考 [官方 CLI 授权说明](https://developers.cloudflare.com/cf/get-started/)。

## LanBridge 公网入口

在“网站转发”添加网站，把“转发目标”选为 **LanBridge（转发列表与管理台）**，选择已接入域名，填写根域名或该域名下的子域名并保存；平台自动检查、发布并核验。平台自动设置内部目标，不需要填写本机端口。`/client` 展示转发列表；`/admin` 使用已有管理员用户名和密码登录。转发列表包含局域网源站信息，入口的国家、IP、人类验证、访问口令、限流和暂停策略同时适用于这两个页面。

公网入口通过网关完成策略检查后，在进程内进入独立的远程管理模式，不代理回管理端口或网关端口。普通网站仍禁止转发平台自身端口。管理员必须先在本机创建，远程不能初始化管理员、发起或确认本机浏览器登录、打开 Cloudflare 授权浏览器或退出平台。Cloudflare 浏览器授权仍在本机完成；远程可使用已有凭据管理网站和连接器。

远程管理员 Cookie 设置 Secure、HttpOnly、SameSite=Strict，写入操作要求入口域名的 HTTPS Origin 和有效 CSRF Token。暂停自身入口会立即阻断该入口，请从本机管理台恢复。MCP 桥接器仍只连接本机。CLI/API 网站对象可传 `target: "lanbridge"`，源站地址及 HTTP 协议由平台设置；普通网站默认 `target: "website"`。

“调用方式”中的“复制给 Agent”按接入方式区分：“本机工具接入”包含项目文档、CLI、MCP 与本机 API；“仅 HTTP API”包含管理地址、认证流程、网站对象格式和 API 操作，不依赖项目文件。API 模式可选择本机回环地址，或已发布且未暂停的 LanBridge 公网地址。本机 Agent 也可只用 HTTP API；远程 Agent 必须选择可达的公网地址。源站地址始终由运行 LanBridge 的电脑访问。

仅通过 HTTP API 接入能避免直接操作项目文件，但不会自动降低管理员权限。管理员会话仍具有完整权限；可改用下述临时管理 Token 限制 HTTP API 权限。文件隔离仍由 Agent 的运行环境限制。

账户与配置中的“连接器版本”可检查官方 cloudflared 更新。有新版时显示更新按钮，更新操作仅支持本机管理台。程序先下载并验证官方 SHA-256、大小和版本，再切换到项目 bin 下的新文件；不覆盖自定义路径或旧程序。原本运行的连接器会短暂停止后恢复，切换或启动失败时回退原路径并尝试恢复运行。停止状态下更新不会自动启动。自定义程序路径和手动检测/安装在高级设置中；正常启动会自动检测并按需安装。

首次连接 Cloudflare 无需先填写 Account ID、Zone ID 或 Zone 名称。点击浏览器授权后，平台使用已授权的 Zone Read 范围读取 Active 域名及所属账户；只有一个匹配域名时自动配置，多个时在管理台选择。已有完整配置重新授权时会保持所选账户和域名并核验归属；部分已有配置会限定候选范围。授权凭据在域名确认并核验前不替换，选择可以取消，十分钟未选择会结束本次流程。没有 Active 域名时需先在 Cloudflare 完成域名接入，这一步无法由本机授权代替。

复制内容不含凭据，也不会自动授予管理员权限。远程 Agent 必须使用已完成入口验证和管理员登录的浏览器会话；其他浏览器的登录不会自动传递给它。没有交互浏览器的远程 Agent 可使用管理员签发的临时管理 Token，通过 Bearer 调用网站转发 API。现有 MCP 桥接器仍只连接本机，不是公网 MCP 服务。


### 同一账户下的多个域名

“Cloudflare 账户与域名”展示已接入域名列表，不是切换当前域名。浏览器首次授权选择默认域名；在“修改配置 → 添加域名”读取当前账户的 Active 域名后接入，也可手动填写名称和 Zone ID，平台会核验账户归属及 Active 状态。每次接入只修改 LanBridge 配置，不自动修改 DNS。

`settings.zones` 保存域名列表；原 `zone_id` / `zone_name` 保留作为默认域名及旧配置兼容字段。旧网站继续绑定原默认域名。新增网站选择所属域名，`site.zone_id` 保存关联；API 省略时按完整域名边界匹配，重叠区域选择最具体区域。保存网站会按各自 Zone 发布和核验 DNS；共享现有隧道及账户内的 Turnstile Widget。只有添加了网站，才会发布对应 DNS。

域名列表右侧显示“默认域名”标记；其他域名可点击“设为默认”，与“移除”并列。切换只改变新增网站的默认选择，不改变已有网站关联、转发、访问策略、Cloudflare DNS 或远端令牌权限；旧配置中的隐式网站关联在切换前明确保存。接口为 `POST /api/zones/{zone_id}/default {}`，需管理员会话、Origin/CSRF，或具有账户权限的临时授权。

新增域名需具备该区域的 Zone Read 和 DNS Edit 权限。用户 API Token 的创建或修复操作使用全部已接入区域；已有 Token 不会在添加域名时静默扩权。浏览器 OAuth 是否覆盖新增区域由 Cloudflare 实际授权决定，权限不足时停止发布并显示失败。所有域名均提供移除入口；仍被网站（包括停用网站）或已发布路由使用时，列出关联并阻止移除，不自动解除网站。移除未使用的默认域名后，列表中下一个域名自动成为默认；移除最后一个域名则清空默认选择，账户、凭据和隧道保留。只移除本地关联，不删除 Cloudflare 域名或 DNS，不改变令牌远端权限。

管理 API（仍需管理员会话、Origin 与 CSRF）：`POST /api/cloudflare/zones {}` 读取可用域名；`POST /api/zones {"zone_id":"…","zone_name":"example.com"}` 核验并接入；`POST /api/zones/{zone_id}/remove {}` 移除未使用域名。`POST /api/settings` 不允许直接替换域名列表。跨 Cloudflare 账户仍使用独立数据目录、隧道和凭据，不共享本账户连接器。

CLI（写入前停止运行中的平台）：`list-zones` 列出已接入和可接入域名；`add-zone zone.json` 接入文件中指定的域名；`remove-zone ZONE_ID` 移除未使用域名。MCP 提供 `lanbridge_list_zones`、`lanbridge_add_zone`，通过同一管理 API 核验。通用 CLI `configure` 也保留已有域名列表，禁止直接覆盖；初始默认域名仍可按原方式配置。

### API Token 自动读取账户与域名

“使用已有 API Token”提供“读取账户与域名”与“保存并接入”：读取阶段只调用 Cloudflare 域名列表，不保存令牌、不修改 DNS；选择后重新核验资源，将账户、默认域名与加密令牌一起保存。读取需要 Zone Read 权限，可访问多个账户或域名时显示选择列表；已有网站或隧道时保留其账户和默认域名。读取成功不等于具备 Tunnel/DNS 写入权限，具体操作仍按 Cloudflare 实际授权核验。

“自动创建 API Token”中的授权令牌也可以读取账户与域名，然后保存选择并创建业务令牌；如果授权令牌只有 API Tokens Write 而没有 Zone Read，需要手动配置账户与默认域名，或改用浏览器授权。

对应的管理 API（均需管理员会话、同源与 CSRF）：`POST /api/cloudflare/token-discover` 接收 `{token}`，返回可选 `zones` 和配置 `revision`；`POST /api/cloudflare/token-connect` 接收 `{token, revision, account_id, zone_id, save_token}`，默认保存写入令牌，`save_token:false` 仅保存账户与默认域名。不要输出或转发令牌；选择过期或资源不匹配会停止保存。

浏览器授权区域可选择系统默认浏览器或已安装的 Chrome、Edge。首次授权和“重新打开授权页”均使用当前选择。指定浏览器时通过官方 CLI 的 `--no-browser` 输出读取官方 OAuth 地址并打开，不向网页状态或日志暴露授权 URL；取消、PKCE 回调与凭据保存仍由原授权流程控制。


### 临时管理 Token

在管理台左侧“临时授权”（位于“账户与配置”上方）签发，默认有效期 24 小时，提供 1、8、24 小时选项，也可输入任意正数小时，支持小数，例如 0.5 小时。用途名称可留空，自动使用最小的空闲编号，例如“临时管理1”“临时管理2”；已到期或撤销的编号可复用，Token 的唯一 ID 和凭证仍独立。管理列表只显示有效 Token，并显示上次登录、上次授权 API 访问的时间及多久之前；早期 Token 在开始记录前的访问无法追溯。高频 API 调用时，访问时间最多每 30 秒写入一次，以减少数据库写入；每次登录立即更新登录与访问时间，到期和撤销仍逐请求校验。签发时可勾选权限，默认只勾选“网站转发”，不勾选“Cloudflare 账户与域名”。网站转发权限允许：新增、修改和暂停网站，检查源站，发布路由，检查 Cloudflare 连接，启停连接器。额外勾选“Cloudflare 账户与域名”后，可配置接入凭据、添加或移除域名、管理 Cloudflare 业务令牌和隧道令牌。本机浏览器授权仍只支持本机访问。所有临时管理 Token 均不能修改管理员密码、网关端口、连接器程序路径，也不能签发或管理其他临时管理 Token。旧 Token 保持仅网站转发权限。

Token 原值加密保存，身份校验仍使用摘要；管理员可以在有效授权中主动查看或复制。列表接口不返回原值或密文，查看接口为 `POST /api/temporary-tokens/{id}/reveal`，须使用管理员会话、同源 Origin 和 CSRF；临时 Token 无权调用。撤销时清除加密原值。旧版本只保存摘要的 Token 无法恢复原值。可在登录页切换到“令牌登录”；登录后只显示对应权限的页面。也可在 HTTP API 请求头使用 `Authorization: Bearer <token>`，无需 Cookie 或 CSRF；提供 Origin 时仍必须同源。不要将 Token 放入 URL。网站转发权限允许的 API：`GET /api/state`、`GET /api/bootstrap`、`POST /api/sites`、`POST /api/sites/{id}/pause`、`POST /api/sites/{id}/probe`、`POST /api/cloudflare/preview`、`POST /api/cloudflare/apply`、`POST /api/cloudflare/check`、`POST /api/connector/start`、`POST /api/connector/stop`。

公网 Bearer 接入仅限已发布的 LanBridge 平台入口，仍检查 HTTPS、入口是否启用或暂停、IP/国家限制及请求限流；不需要另行取得入口访问验证 Cookie，也不会授权访问普通转发网站。网页登录首次进入仍需完成入口访问验证。到期或撤销后，Token 和从它派生的网页登录会话立即失效。最多 20 个有效 Token。临时管理 Token 不改变本机 CLI 或 MCP 的权限。

签发 API `POST /api/temporary-tokens` 支持 `permissions: ["sites", "account"]`；省略时为 `["sites"]`，空列表或未知权限拒绝。账户权限允许 `/api/settings`（仅账户、默认域名、隧道名称字段）、`/api/credentials`（仅 Cloudflare 接入令牌）、域名管理及账户卡中的浏览器授权、令牌发现/接入、业务令牌管理和隧道令牌获取接口。权限在签发时固定，需改变时撤销并重新签发。

管理员密码登录后，本机及公网管理台均可签发、查看和撤销临时管理 Token；写操作保留 Origin 与 CSRF 校验。所有临时管理 Token 均不能签发或管理其他临时管理 Token。管理员密码修改仍仅支持本机管理入口，公网管理员会话也不能修改。


**已运行实例**：启动器会检测本机可读取进程信息的 LanBridge 实例，显示管理端口与运行目录。默认选中本目录实例，本目录正在运行时，主按钮显示“打开管理台”、管理端口只读；实例退出并重新检测后恢复端口编辑。选中其他实例后点击“打开选中管理台”，完整运行目录和数据目录可悬停查看；支持不同项目目录和绝对路径的独立数据目录。同一数据目录仍禁止重复启动。旧运行版本使用进程、监听端口和管理接口兼容识别。进程信息不可读取或使用无法确定工作目录的相对数据路径时，无法可靠列出；检测失败会显示提示。

## 启动时自动连接

“账户与配置 → Cloudflared 连接器”提供“启动时自动连接”开关，默认开启，只能由本机管理员修改。设置保存后供下次普通启动使用，不会立即启动或停止连接器。

普通启动先开放管理台、启动转发网关，再检查隧道 ID、连接令牌和 Cloudflared 是否就绪，尝试一次自动连接。未配置或失败时管理台继续可用，在连接器卡片中显示原因，可完成配置后到“网站转发”手动启动；不会反复自动重试或自动安装缺失的 Cloudflared。

手动停止连接器后，本次运行保持停止；下次普通启动仍按开关决定。网页“重启 LanBridge”保留重启前连接器的运行或停止状态。启动器打开已有实例时不改变连接器状态。

## 从启动器关闭和重启实例

启动器的“本机已运行实例”列表中选中一行，可使用“关闭实例”或“重启实例”；确认框显示管理端口和运行目录。关闭会停止该实例的管理台、网关和连接器，重启应用待生效端口并保留连接器此前的运行或停止状态。其他实例不受影响。操作完成后启动器保留打开并重新检测列表。

点击右上角 × 只关闭启动器窗口，后台 LanBridge 继续运行。实例操作进行中需等待完成后再关闭窗口。控制操作通过受保护数据目录中的本次运行身份与控制凭据核验，不要求在浏览器中登录，也不通过强制结束进程实现。公网和网页来源请求不能使用该控制接口。

已经运行的旧版本没有启动器控制能力，按钮会禁用：先从管理台退出旧实例，再通过启动器启动一次即可加载新功能。

启动器默认勾选“启动后打开管理台”；取消勾选后，点击“启动 LanBridge”只启动后台服务。已运行时隐藏该选项，可通过“打开管理台”或列表中的“打开选中管理台”主动访问。

## 依赖文件与安装

`requirements.txt` 记录项目直接依赖；`requirements.lock.txt` 固定完整依赖（含间接依赖和测试工具）的版本。`install.ps1` 使用后者安装，漏洞检查脚本也读取同一清单，避免安装与检查版本不一致。两者不是废弃副本。更新依赖时同步直接清单和完整锁定清单，在隔离环境验证；不要用重新安装依赖来修复正式运行进程。

### Cloudflare 管理授权续期

浏览器授权的管理 Token 会按返回的有效期自动续期。续期暂时失败时，账户设置会显示影响范围及“重试续期”；先检查网络并在本机用管理员账户重试，仍失败再点“重新授权”。提前续期失败时，尚有足够有效期的旧 Token 会继续用于管理请求。续期失败不会使 LanBridge 管理员密码失效，也不会因此停止已运行的转发；公网网站仍可访问不代表 Cloudflare 管理授权已经恢复。手动 API Token 不使用此续期流程。

### 修改已有网站的公网域名

在“网站转发”编辑网站，可修改“公网域名”和“所属域名”，支持已接入域名的根域名或子域名。保存时保留网站 ID、源站、访问策略和已保存的网站口令；启用的网站会按现有流程自动发布并核验。发布失败时按页面提示重试。

修改后旧地址停止转发，不会自动跳转到新地址；原 Cloudflare DNS 记录不会被自动删除。需要跳转或清理旧 DNS 时单独处理。

### 后台保存与发布

网页“保存网站与策略”先完成本机校验与配置保存，然后立即返回，Cloudflare 人类验证配置、DNS 和路由发布及核验在后台执行。页面显示实时阶段；云端失败时保留已保存配置，点击“重试发布”继续。任务执行期间不重复提交网站保存。未发布成功前不要把保存提示理解为公网已完成更新。

后台任务不保存明文口令，也不在重启后自动重放云端写操作；若执行被中断，会提示核验并重试。既有 CLI 和未指定后台模式的 API 保持原同步行为。


### 日志操作权限

公网完整管理员可以查看、下载操作日志；日志容量设置仅支持本机完整管理员，公网不能增减容量，也不能通过日志接口执行清空等写操作。临时管理令牌继续不能访问日志。公网页面隐藏日志设置，并提示“日志设置仅限本机修改”。后端按实际入口判定，伪造转发头不能获得本机权限。

已通过管理员身份、Origin 和 CSRF 校验的公网日志写入尝试返回 403，并记录“拒绝公网日志修改”；同一远程管理实例每五分钟最多记录一次，防止拒绝请求刷满日志。系统没有开放清空日志接口。达到现有容量上限时仍自动清理最旧记录，本限制不等同于不可删除或永久保存日志。


### 小流量网站的访问保护

每天不到一千次访问时，沿用一天浏览器记忆、现有限速和异常短期限制即可，无需增加动态评分或更多策略开关。普通请求及验证接口触发分钟限流后，Retry-After 使用当前窗口实际剩余秒数，向上取整，不再固定额外等待 60 秒；持续异常触发的 5–15 分钟限制仍按实际剩余时间执行。倒计时结束后由访客手动重试，不自动发起验证。


验证组件的 Site Key 和 Secret Key 通过一次请求原子保存，不会因第二次请求失败留下半套配置；只保存验证字段，不覆盖其他账户设置。保存中重复点击或回车不会重复提交。验证服务繁忙并返回等待时间时，重试按钮倒计时结束后才可用，不自动反复请求。
