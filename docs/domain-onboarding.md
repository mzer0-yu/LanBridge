# 阿里云独立域名自动接入

这是本地未发布的新功能，不包含在 GitHub 已发布的 v1.0.0 源码包中。入口：本机管理台 → 账户与配置 → Cloudflare 账户与域名 → 阿里云域名自动接入。新代码需要重启 LanBridge 后加载；不要仅刷新网页来代替后端重载。

## 操作步骤

1. 先接入目标 Cloudflare 账户。当前写入凭据必须能够创建新 Zone，并读取区域、编辑新域名的 DNS。原来只允许已有域名的令牌不够；需要重新创建具有目标账户资源范围的令牌，不能仅在 LanBridge 点击“修复”来假定自动获得权限。
2. 展开“阿里云授权”，点击“浏览器授权”，在运行 LanBridge 的电脑上完成阿里云官方 CLI OAuth 登录。先安装官方 CLI 3.3.0 或更新版本（加入 PATH，或将 `aliyun.exe` 放入项目 `bin/`）。首次使用可能需要管理员安装/同意 `official-cli` 应用，并给 RAM 身份分配访问权限。OAuth 不会自动扩大域名操作权限。授权结果和刷新令牌在当前 Windows 用户的保险库加密保存，临时配置使用受限目录并在调用结束后清除。临时 STS 凭据按需续期，失败后原位提示重新授权。

   “手动 RAM 凭据（备用）”仍支持 AccessKey ID、AccessKey Secret 和可选 SecurityToken，不使用主账户 AccessKey。保存手动凭据会切回手动模式并清除本机 OAuth 配置；重新浏览器授权成功后则清除手动凭据。页面和日志不回显密钥。授权只在本机完整管理员会话开放，云端登录页由运行 LanBridge 的电脑打开。
3. 输入独立域名，点击“准备并预览”。程序读取阿里云注册商 DNS 服务器、DNSSEC DS 和全部 AliDNS 记录，再创建或复用当前 Cloudflare 账户的 full Zone；这一步不更改原解析或注册商 Nameservers。
4. 核对预览中的 DNS 服务器及全部记录，下载原 DNS 备份。有 DNS 记录时，需再次输入域名并勾选确认；没有 DNS 记录时，省去这两项，核对域名及切换说明后点击“确认接入并切换 DNS”。两种情况都需要这次明确确认，预览不会自动切换。程序重新读取两端记录，防止预览后记录变化；先迁移缺少的记录、读回核对，再向阿里云提交 Nameservers 变更任务。
5. 任务每分钟在后台检测一次，等待注册商 Nameservers 与新分配值一致、Cloudflare 状态为 Active 后，自动把域名加入 LanBridge。此后可在“网站转发”添加该域名下的网站，按现有流程发布。

仅支持本机完整管理员会话。公网管理和临时管理令牌不能调用此流程。一次只跟踪一个迁移任务。

“已接入的阿里云域名”列表读取当前阿里云账户持有域名，与 LanBridge 已接入列表取交集；它不依赖接入任务历史，因此通过其他方式接入的阿里云域名也会显示。列表不包含尚未接入或不属于当前阿里云账户的域名。打开区域后异步读取，提供“刷新列表”，显示查询时间；查询结果短期缓存 5 分钟，刷新可绕过缓存，账户或接入列表变化后失效。查询失败不会当作空列表，刷新失败时保留并明确标注上次结果。已完成任务移至操作日志的最近完成任务入口，活动任务仍展开，不更改确认步骤。

## 权限准备

Cloudflare API Token：目标账户中适当域名资源范围的 Zone Read、Zone Edit、DNS Edit；既有 Tunnel 权限继续保留。不自动扩大权限。当前浏览器 OAuth 新授权会申请 `zone.write`（Zone 编辑），旧版授权未包含此项，需要在“修改接入方式”重新浏览器授权一次；普通续期不会扩大权限。若新授权仍无法创建 Zone，核对目标账户的角色、资源范围和实际授予权限，也可显式使用具有目标账户 Zone Edit 权限的写入 API Token。正常 OAuth 刷新不会因令牌值轮换而中断任务；账户、权限范围或阿里云凭据变化需要重新核对。

