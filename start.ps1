$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw '请先运行 install.ps1 创建项目独立虚拟环境。'
}
& $python run.py serve --open-browser
if ($LASTEXITCODE -ne 0) {
    Read-Host '启动失败，请查看上方提示；按 Enter 关闭'
}
