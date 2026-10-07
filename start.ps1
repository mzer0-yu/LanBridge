param([switch]$NoDialog)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host '请先运行 install.ps1 创建项目独立虚拟环境。'
    Read-Host '按 Enter 关闭'
    exit 1
}
if ($NoDialog) {
    & $python run.py serve --open-browser
    exit $LASTEXITCODE
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'LanBridge 启动器'
$form.ClientSize = New-Object System.Drawing.Size(440, 285)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.AutoScaleMode = 'Dpi'
$form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)
$form.BackColor = [System.Drawing.Color]::FromArgb(248, 250, 249)
function Add-Label($text, $x, $y, $width, $height) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $text
    $label.Location = New-Object System.Drawing.Point($x, $y)
    $label.Size = New-Object System.Drawing.Size($width, $height)
    $form.Controls.Add($label)
}
Add-Label 'LanBridge' 24 20 380 30
Add-Label '选择本机端口，启动成功后自动保存。' 24 55 390 28
Add-Label '管理台端口' 24 102 170 26
Add-Label '转发网关端口' 236 102 180 26
$adminPort = New-Object System.Windows.Forms.NumericUpDown
$gatewayPort = New-Object System.Windows.Forms.NumericUpDown
$adminPort.Location = New-Object System.Drawing.Point(24, 132)
$gatewayPort.Location = New-Object System.Drawing.Point(236, 132)
foreach ($inputBox in @($adminPort, $gatewayPort)) {
    $inputBox.Minimum = 1024
    $inputBox.Maximum = 65535
    $inputBox.Size = New-Object System.Drawing.Size(180, 32)
    $form.Controls.Add($inputBox)
}
Add-Label '仅监听本机；端口冲突时可在这里修改后重试。' 24 176 390 26
$start = New-Object System.Windows.Forms.Button
$start.Text = '启动并打开管理台'
$start.Location = New-Object System.Drawing.Point(220, 222)
$start.Size = New-Object System.Drawing.Size(196, 40)
$start.BackColor = [System.Drawing.Color]::FromArgb(36, 109, 86)
$start.ForeColor = [System.Drawing.Color]::White
$start.FlatStyle = 'Flat'
$start.DialogResult = 'OK'
$form.Controls.Add($start)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = '取消'
$cancel.Location = New-Object System.Drawing.Point(112, 222)
$cancel.Size = New-Object System.Drawing.Size(96, 40)
$cancel.DialogResult = 'Cancel'
$form.Controls.Add($cancel)
$form.AcceptButton = $start
$form.CancelButton = $cancel
try {
    $statusText = & $python run.py status
    if ($LASTEXITCODE -ne 0) { throw '无法读取本机配置，请查看终端提示。' }
    $status = $statusText | ConvertFrom-Json
    $adminPort.Value = $status.result.settings.admin_port
    $gatewayPort.Value = $status.result.settings.gateway_port
    if ($status.result.pending_gateway_port) { $gatewayPort.Value = $status.result.pending_gateway_port }
    while ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        if ($adminPort.Value -eq $gatewayPort.Value) {
            [System.Windows.Forms.MessageBox]::Show('管理台与转发网关端口必须不同。', 'LanBridge', 'OK', 'Warning') | Out-Null
            continue
        }
        & $python run.py serve --open-browser --admin-port ([int]$adminPort.Value) --gateway-port ([int]$gatewayPort.Value)
        if ($LASTEXITCODE -eq 0) { break }
        [System.Windows.Forms.MessageBox]::Show('启动失败。请查看终端中的具体提示；如端口被占用，请修改后重试。若平台已运行，请先正常退出原实例。', 'LanBridge', 'OK', 'Warning') | Out-Null
    }
} catch {
    Write-Host $_.Exception.Message
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'LanBridge', 'OK', 'Error') | Out-Null
} finally {
    $form.Dispose()
}
