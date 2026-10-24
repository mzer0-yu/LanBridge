# LanBridge 命令行

项目同时提供网页管理台、CLI、HTTP API 和 MCP。CLI 的在线命令复用同一套服务端业务、权限及安全校验，可以在服务运行期间操作；无需为了修改网站停止转发。

## 安装与入口

已有项目依赖时，直接运行 `./lanbridge.cmd --help`。希望在任意目录输入 `lanbridge`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-cli.ps1 -AddToPath
```

重新打开终端后生效。安装脚本只将当前项目目录追加到当前用户 PATH，不复制另一份源码、不改变系统 PATH。移动项目后须更新 PATH。首次安装也可运行 `install.ps1 -AddToPath`，会先安装锁定依赖。

以下三种入口共用同一实现：

```powershell
lanbridge --help
.\lanbridge.cmd --help
.\.venv\Scripts\python.exe -m lanbridge --help
```

`run.py` 的全部旧命令仍兼容。`serve`、`instances`、`control` 等实例命令保持原行为；在线管理使用 `online` 子命令。

## 登录与自动化

```powershell
lanbridge serve
# 在另一个终端登录，密码隐藏输入
lanbridge online login
lanbridge online state
lanbridge online logout
```

已有实例会继续使用，不会重复启动。默认从项目 `data/runtime.json` 只读发现当前管理端口，找不到运行标记则使用 `http://127.0.0.1:8890`。在线命令不构造 Service、不直接打开正式数据库。

指定地址、数据目录、用户名等公共选项须写在在线操作之前：

```powershell
lanbridge online --url http://127.0.0.1:9000 --username admin login
lanbridge online --data-dir C:\LanBridgeData state
lanbridge online --url https://lanbridge.example.com state
```

远程地址必须使用 HTTPS，证书校验不可关闭；本机专属操作仍被服务端拒绝。重定向不跟随，写操作失败不会自动重试。管理端口改变后需要对新地址重新登录。

登录后的 Cookie 与 CSRF 按完整管理地址隔离，使用 Windows DPAPI 加密保存到 `data/cli-sessions/`，仅同一 Windows 用户可解密。不会保存管理员密码。服务端会话到期或撤销后需重新登录。会话损坏时可运行 `online logout --local` 只清除本地副本；正常 logout 会撤销服务端会话。可通过 `--profile-dir` 为不同终端自动化隔离会话。共用会话目录时，缓存更新和清除使用短期跨进程锁，并核对读取时版本；旧响应不会覆盖新登录，旧注销不会删除新会话，本地清除后旧响应不会重建缓存。检测到会话被另一命令改变时会提示请求已处理、先核对状态，写操作不会自动重试。未变化的普通查询不重写加密会话文件。

自动化可由调用环境提供 `LANBRIDGE_TOKEN`（临时管理 Token）或 `LANBRIDGE_PASSWORD`（仅管理员 login/setup 时读取）。也可以 `online --token state` 隐藏输入临时 Token。Token 不落盘，权限范围、期限及撤销继续由后端控制；不会因此取得完整管理员权限。不要把秘密写进命令历史、仓库或日志。初始化管理员：`online --username admin setup`。

## 常用操作

```powershell
lanbridge online sites
lanbridge online save --file website.json
lanbridge online probe SITE_ID
lanbridge online pause SITE_ID
lanbridge online pause SITE_ID --field cloudflare=true
lanbridge online resume SITE_ID
lanbridge online protection
lanbridge online retry-publish
lanbridge online retry-publish --field repair_dns_conflict=FAILED_JOB_ID
lanbridge online connector ensure
lanbridge online connector start
lanbridge online connector stop
lanbridge online connector check-update
lanbridge online connector update --field version=v2026.1.0
lanbridge online cloudflare preview
lanbridge online cloudflare apply --field revision=PREVIEW_REVISION
lanbridge online cloudflare check
lanbridge online cloudflare browser-authorize --field require_waf=true
lanbridge online cloudflare browser-authorize-setup
lanbridge online logs
lanbridge online export-logs --output .\audit.jsonl
lanbridge online tokens
lanbridge online issue-token --field name=Agent --field hours=1 --show-secrets
lanbridge online revoke-token TOKEN_ID
lanbridge online restart
lanbridge online shutdown
```

