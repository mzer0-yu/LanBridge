param([switch]$AddToPath)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'))) {
    throw '请先运行 install.ps1 安装项目依赖'
}
if ($AddToPath) {
    $cliDirectory = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
    $currentUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $pathEntries = @($currentUserPath -split ';' | Where-Object { $_ })
    if (-not ($pathEntries | Where-Object { $_.TrimEnd('\') -ieq $cliDirectory })) {
        [Environment]::SetEnvironmentVariable('Path', (($pathEntries + $cliDirectory) -join ';'), 'User')
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $cliDirectory })) {
        $env:Path = $env:Path + ';' + $cliDirectory
    }
    Write-Host '已加入当前用户 PATH；新终端可直接运行 lanbridge。'
}
Write-Host '命令行：.\lanbridge.cmd --help；运行中管理：.\lanbridge.cmd online --help'
