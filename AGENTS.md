# LanBridge 开发说明

## 入口与职责

在项目根目录（本文件所在目录）开发，不使用旧源码副本。先读 [README.md](README.md) 和 [当前检查状态](docs/review-status.md)，按任务查阅 [已修复问题](docs/fixed-issues.md)。[skills/lanbridge/SKILL.md](skills/lanbridge/SKILL.md) 是操作平台的说明，开发不应默认执行其中的云端操作。

- `lanbridge/`：后端、网关、业务和存储；`ui/`：前端静态文件。
- `tests/`：有效回归；`tests/ui/`：隔离浏览器检查；`scripts/`：维护工具。
- `docs/`：文档；`.test-artifacts/`：可归档的测试产物。
- `data/`、`bin/`、`.venv/`：正式运行数据、连接器、依赖，不因被 Git 忽略而删除或移动。

## 修改原则

先确认触发条件与调用入口，再修改。用户指令优先；不把可选优化、加固或测试数量算成新 bug。不宣称测试通过就证明公网没有漏洞。不要重置用户的已有修改，不默认提交或推送。用户已明确要求先在本地确认；未经用户新的明确授权，不推送 GitHub 公有仓库、不修改远端标签或 Release。

保护现有权限边界：本机专属操作不对公网开放；临时令牌按授权范围访问；Cookie 写操作保留 Origin/CSRF 检查；公网入口保留 HTTPS、访问策略、验证和限速。不要为方便测试关闭这些措施。

本机关联配置和凭据保持原子提交；后台和前端异步结果检查会话/代次。任何测试不得向输出、截图或文档写入真实密码、令牌、Cookie、保险库或 SQL 凭据内容。

## UI 约定

需要统一 UI 样式：按共同规范集中解决不一致，确认后将尺寸、间距、字体和布局作为稳定基准，不在后续检查中来回调整正常控件。新功能沿用该基准；发现偏离规范、可复现的排版/交互问题，或用户明确要求时再修改。不要把统一样式理解为禁止统一，也不要以优化为由反复更改已确认的方案。发生视觉变化需报告位置、原因及前后差异。

沿用现有配色与 Segoe UI / Microsoft YaHei 字体栈；代码和令牌值使用等宽字体。保持按钮尺寸和状态反馈稳定。普通操作按钮共用 13px 字号、20px 行高、桌面至少 36px/手机至少 40px 高度；同一操作列对齐宽度。图标、文本入口及分段选择按其用途单独设计，不新增零散字号和尺寸覆盖。成功反馈优先原位显示，不增加浏览器底部冒泡；折叠标题不添加无必要分割横线。检查手机、断点、长文本、键盘访问和减少动画设置。

修改静态脚本/样式时更新对应 HTML 资源版本，避免旧缓存。保留用户输入和已保存结果，不让后续状态刷新失败误报保存失败。

配置类令牌、密钥和网站口令沿用 `.secret-input` 遮罩单行控件，不使用登录密码输入框；非登录配置关闭自动完成。真正的登录/修改密码保留正确 autocomplete 语义。

短状态与单项校验提示不加句号（如“有未保存的修改”）；完整说明、操作后果和多句提示保留句号，不机械删除所有标点。

## 验证

在主目录运行；Python 使用项目 `.venv`，Node/Playwright 路径按当前环境定位，不安装或升级依赖来掩盖失败。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.test-artifacts/python
node --test tests/test_navigation.js tests/test_gate.js tests/test_local_login.js tests/test_client_refresh.js
node tests/ui/run.cjs
powershell -NoProfile -STA -ExecutionPolicy Bypass -File tests/test_launcher.ps1
powershell -NoProfile -STA -ExecutionPolicy Bypass -File tests/test_launcher_single_window.ps1
git diff --check
```

浏览器依赖和覆盖见 [tests/ui/README.md](tests/ui/README.md)。局部变化先运行相关检查；广泛变化、权限/存储机制变更或用户要求全面复查时运行完整回归。只读检查正式部署时不要提交配置；测试使用隔离数据和模拟上游，禁止默认操作正式 Cloudflare 资源。需要后端重载时，先确认运行路径和原连接器状态，再按用户授权执行并验证恢复。

## 文档与清理

`docs/review-status.md` 只保留当前结论和验证边界；新历史证据写入 `docs/history.md`；已确认缺陷按机制归并到 `docs/fixed-issues.md`，不要在多份文档重复新增计数。

缓存和确认过时的测试产物移入项目外由用户指定的 trash 目录的独立归档，附来源、数量和用途清单，由用户手动删除。Windows 移动前核对解析后的绝对源/目标路径在预期范围内，使用 LiteralPath。保留有效测试和各场景最新截图；对用途不明的文件先追踪引用，不盲删。主目录不要堆放临时维护脚本或一次性报告。
