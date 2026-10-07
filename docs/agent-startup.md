# Agent 启动与实例控制

图形启动器 `start.cmd` 面向人工操作；Agent 使用同一套运行核心的 `run.py` CLI，无需点击窗口。以下示例为 Windows PowerShell，在实际项目目录执行，使用项目 `.venv`。先确认用户要操作的项目/数据目录；启动、关闭、重启只执行用户授权的操作。

启动器左下角的“Agent / CLI 说明”可用记事本打开本目录这份文档，无需先启动服务。右键该入口（或聚焦后按 Shift+F10）可复制文件路径或文件 URL；`file:///` 地址仅适用于能访问该路径的本机用户，不是公网分享链接。

图形启动器在同一 Windows 用户会话、同一项目目录内只保留一个窗口；再次运行 `start.cmd` 会请求唤回已有窗口。不同安装目录可分别打开。右上角关闭仅关闭启动器，后台 LanBridge 服务继续运行；此窗口限制不影响 Agent 的 `run.py` CLI。

## 命令入口

```powershell
Set-Location -LiteralPath 'C:\Users\yu\Desktop\Mobo\services\LanBridge'
.\.venv\Scripts\python.exe run.py capabilities
.\.venv\Scripts\python.exe run.py --help
.\.venv\Scripts\python.exe run.py serve --help
.\.venv\Scripts\python.exe run.py control --help
```

路径是本机示例，其他安装位置替换为实际目录。`--data-dir` 是全局参数，必须放在子命令前；省略时使用 `run.py` 所在项目的 `data/`，与调用者工作目录无关。

| 目标 | 在项目目录调用 | 完成判断 |
| --- | --- | --- |
| 列出运行实例 | `.\.venv\Scripts\python.exe run.py instances` | JSON `instances` 列表且 `error` 为空；同时检查退出码 |
| 查看保存配置 | `.\.venv\Scripts\python.exe run.py status` | `lanbridge-result/v1` JSON；不是连接器实时运行状态 |
| 前台启动，不开浏览器 | `.\.venv\Scripts\python.exe run.py serve` | 持续运行；后台使用下文结果文件判断就绪 |
| 启动并打开管理台 | `.\.venv\Scripts\python.exe run.py serve --open-browser` | 管理台就绪后请求打开系统浏览器 |
| 指定管理端口 | `.\.venv\Scripts\python.exe run.py serve --admin-port 8890` | 成功绑定才保存端口；冲突返回失败，不停止占用端口的其他应用 |
| 打开已有管理台 | `.\.venv\Scripts\python.exe run.py open-existing` | 退出码 0 表示核验成功并请求打开；1 表示未核验到实例 |
| 正常关闭实例 | `.\.venv\Scripts\python.exe run.py --data-dir <绝对数据目录> control stop --instance <当前实例身份>` | 退出码 0、JSON `ok:true`、`completed:true` |
| 重启实例 | 上一命令把 `stop` 换为 `restart` | 同上，并返回新管理端口；重新检测实例身份 |

`start.cmd` 不解析 CLI 参数。`start.ps1 -NoDialog` 是保留的前台诊断入口，会请求打开管理台；Agent 要选择端口、后台运行或控制是否打开浏览器，应直接使用上述 CLI。

## 启动前先检测

`instances` 返回项目目录、绝对数据目录、管理端口、`current`、`instance`、`controllable` 等，不返回控制密钥。不要根据监听端口判断“就是 LanBridge”，也不要自动选择列表第一项。

同一数据目录只允许一个实例。该目录已有已核验实例时，`serve` 复用它；带 `--open-browser` 才请求打开浏览器。复用不会修改端口或重启连接器。检测失败或返回 `error` 时，不把它当成“没有实例”。旧实例可能没有可用的 `instance`/控制能力，应按 `controllable` 判断；需要用户从旧管理台正常退出后重新加载新版。

启动的管理端口默认为保存值或待生效值；`--admin-port` 显式指定时覆盖本次选择。转发网关冲突不阻止进入管理台，可在管理台恢复。连接器是否自动启动按“启动时自动连接”设置和配置就绪状态决定；管理台启动成功不等于公网转发已验证可用。

## 后台启动并确认结果

Agent 的后台启动使用 `pythonw.exe`，不保留终端窗口。只创建进程不能证明服务就绪，需等待独立结果文件。