阿里云需要以下接口访问能力。权限 Action 名不能简单用 API 名替代：

| 接口 | 官方公开的 RAM Action / 用途 |
| --- | --- |
| QueryDomainByDomainName | `domain:QueryCommonInfo`，查询注册商域名状态和 DNS 服务器 |
| QueryDomainList | `domain:QueryCommonInfo`，分页读取账户持有域名；[官方文档](https://help.aliyun.com/zh/dws/developer-reference/api-domain-2018-01-29-querydomainlist) |
| QueryDSRecord | 查询 DNSSEC DS；官方页面未公开具体授权 Action，需在阿里云 OpenAPI Explorer / RAM 核对，程序不能跳过这一步 |
| DescribeDomainRecords | `alidns:DescribeDomainRecords`，读取源 DNS 全部记录 |
| SaveBatchTaskForModifyingDomainDns | `domain:DnsModification`，修改指定注册域名的 DNS 服务器 |
| QueryTaskDetailList | `domain:QueryDomainTask`，查询提交结果 |

尽量将支持资源级限制的权限限定到目标域名，具体 Resource 和其他条件以官方当前授权表为准。某个查询权限不足时流程停止，不通过授予全部管理员权限作为默认解决方案。创建 RAM 用户、授权或签发 STS 由你在阿里云完成；LanBridge 不接收你的阿里云登录密码。

## 支持范围与失败处理

- 当前 DNS 需由阿里云托管（hichina.com、alidns.com、aliyundns.com 下的 Nameservers）。在阿里云购买但 DNS 已迁移到其他服务商的域名，使用现有“添加其他域名”流程，或先人工迁移 DNS；程序不能从不权威的 AliDNS 空列表推断完整记录。
- 只迁移启用、默认线路、不带负载均衡的 A、AAAA、CNAME、MX、TXT、子域 NS、CAA、SRV，最多 1000 条；TTL 需可原样保留（60–86400 秒）。根域 NS、其他记录类型、特殊线路、停用记录、DNSSEC DS 存在或状态不明时阻止迁移。域名过期、安全锁开启、正在转出/更改持有者、邮箱验证被 clienthold 或上述状态不明确时，先到阿里云处理。
- 保留 DNS 值、MX/SRV 优先级、TTL，并设为 DNS only，不自动启用代理。Cloudflare 已有额外记录、冲突记录或 TTL/代理设置不同，需先人工处理，不覆盖或删除。网站、邮箱、验证 TXT 都需要核对；NS 切换和全球缓存更新不是即时完成。
- “取消准备”仅清除本机预览；已创建的 Cloudflare Zone 保留，未提交 Nameservers。DNS 导入部分失败时，原 Nameservers 保持不变，Cloudflare 可能留下已导入记录；处理后重新准备可复用相同记录。
- 写入前保存原 DNS 备份，保留每个域名首次迁移前的备份，不自动覆写。后续预览记录可能与首次备份不同，两者分别核对；备份下载含 DNS 内容，应当妥善保存。
- 提交后网络中断、响应缺失或进程异常会留下“结果未确认”。程序不会自动重发注册商写操作；点“检测接入状态”或去阿里云任务列表核对。即使没有任务编号，仍可通过注册商 NS 与 Cloudflare Active 状态判断完成。
- 普通重启恢复等待中的跟踪；后台等待超过 24 小时后停止自动重试，仍可手动检测。“结束跟踪”需要输入域名确认，仅结束本机跟踪，不撤销云端变更，不恢复 DNS。
- 回退请在阿里云人工恢复备份中的原 Nameservers，并保留原服务商 DNS 记录；程序不会自动删除原 DNS 或取消已提交的注册商任务。

## HTTP API

只接受本机管理员 Cookie；POST 同时要求正确 Origin 和 `X-CSRF-Token`。不提供临时令牌或公网自动迁移权限。

| 方法和路径 | 用途 / JSON |
| --- | --- |
| GET `/api/domain-onboarding` | 凭据是否已保存及当前任务（无凭据原文） |
| GET `/api/domain-onboarding/connected-domains` | 阿里云持有且已接入 LanBridge 的交集；`?refresh=1` 强制重新查询，仅本机完整管理员 |
| POST `/api/domain-onboarding/oauth` | `{}`，浏览器 OAuth 授权，最长等待 180 秒 |
| POST `/api/domain-onboarding/credentials` | `{access_key_id, access_key_secret, security_token}`；同时保存，token 可为空 |
| POST `/api/domain-onboarding/prepare` | `{domain}`，创建/复用 Zone 并保存 30 分钟有效的预览 |
| POST `/api/domain-onboarding/confirm` | `{id, confirmed_domain}`，逐项核对后显式确认 |
| POST `/api/domain-onboarding/check` | `{}`，只检查，不重发 Nameservers 写请求 |
| POST `/api/domain-onboarding/cancel` | `{id}`，清除未提交的任务 |
| POST `/api/domain-onboarding/finish` | `{id, confirmed_domain}`，结束已提交任务的本机跟踪 |
| GET `/api/domain-onboarding/backup` | 下载当前任务域名的首次原 DNS 备份 |
| GET `/api/domain-onboarding/help` | 当前说明 |

Agent 可以通过已授权的本机管理员会话调用 API，但不能替用户自动确认 DNS 切换。不会读取浏览器密码库，也不新增 CLI 读取明文密钥参数。

## 官方参考

- [阿里云官方 CLI OAuth 配置](https://help.aliyun.com/zh/ram/configure-oauth-for-alibaba-cloud-cli)
- [Cloudflare 创建 Zone API](https://developers.cloudflare.com/api/resources/zones/methods/create/)
- [Cloudflare full setup](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)
- [阿里云修改域名 DNS 服务器](https://help.aliyun.com/zh/dws/developer-reference/api-domain-2018-01-29-savebatchtaskformodifyingdomaindns)
- [阿里云域名查询](https://help.aliyun.com/zh/dws/developer-reference/api-domain-2018-01-29-querydomainbydomainname)
- [阿里云 DNSSEC DS 查询](https://help.aliyun.com/zh/dws/developer-reference/api-domain-2018-01-29-querydsrecord)
- [阿里云任务详情](https://help.aliyun.com/zh/dws/developer-reference/api-domain-2018-01-29-querytaskdetaillist)
- [AliDNS 记录列表](https://help.aliyun.com/zh/dns/api-alidns-2015-01-09-describedomainrecords)
- [阿里云 RPC 签名](https://help.aliyun.com/en/sdk/product-overview/rpc-mechanism)

验证使用模拟云端和隔离数据，没有对真实注册域名执行 NS 切换；首次使用仍需核对实际账户权限、记录及云端结果。

### 授权完成后暂不接入

完成 Cloudflare 或阿里云授权不会自动迁移新域名。Cloudflare 授权更新后，旧创建区域失败记录显示为“可继续接入新域名”；选择“返回域名接入”后仍需手动准备并预览。选择“暂不接入”仅清除该次创建区域失败提醒并保留审计记录，不删除已有域名、不修改 DNS、不撤销授权。存在尚未结束的接入任务时拒绝清除，请使用任务自己的取消或结束跟踪入口。新失败记录不会被旧页面请求清除。

### 已完成任务的展示

成功接入后，接入区域隐藏任务卡片及重复的完成提示；进行中和失败任务继续显示。操作日志顶部提供“最近完成的域名接入”折叠入口，可查看域名、迁移记录数、前后 DNS 服务器及下载原 DNS 备份。该入口只对应本机保留的最近任务，新任务建立后不再显示旧任务摘要；操作审计记录按日志保留规则保存，原 DNS 备份仍加密保存，不写入审计明文。仅本机完整管理员可查看此入口和下载备份。
