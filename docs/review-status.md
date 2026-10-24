# LanBridge 当前检查状态

更新日期：2026-10-24。开发与运行规则见 [AGENTS.md](../AGENTS.md)。这里只记录当前结论；历次复现、修改和归档证据见 [检查历史](history.md)，机制见 [已修复问题](fixed-issues.md)。

## 当前结论

本轮检查清单内没有已确认且尚未修复的问题。此结论限定于下述源码审查、隔离回归和少量健康请求，不代表没有未知漏洞。

- 在线 CLI：`lanbridge.cmd` / `python -m lanbridge` 提供 71 项具名在线操作，根 control 对应另一项内部实例控制，共覆盖 72 项管理入口。参数、权限及业务处理复用现有后端；使用见 [CLI 指南](cli.md) 与 [全部命令](cli-commands.md)。命令已加入当前用户 PATH。
- CLI 会话与输出：Cookie/CSRF 按地址以 Windows DPAPI 加密保存；跨进程锁内校验版本，迟到请求不覆盖新登录或恢复已清除缓存。未变化的查询不重写会话。秘密隐藏输入、输出遮罩、参数错误不回显原值；导出支持 CSRF 更新，文件成功关闭后才标记完成，异常不自动重复远端写入。
- 本轮新增修复：旧式 CLI 先取得独占运行锁，再初始化数据库；初始化失败仍释放锁。status 使用只读 SQLite 快照，不创建目录、保险库或日志，不迁移旧库，未初始化时返回明确提示。旧网站默认字段由与 API 共用的纯函数规范化。
- DNS 与源站：完整管理员明确确认后才迁移同账户、已离线且无连接的旧隧道 CNAME。任务与配置快照绑定、写前复查、先备份、写后核验；未知结果不盲目回滚。备份只淘汰已核验记录。显式重试始终核验云端。quant 已启用公网 Host 模式，未关闭源站校验，实际访问正常。
- 网站与静态托管：HTTP/HTTPS、WebSocket/WSS、静态主页 file URL 和 LanBridge 自身目标沿用统一策略。静态目录新增/变更仍限本机完整管理员，公网隐藏本地路径，拒绝越界、私有目录、隐藏文件及链接。普通网站默认使用源站 Host。
- 授权与发布：账户/域名归属、旧任务代次、原子保存、Origin/CSRF、本机/公网和临时权限继续保留。OAuth 官方页面仍需用户确认；后台任务的本地保存、云端完成和待核对状态明确区分。
- 访问防护：人类验证记忆默认一天、可配 0–30 天；策略变更撤销旧凭证，单改名称不撤销。按需验证、异常冷却、静态资源独立额度、网关并发/正文/超时/WebSocket 限制及最近 24 小时滚动统计保持。已暂停且已发布网站达到持续异常阈值时，每轮暂停自动尝试一次精确域名云端阻断；失败保留本机暂停，恢复时核验解除规则，仍需 WAF 权限。
- 视觉：沿用既定字体、颜色、间距、折叠与控件规范。桌面/手机渲染及自动几何、反馈与对比度检查未复现本轮需调整的问题，没有为检查而重新设计正常界面。

## 最终验证

| 检查 | 当前结果 | 证据 |
| --- | --- | --- |
| 最终完整后端 | 840 passed、1 skipped、1 warning，208.77 秒 | `.test-artifacts/comprehensive-final-20261024-python.log` |
| Node 逻辑 | 100 passed | `.test-artifacts/comprehensive-20261024-node.log` |
| 完整隔离浏览器 | 39 组通过 | `.test-artifacts/comprehensive-20261024-ui.log` |
| Windows 启动器 | 两组通过 | `.test-artifacts/comprehensive-20261024-launcher*.log` |
| CLI 初始化专项 | 6 项最终边界通过；相关专项 204 passed | `.test-artifacts/offline-cli-verified.log`、`offline-cli-fixed.log` |
| 依赖一致性与公告 | pip check 通过；OSV 查询 27 个锁定包未返回已知漏洞记录 | `.test-artifacts/comprehensive-20261024-dependencies.log`、`dependencies.json` |
| 正式实例健康 | 只读 status 成功、本机 bootstrap 200、quant 公网 GET 200 | 本轮少量只读检查，未写配置 |
| 格式 | git diff --check 通过 | 本轮最终检查 |

唯一跳过项为 Windows 符号链接创建权限；唯一警告为既有 Starlette/TestClient 对 httpx 的弃用提示，未为隐藏警告升级依赖。依赖版本为 certifi 2026.10.19、opentelemetry-api 1.45.1，其他锁定项保持；公告扫描只覆盖已知问题。

查看了 320px 逐网站防护统计与 1440px 网站编辑截图；完整浏览器覆盖六页、折叠展开、复选框、弹窗、等待/失败/成功、长文本、静态目标、Host 选项、暂停及权限状态。包含 14 个实际对比度场景；不声称全站无障碍认证。

## 覆盖范围与限制

本轮源码复查及回归覆盖 CLI 在线/离线、会话与参数、后台发布/DNS 迁移、暂停/恢复和自动阻断、访问控制、统计保存、静态路径与链接边界、HTTP/WS 网关资源限制、界面状态和布局。已有修复的失败/重试/跨会话与原子保存证据保留。

自动测试使用隔离数据、模拟云端与本机测试服务；未进行真实攻击、正式站点负载或手机网络验收。正式站点只做少量 GET 健康检查，本轮没有修改正式 DNS 或网站策略。应用限流不保证抵御任意规模 DDoS，仍依赖 Cloudflare 边缘和源站自身保护。

源码保持 `1.1.0-dev`，此前推送基线为 adca62d。本次源码同步包含 DNS、Host、完整 CLI、本轮修复及 CLI 功能一致性开发规范，不创建 Release。

## 证据与持续流程

最新完整后端隔离数据：`.test-artifacts/python-comprehensive-final-20261024/`；最新截图：`.test-artifacts/ui/`；本轮完整日志使用 comprehensive 前缀。以往的专项、依赖修复、性能测量及归档索引见 [检查历史](history.md) 和 [性能记录](performance-and-release.md)。

本轮没有删除或移动 data、bin、.venv、有效源码、测试或产物。此前项目外 trash 归档保留清单，永久删除由用户执行。

个人 `website-troubleshooting`（排查网站问题）串联视觉与后端专项，要求复现后修复、验证并记录边界。本轮未修改个人 skill，也未设置后台定时检查或自动发布。
