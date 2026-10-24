# 全部管理命令对应表

当前管理入口与命令逐项对应。参数用下划线转换成连字符；每条命令使用 `--help` 查看具名字段，秘密参数会隐藏输入。此表由当前服务端路由与 CLI 元数据生成。

| 命令 | 功能 | 方法与管理入口 | 必需路径参数 |
| --- | --- | --- | --- |
| `lanbridge online admin-port` | 设置管理端口 | `POST /api/admin-port` | — |
| `lanbridge online logs` | 查看操作日志 | `GET /api/audit` | — |
| `lanbridge online export-logs` | 导出操作日志 | `GET /api/audit/export` | — |
| `lanbridge online log-settings` | 设置操作日志容量 | `POST /api/audit/settings` | — |
| `lanbridge online bootstrap` | 查看登录和初始化状态 | `GET /api/bootstrap` | — |
| `lanbridge online client-routes` | 查看公网网站列表 | `GET /api/client/routes` | — |
| `lanbridge online cloudflare-apply` | 应用预览的云端变更并核验 | `POST /api/cloudflare/apply` | — |
| `lanbridge online cloudflare-browser-authorize` | 发起 Cloudflare 官方浏览器授权 | `POST /api/cloudflare/browser-authorize` | — |
| `lanbridge online cloudflare-browser-authorize-cancel` | 取消当前 Cloudflare 授权 | `POST /api/cloudflare/browser-authorize-cancel` | — |
| `lanbridge online cloudflare-browser-authorize-refresh` | 续期 Cloudflare 授权 | `POST /api/cloudflare/browser-authorize-refresh` | — |
| `lanbridge online cloudflare-browser-authorize-restart` | 重新发起 Cloudflare 授权 | `POST /api/cloudflare/browser-authorize-restart` | — |
| `lanbridge online cloudflare-browser-authorize-select-zone` | 选择授权域名 | `POST /api/cloudflare/browser-authorize-select-zone` | — |
| `lanbridge online cloudflare-browser-authorize-setup` | 继续授权后的隧道配置 | `POST /api/cloudflare/browser-authorize-setup` | — |
| `lanbridge online cloudflare-check` | 检查云端连接 | `POST /api/cloudflare/check` | — |
| `lanbridge online cloudflare-create-tunnel` | 创建或恢复专用隧道令牌 | `POST /api/cloudflare/create-tunnel` | — |
| `lanbridge online cloudflare-forget-token-authority` | 移除本机保存的令牌管理授权 | `POST /api/cloudflare/forget-token-authority` | — |
| `lanbridge online cloudflare-preview` | 预览 DNS 和路由变更 | `POST /api/cloudflare/preview` | — |
| `lanbridge online cloudflare-provision-token` | 创建或修复业务 API Token | `POST /api/cloudflare/provision-token` | — |
| `lanbridge online cloudflare-token-connect` | 确认并接入令牌对应的账户与域名 | `POST /api/cloudflare/token-connect` | — |
| `lanbridge online cloudflare-token-discover` | 发现令牌可访问的账户与域名 | `POST /api/cloudflare/token-discover` | — |
| `lanbridge online cloudflare-turnstile` | 同步人类验证域名 | `POST /api/cloudflare/turnstile` | — |
| `lanbridge online cloudflare-turnstile-auto` | 自动准备人类验证 | `POST /api/cloudflare/turnstile-auto` | — |
| `lanbridge online cloudflare-zones` | 查询账户可接入的域名 | `POST /api/cloudflare/zones` | — |
| `lanbridge online connector-auto-start` | 设置启动时自动连接 | `POST /api/connector-auto-start` | — |
| `lanbridge online connector-check-update` | 检查连接器更新 | `POST /api/connector/check-update` | — |
| `lanbridge online connector-ensure` | 检测或准备连接器 | `POST /api/connector/ensure` | — |
| `lanbridge online connector-start` | 启动连接器 | `POST /api/connector/start` | — |
| `lanbridge online connector-stop` | 停止连接器 | `POST /api/connector/stop` | — |
| `lanbridge online connector-update` | 更新连接器 | `POST /api/connector/update` | — |
| `lanbridge online credentials` | 保存或移除访问凭据 | `POST /api/credentials` | — |
| `lanbridge online domains` | 查看域名接入进度 | `GET /api/domain-onboarding` | — |
| `lanbridge online domain-onboarding-backup` | 下载域名 DNS 备份 | `GET /api/domain-onboarding/backup` | — |
| `lanbridge online domain-onboarding-cancel` | 取消域名接入 | `POST /api/domain-onboarding/cancel` | — |
| `lanbridge online domain-onboarding-check` | 检查域名接入结果 | `POST /api/domain-onboarding/check` | — |
| `lanbridge online domain-onboarding-confirm` | 确认域名 DNS 迁移 | `POST /api/domain-onboarding/confirm` | — |
| `lanbridge online domain-onboarding-connected-domains` | 查看已接入的阿里云域名 | `GET /api/domain-onboarding/connected-domains` | — |
| `lanbridge online domain-onboarding-credentials` | 保存阿里云授权凭据 | `POST /api/domain-onboarding/credentials` | — |
| `lanbridge online domain-onboarding-dismiss-error` | 收起历史接入错误 | `POST /api/domain-onboarding/dismiss-error` | — |
| `lanbridge online domain-onboarding-finish` | 结束域名接入跟踪 | `POST /api/domain-onboarding/finish` | — |
| `lanbridge online domain-onboarding-help` | 下载域名接入说明 | `GET /api/domain-onboarding/help` | — |
| `lanbridge online domain-onboarding-oauth` | 发起阿里云官方授权 | `POST /api/domain-onboarding/oauth` | — |
| `lanbridge online domain-onboarding-prepare` | 预览域名接入变更 | `POST /api/domain-onboarding/prepare` | — |
| `lanbridge online gateway-port` | 设置转发网关端口 | `POST /api/gateway-port` | — |
| `lanbridge online gateway-retry` | 恢复本机网关 | `POST /api/gateway-retry` | — |
| `lanbridge control` | 控制已核验的本机实例 | `POST /api/launcher/control` | `stop/restart --instance INSTANCE_ID` |
| `lanbridge online local-login-approve` | 确认或拒绝本机登录请求 | `POST /api/local-login/approve` | — |
| `lanbridge online local-login-browsers` | 列出本机授权浏览器 | `GET /api/local-login/browsers` | — |
| `lanbridge online local-login-cancel` | 取消本机登录请求 | `POST /api/local-login/cancel` | — |
| `lanbridge online local-login-open` | 打开本机登录确认页 | `POST /api/local-login/open` | — |
| `lanbridge online local-login-poll` | 查询并领取本机登录会话 | `POST /api/local-login/poll` | — |
| `lanbridge online local-login-request` | 查看本机登录请求 | `GET /api/local-login/request/{request_id}` | `--request-id ID` |
| `lanbridge online local-login-start` | 发起本机登录请求 | `POST /api/local-login/start` | — |
| `lanbridge online login` | 登录管理员 | `POST /api/login` | — |
| `lanbridge online logout` | 注销管理员会话 | `POST /api/logout` | — |
| `lanbridge online change-password` | 修改管理员密码 | `POST /api/password` | — |
| `lanbridge online public-client` | 启用或关闭公网转发列表 | `POST /api/public-client` | — |
| `lanbridge online restart` | 重启 LanBridge | `POST /api/restart` | — |
| `lanbridge online settings` | 修改平台配置与人类验证设置 | `POST /api/settings` | — |
| `lanbridge online setup` | 初始化管理员 | `POST /api/setup` | — |
| `lanbridge online shutdown` | 关闭 LanBridge | `POST /api/shutdown` | — |
| `lanbridge online save-site` | 保存网站、静态托管与访问策略 | `POST /api/sites` | — |
| `lanbridge online site-pause-mode` | 设置本机暂停与 Cloudflare 阻断方式 | `POST /api/sites/{site_id}/pause` | `--site-id ID` |
| `lanbridge online site-probe` | 探测源站 | `POST /api/sites/{site_id}/probe` | `--site-id ID` |
| `lanbridge online state` | 查看全部状态和访问防护统计 | `GET /api/state` | — |
| `lanbridge online tokens` | 列出临时管理令牌 | `GET /api/temporary-tokens` | — |
| `lanbridge online issue-token` | 签发临时管理令牌 | `POST /api/temporary-tokens` | — |
| `lanbridge online token-reveal` | 查看已签发的临时 Token | `POST /api/temporary-tokens/{token_id}/reveal` | `--token-id ID` |
| `lanbridge online token-revoke` | 撤销临时 Token | `POST /api/temporary-tokens/{token_id}/revoke` | `--token-id ID` |
| `lanbridge online token-login` | 使用临时 Token 登录 | `POST /api/token-login` | — |
| `lanbridge online add-zone` | 接入域名 | `POST /api/zones` | — |
| `lanbridge online zones-default` | 设置默认域名 | `POST /api/zones/{zone_id}/default` | `--zone-id ID` |
| `lanbridge online zones-remove` | 移除域名 | `POST /api/zones/{zone_id}/remove` | `--zone-id ID` |

通用 `online api` 与旧快捷命令继续保留。Cloudflare/阿里云官方授权仍需用户在官方页面确认，不能用 CLI 绕过。