占位 ID、版本和 revision 须替换成实际返回值。连接器更新版本应取自 check-update，以上版本只演示参数格式。暂停默认使用友好页面，`cloudflare=true` 请求同时云端阻断；需要完整管理员及 WAF 权限。恢复沿用服务端解除规则与核验流程。DNS 迁移绑定当前失败任务，仍需完整管理员，不能用命令跳过预览、确认或冲突检查。

`website.json` 使用已有网站对象格式；新建不带 id，编辑带 id 并保留现有完整字段。在线 save 默认使用后台发布。静态主页 URL、HTTP/WebSocket 协议、源站 Host、人类验证、国家/IP 白名单、访问口令、限流、启用/停用等字段均可提交，不限于这些快捷命令。网站保存、暂停、浏览器授权和域名接入可能先返回后台任务；用 `online state` 或相应状态接口核对 phase 和错误，提交成功不代表云端已完成。

Cloudflare/阿里云 OAuth 可以从 CLI 发起、查询进度、选择域名、取消和重试；官方授权页面仍需用户确认，CLI 不代替人类完成 OAuth 或 Turnstile。

## 每项功能的独立命令

每个管理操作都有具名命令及 `--help`，无需输入 HTTP 方法、API 路径或手写 JSON。完整逐项对应表见 [全部命令清单](cli-commands.md)。`online commands` 同时输出命令名、路径参数、请求字段、查询参数及调用入口，方便自动化发现。

```powershell
lanbridge online save-site --help
lanbridge online save-site --name Device --hostname device.example.com --origin http://192.168.1.20:8080 --enabled true --human-check true --human-check-mode adaptive --allowed-countries CN,HK --protocols http,websocket
lanbridge online site-pause-mode --site-id SITE_ID --paused true --cloudflare true
lanbridge online public-client --enabled false
lanbridge online connector-auto-start --enabled true
lanbridge online admin-port --port 9000
lanbridge online gateway-port --port 9001
lanbridge online gateway-retry
lanbridge online change-password --current --password
lanbridge online credentials --cf-write-token
lanbridge online log-settings --limit-mb 10
lanbridge online logs --before 123
lanbridge online token-reveal --token-id TOKEN_ID --show-secrets
lanbridge online zones-default --zone-id ZONE_ID
lanbridge online cloudflare-browser-authorize --browser edge --require-waf true
lanbridge online cloudflare-browser-authorize-select-zone --zone-id ZONE_ID
lanbridge online cloudflare-browser-authorize-refresh
lanbridge online cloudflare-browser-authorize-cancel
lanbridge online cloudflare-provision-token --authority --remember true --human-check true
lanbridge online domain-onboarding-credentials --access-key-id --access-key-secret
lanbridge online domain-onboarding-prepare --domain example.com
lanbridge online domain-onboarding-confirm --id JOB_ID --confirmed-domain example.com
lanbridge online domain-onboarding-connected-domains --refresh true
lanbridge online domain-onboarding-backup --output .\dns-backup.json
```

具名参数规则：布尔值明确传 true/false；国家/IP、协议和权限数组可用逗号分隔或 JSON 数组；数字自动转换（有效期小时数和日志 MB 支持小数）；`save_token`、`remove_cf_read_token` 是普通布尔选项，不触发秘密输入；网站名称等文本保持原样，数字形式名称也不会变成数字。秘密参数（如 `--password`、`--cf-write-token`、`--authority`）只是隐藏输入开关，不在参数后写秘密内容。现有 `--file`、`--field`、`--secret` 及快捷命令仍兼容。

所有管理接口共 72 项：71 项在 `online` 中有具名入口，启动器内部控制接口对应已有根命令 `lanbridge control`。登录/初始化/注销使用已有 login/setup/logout；同一份后端继续判断完整管理员、临时权限与本机/公网范围。命令存在不代表当前身份有权执行。后台任务仍需查询 state 核对完成状态。

## 全功能管理入口

```powershell
# 自动列出当前版本所有管理路由及其 action
lanbridge online commands

# 高级通用调用（可选，日常操作也可用上面的具名命令）
lanbridge online api GET /api/state
lanbridge online api POST /api/public-client --field enabled=false
lanbridge online api POST /api/connector-auto-start --field enabled=true
lanbridge online api POST /api/admin-port --field port=9000
lanbridge online api POST /api/password --secret current --secret password
lanbridge online api POST /api/credentials --secret cf_write_token
lanbridge online api POST /api/sites --file website.json
lanbridge online api POST /api/zones --file zone.json
lanbridge online api POST /api/zones/ZONE_ID/default
lanbridge online api POST /api/zones/ZONE_ID/remove
lanbridge online api POST /api/audit/settings --field limit_mb=10
lanbridge online api GET /api/domain-onboarding/backup --output .\dns-backup.json
lanbridge online domain prepare --field domain=example.com
lanbridge online domain confirm --field id=JOB_ID --field confirmed_domain=example.com
lanbridge online domains
```

