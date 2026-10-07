# LanBridge 主目录迁移

迁移日期：2026-10-07。

主目录：`<项目目录或本地归档目录>`。

从此目录运行 `start.cmd` 或 `start.ps1`；CLI 和 MCP 也使用此目录中的 run.py、mcp_server.py 及 .venv/Scripts/python.exe。源码、测试、文档、Git 历史及未提交修改均保留；正式配置、日志、Windows 加密凭据、连接器和依赖一起迁移。端口、网站和公网域名保持原值。

检查：数据库完整性、已有凭据解密、工具和凭据文件内容一致、Python 依赖一致性、网站数量、管理页和转发页返回 200、连接器恢复运行。76 个依赖目录链接修正为新路径；虚拟环境激活脚本和命令入口重新生成。

旧项目归档到工作区 trash/lanbridge-before-relocation，作为回退副本，不是运行主目录。旧测试产物也保留在此副本中。不完整工具副本在 trash/lanbridge-relocation-incomplete-bin。均未直接删除。
