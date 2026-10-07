# 隔离浏览器检查

在项目目录运行 `node tests/ui/run.cjs`。需要 Node.js、Playwright 和已安装的 Microsoft Edge；本次验证使用 Playwright 1.62.1。未安装 Playwright 时可运行 `npm install --no-save --package-lock=false playwright@1.62.1`，无需下载额外浏览器。

这些检查通过 `page.route` 拦截全部 `lb.preview` 请求，使用 `fixture.js` 中的虚构配置，不连接正式 Cloudflare 账户，不读取 `data/`，也不修改正式网站或凭据。脚本可以从任意工作目录运行。

覆盖六个页面、网站编辑、登录切换和失败反馈、临时授权、复制和撤销、日志分页和展开、导航定位、桌面及手机溢出，以及 HTML 文本转义。`disclosure-style-check.js` 检查全部 19 个静态折叠项在收起、展开时的标题边框，并验证嵌套账户配置、令牌管理、自定义路径和密钥区域。截图统一写入项目 `.test-artifacts/ui/`，不提交到 Git。

浏览器检查补充后端和 Node 测试，不代替真实公网部署验收。API 返回值、鉴权和资源限制由 Python 测试验证。

刷新检查涵盖快速重复点击、动画与成功反馈、失败重试及尺寸稳定；HTTP 默认接入的重复脚本已合并进导航检查，统一入口当前运行 12 组浏览器检查，包含登录初始化、公网转发列表开关及关闭提示页。

全页面布局检查额外覆盖 760px 和 900px，检查移动端/桌面转换处的排版；全部宽度为 1440、900、760、390px。

`content-layout-check.js` 补查 320、760、1440px 下的长网站名称、长地址及表单字体一致性；令牌值保留等宽字体。
