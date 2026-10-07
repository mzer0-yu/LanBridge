# LanBridge

**把局域网网页带到公网，在一个管理台里完成发布与访问控制。**

LanBridge 是面向 Windows 的自托管 Cloudflare Tunnel 管理工具。把 NAS 面板、开发服务、实验设备网页或内部工具映射到自己的公网子域名，集中管理网站、连接器和访问权限。无需在路由器上配置公网端口转发，也无需修改源站网页来加入人类验证。

**首个正式版本：v1.0.0** · [下载 Release](https://github.com/mzer0-yu/LanBridge/releases/tag/v1.0.0) · [使用指南](docs/user-guide.md) · [反馈问题](https://github.com/mzer0-yu/LanBridge/issues)

> 采用 [MIT 许可证](LICENSE)，欢迎使用、修改、分享和参与开发。本项目不是 Cloudflare 官方产品。

![LanBridge 管理台示例](docs/assets/overview.png)

## 能做什么

- **网站集中管理**：一个子域名对应一个局域网源站，支持多域名、多网站、HTTP / HTTPS 和 WebSocket / WSS。
- **发布流程一体化**：接入 Cloudflare 账户，准备 Tunnel、DNS 与 Turnstile，预览变更、发布后核验。
- **网页访问保护**：独立人类验证页、可选访问口令、国家/IP 范围和请求限速，源站无需嵌入验证代码。
- **随时暂停和恢复**：暂停单个网站的转发，保留配置；管理本机 cloudflared 连接器。
- **临时授权协作**：给协作者或 Agent 签发有期限、按范围授权的管理令牌，支持查看使用记录和撤销。
- **适合自动化**：网页、CLI、HTTP API、MCP 与 Agent 操作说明共用业务核心。
- **本机管理与可选公网入口**：管理员密码修改等操作仅限本机；公网转发列表页面可以单独关闭。
- **可控的操作日志**：按容量保留记录，默认上限 10 MB，支持 JSONL 导出和日志 URL。

## 适合哪些场景

| 场景 | 示例 |
| --- | --- |
| 家庭与个人工具 | 自己维护的 NAS 网页、家庭仪表盘、个人 Web 服务 |
| 开发与协作 | 临时演示开发服务、远程查看测试界面 |
| 设备与实验室 | 访问局域网设备网页、实验工具或监控面板 |
| Agent 管理 | 通过受限临时令牌让 Agent 管理网站及发布流程 |

LanBridge 转发的是网页和所选协议流量，不提供通用 VPN 或任意 TCP 端口隧道。源站自身的登录和权限仍由源站应用负责。

## 开始使用

准备一台能持续运行的 Windows 电脑、**Python 3.12+**、Cloudflare 账户，以及已接入 Cloudflare 的域名。需要联网安装依赖、连接 Cloudflare；实际网站是否可用也取决于本机与源站在线状态。

1. 从 [v1.0.0 Release](https://github.com/mzer0-yu/LanBridge/releases/tag/v1.0.0) 下载源码包并解压，或克隆仓库。
2. 在项目目录打开 PowerShell，安装依赖：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

   如果 `python` 不在 PATH，用 `-PythonPath "C:\Path\To\python.exe"` 指定 Python。

3. 双击 **`start.cmd`**，在启动器中确认或修改管理台端口，按需勾选“启动后打开管理台”，点击启动。管理台端口成功绑定后按选项决定是否打开浏览器；网关冲突可进入管理台修改后重试，首次创建管理员账户，使用独立的长密码。
4. 在“账户与配置”接入 Cloudflare。可使用浏览器授权，或提供所需权限的 API Token；凭据在本机保存。
5. 到“网站转发”添加网站，例如 `demo.example.com` → `http://192.168.1.20:8080`，按需要设置访问保护，然后启动连接器并核查连接。
6. 用外网浏览器访问自己的子域名，验证网页、应用登录和需要的 WebSocket 功能。

cloudflared 路径可以留空，平台可检测或从官方发布下载连接器。首版提供 **Python 源码发布包**，不是独立的免安装 EXE。

![网站转发与访问策略示例](docs/assets/forwarding.png)

*截图全部使用虚构域名和测试配置，不是可访问的在线演示。*

## 它如何工作

```text
外网访客 → Cloudflare → Cloudflare Tunnel → LanBridge 本机网关
                                               ↓ 验证与访问策略
                                          局域网网页 / 服务

本机管理员 → LanBridge 管理台 → Cloudflare API 与本机配置
```

默认管理台监听 `127.0.0.1:8890`，转发网关监听 `127.0.0.1:8891`。正常使用无需把这两个端口直接暴露到外网。只有显式添加 LanBridge 公网入口后才启用远程管理模式。

## 常见问题

**需要公网 IP 或路由器端口映射吗？** 通过 Cloudflare Tunnel 建立连接，不要求路由器入站端口映射；需要能访问 Cloudflare 的网络。

**支持 Linux/macOS 吗？** 首版主要运行和验证目标是 Windows。代码有部分其他平台分支，但不把它们宣称为已完成跨平台验收。

**关闭浏览器会停止转发吗？** 不会。运行服务和连接器继续工作；“退出管理员”只注销会话，“退出 LanBridge”会停止平台及其启动的连接器。

**能给朋友直接用吗？** 可以。项目采用 MIT 许可证，朋友可以按安装说明使用，也可以修改和分享；须保留版权与许可声明。

**会自动开机启动吗？** 首版不默认安装 Windows 服务，也不默认设置登录自启。

**转发列表和管理台有什么区别？** `/client` 是只读入口列表，`/admin` 是管理台。公网列表可关闭；本机访问及管理台不因此关闭。

## 文档与开发

- [使用指南与 API / CLI / MCP 说明](docs/user-guide.md)
- [Agent 启动、后台就绪确认与实例控制](docs/agent-startup.md) · [Agent 操作 Skill](skills/lanbridge/SKILL.md)
- [开发规则](AGENTS.md) · [当前验证状态](docs/review-status.md) · [浏览器检查](tests/ui/README.md)
- [性能测量与本地打包](docs/performance-and-release.md)
- [v1.0.0 发布说明](docs/releases/v1.0.0.md) · [安全说明](SECURITY.md)
- [已修复问题](docs/fixed-issues.md) · [检查历史](docs/history.md)

```text
lanbridge/   后端、网关、业务和存储
ui/          管理台与转发列表
scripts/     维护工具
skills/      Agent 操作说明
tests/       后端、前端与隔离浏览器检查
docs/        用户文档、开发记录和示例截图
data/        本机配置、加密凭据与日志（不提交）
bin/         本机连接器（不提交）
```

## English overview

LanBridge is a Windows-first, self-hosted control panel for exposing LAN web services through Cloudflare Tunnel. It combines website mappings, connector management, Turnstile verification, access policies, temporary scoped management tokens, and CLI / HTTP API / MCP interfaces.

The first release is source-based and requires Python 3.12+, a Cloudflare account and a domain. Licensed under the [MIT License](LICENSE). The Windows launcher lets you choose the management port and whether to open the browser. Configure the forwarding gateway port inside the management UI. Agents can use the CLI for startup and instance control; see the [Agent startup guide](docs/agent-startup.md).