```powershell
$projectRoot = (Resolve-Path -LiteralPath '.').Path
$dataDir = Join-Path $projectRoot 'data'
$resultFile = Join-Path ([IO.Path]::GetTempPath()) ('lanbridge-start-' + [Guid]::NewGuid().ToString('N') + '.json')
$launchArgs = @(
    ('"{0}"' -f (Join-Path $projectRoot 'run.py')),
    '--data-dir', ('"{0}"' -f $dataDir),
    'serve', '--startup-result', ('"{0}"' -f $resultFile)
)
# 如需指定管理端口：$launchArgs += @('--admin-port', '8890')
# 仅用户要求打开管理台时：$launchArgs += '--open-browser'
$child = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\pythonw.exe') -ArgumentList $launchArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $resultFile) -and -not $child.HasExited -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (-not (Test-Path -LiteralPath $resultFile)) {
        throw '启动结果尚未确认；重新检测实例和诊断日志，不要立即重复启动'
    }
    $launchResult = Get-Content -LiteralPath $resultFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Remove-Item -LiteralPath $resultFile
    if (-not $launchResult.ok) { throw $launchResult.error }
    $launchResult | ConvertTo-Json -Compress
} finally {
    $child.Dispose() # 仅释放本地进程句柄，不停止后台服务
}
```

结果文件使用无凭据 JSON：新启动成功含 `ok:true, port`，已运行复用含 `ok:true, reused:true`，失败含 `ok:false, error`。发现 `reused` 或启动超时后，用 `instances` 再确认对应数据目录及管理端口；不要把打印的一行地址、进程仍存在或网页有响应单独当作启动成功。超时可能仍在启动，保留后续结果文件用于确认，禁止强杀不明实例。

## 选择目标并关闭或重启

以下以关闭为例。运行命令前，核对目标数据目录；重启仅把 `stop` 换为 `restart`，不要顺序执行两个示例。

```powershell
$projectRoot = (Resolve-Path -LiteralPath '.').Path
$dataDir = Join-Path $projectRoot 'data'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$listing = & $pythonPath (Join-Path $projectRoot 'run.py') instances | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $listing.error) { throw '实例检测失败' }
$target = @($listing.instances | Where-Object {
    [String]::Equals($_.data, $dataDir, [StringComparison]::OrdinalIgnoreCase)
})
if ($target.Count -ne 1) { throw '目标数据目录未唯一匹配，请重新确认' }
if (-not $target[0].controllable) { throw '该实例不支持 CLI 启停控制' }
& $pythonPath (Join-Path $projectRoot 'run.py') --data-dir $target[0].data control stop --instance $target[0].instance
if ($LASTEXITCODE -ne 0) { throw '操作未确认完成，重新检测目标实例' }
```

`instance` 是运行身份，不是秘密；真正控制凭据由 CLI 从受保护数据目录内部使用。不要读取/打印 `data/runtime.json` 的控制密钥、保险库、数据库秘密，也不要传密钥到命令行。重启后身份变化，后续控制必须重新检测。控制命令等待正常退出/新实例就绪；超时或身份不匹配先核对状态，不盲目重复操作，不根据 PID 强杀。

该控制通道属于同一 Windows 用户的本机运行管理，不需要浏览器管理员 Cookie，不向公网 Agent 或临时管理 Token 开放。正常关闭同时停止目标管理台、网关和连接器；重启应用待生效端口、保留连接器原先运行或停止状态。当前重启流程会请求打开管理台；“启动时不开浏览器”仅指普通 `serve` 不加 `--open-browser`，不代表重启也静默。

## 输出与业务操作

- `capabilities`、`status`、`instances`、`control` 的 JSON 结构不同；按对应字段解析，不假定所有命令都是 `lanbridge-result/v1`。`instances` 即使退出码为 0，也要检查 `error`。
- `serve` 前台长期运行，stdout 可能包含地址或复用说明；后台确认使用 `--startup-result`，不能依赖 `pythonw.exe` 的 stdout。
- 管理服务运行时，网站/Cloudflare 配置修改使用已认证 API 或 MCP；普通 CLI 配置写命令需先停止服务。`instances`、`open-existing`、`control` 是运行管理例外，可在服务运行时调用。
- 打开管理台不等于已登录。远程 Agent 使用授权的公网 API/临时 Token；需要本机启动、端口或进程控制时由本机 Agent 执行。
