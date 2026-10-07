param([string]$PythonPath = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $PythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要可用的 Python 3.12+' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
Write-Host '安装完成，双击 start.cmd 启动 LanBridge；管理端口和是否打开管理台可在启动器选择。'