API 清单由服务端源码自动生成，包含静态路径以及 Cloudflare、连接器、域名接入的动态 action；具体 ID 占位符需替换。`/api/launcher/control` 是启动器内部专用身份入口，命令行进程控制用已有 `lanbridge instances` 和 `lanbridge control stop/restart --instance INSTANCE_ID`；不伪造启动器控制头。

| 功能范围 | 命令或 API |
| --- | --- |
| 网站、静态目录、协议、策略、暂停/恢复、源站探测、发布重试/迁移 | sites/save/pause/resume/probe/retry-publish，`/api/sites` |
| 每站及汇总 24 小时访问防护统计、运行进度与错误 | protection/state，`/api/state` |
| 配置、密钥、公网列表、启动自动连接、端口、密码 | settings/credentials，`/api/settings`、`/api/public-client`、`/api/connector-auto-start`、`/api/admin-port`、`/api/gateway-port`、`/api/gateway-retry`、`/api/password` |
| 管理员登录、初始化、本机授权登录、临时 Token | login/setup/logout/tokens/issue-token/revoke-token，`/api/local-login/*`、`/api/token-login`、`/api/temporary-tokens/*` |
| Cloudflare 授权、刷新、令牌发现/接入/创建、Tunnel、Turnstile、预览与发布 | cloudflare，`/api/cloudflare/*` |
| 域名增删、默认域名、阿里云接入预览/确认/核验/取消和备份 | domains/domain，`/api/zones/*`、`/api/domain-onboarding/*` |
| 日志查看、翻页、导出及容量设置 | logs/export-logs，`/api/audit?before=ID`、`/api/audit/settings` |
| 连接器准备、路径、启停、检查更新/更新 | connector |
| 平台启动、实例发现、打开管理台、正常停止/重启 | serve/instances/open-existing/control，online shutdown/restart |

## 输入、输出与错误

- `--file file.json` 读取 UTF-8/BOM JSON 对象；`--file -` 从标准输入读取。请求体上限 20000 字节，与服务端一致。`--field 字段=值` 可重复，值支持 JSON 布尔值、数字、数组和对象，不能用于秘密字段。
- 秘密使用 `--secret 字段名` 隐藏输入，可重复；批处理可从安全输入流提供 JSON。不要把密码或令牌作为命令行参数。
- 默认 JSON 输出，成功退出码 0、失败 1，参数语法错误 2（用法与错误写到 stderr，不回显原始参数值，避免误填秘密进入日志）。返回的 token、secret、password、csrf 等字符串默认隐藏；确实需要领取 Token 时显式 `--show-secrets`，并保护输出。
- `--output 新文件` 流式保存原始响应，包括日志/备份/文本；不会自动遮罩原始下载，应保护文件。输出文件在发送请求前排他创建；已有文件、目录缺失或不可写会在请求前拒绝，避免误覆盖和不必要写操作。未完成下载会移除本次创建的残缺文件；若服务端接受后才发生磁盘写入、刷新或关闭故障，会明确提示先查询状态、勿重复提交。JSON 响应下载保留有界会话元数据副本，登录/Token 登录等使用 --output 也会更新加密会话中的 CSRF。
- 普通 JSON 响应最多 8 MiB，较大响应请保存文件。--timeout 须为有限正数；非法域名、运行标记或统计响应会显示错误而非 Python traceback。重定向、HTTP 错误与传输异常不会自动重试，不回显可能包含凭据的服务端错误正文。HTTP 401 请重新登录，403 核对权限，400 核对输入及服务端操作记录；响应未知时先查询状态再决定是否重试。
- status 使用只读数据库快照，不创建数据目录、保险库或日志，不迁移旧库；未初始化时提示先运行 serve/setup-admin。旧的直接配置 CLI（configure/save-site 等）在数据库初始化前取得运行锁，写入前须停止平台；日常运行中修改使用 online。后台授权、转发、验证和本机/公网权限边界完全沿用服务端机制。
