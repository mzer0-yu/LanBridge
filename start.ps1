param([switch]$NoDialog)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$pythonWindowless = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
if ($NoDialog) {
    if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 install.ps1 安装依赖。' }
    & $python run.py serve --open-browser
    exit $LASTEXITCODE
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
# Give the window its own taskbar group instead of inheriting PowerShell's identity.
if (-not ('LanBridgeLauncherShell' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Runtime.InteropServices;
public static class LanBridgeLauncherShell {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SetCurrentProcessExplicitAppUserModelID(string appID);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int processID);
}
public sealed class LanBridgeLauncherGate : IDisposable {
    private readonly Mutex mutex;
    private readonly EventWaitHandle activation;
    public bool IsOwner { get; private set; }
    public string Key { get; private set; }
    public LanBridgeLauncherGate(string directory) {
        string normalized = Path.GetFullPath(directory).TrimEnd('\\', '/').ToUpperInvariant();
        string identity = normalized + "|" + WindowsIdentity.GetCurrent().User.Value;
        using (SHA256 hash = SHA256.Create()) {
            Key = "Local\\LanBridge.Launcher." + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(identity))).Replace("-", "");
        }
        mutex = new Mutex(false, Key);
        try { IsOwner = mutex.WaitOne(0); }
        catch (AbandonedMutexException) { IsOwner = true; }
        try {
            activation = new EventWaitHandle(false, EventResetMode.AutoReset, Key + ".Activate");
            if (IsOwner) activation.Reset();
        } catch {
            if (IsOwner) mutex.ReleaseMutex();
            mutex.Dispose();
            throw;
        }
    }
    public void RequestActivation() {
        // Grant the existing process permission to foreground its own window.
        LanBridgeLauncherShell.AllowSetForegroundWindow(-1);
        activation.Set();
    }
    public bool TakeActivation() { return activation.WaitOne(0); }
    public void Dispose() {
        activation.Dispose();
        if (IsOwner) { mutex.ReleaseMutex(); IsOwner = false; }
        mutex.Dispose();
    }
}
"@
}
$launcherGate = New-Object LanBridgeLauncherGate($PSScriptRoot)
if (-not $launcherGate.IsOwner) {
    try { $launcherGate.RequestActivation() } finally { $launcherGate.Dispose() }
    return
}
try {
[LanBridgeLauncherShell]::SetCurrentProcessExplicitAppUserModelID('LanBridge.Launcher') | Out-Null
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$launcherIcon = New-Object System.Drawing.Icon((Join-Path $PSScriptRoot 'ui\lanbridge.ico'))
$form.Icon = $launcherIcon
$form.Text = 'LanBridge'
$form.ClientSize = New-Object System.Drawing.Size(600, 426)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.AutoScaleMode = 'Dpi'
$form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)
$form.BackColor = [System.Drawing.Color]::FromArgb(248, 250, 249)
$form.ForeColor = [System.Drawing.Color]::FromArgb(28, 55, 46)
function New-Label($text, $x, $y, $width, $height) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $text
    $label.Location = New-Object System.Drawing.Point($x, $y)
    $label.Size = New-Object System.Drawing.Size($width, $height)
    $form.Controls.Add($label)
    return $label
}
$brand = New-Object System.Windows.Forms.PictureBox
$brand.Location = New-Object System.Drawing.Point(24, 24)
$brand.Size = New-Object System.Drawing.Size(46, 46)
$brand.SizeMode = 'Zoom'
$brand.Image = [System.Drawing.Image]::FromFile((Join-Path $PSScriptRoot 'ui\lanbridge.png'))
$form.Controls.Add($brand)
$title = New-Label 'LanBridge' 84 23 330 34
$title.Font = New-Object System.Drawing.Font('Segoe UI', 20, [System.Drawing.FontStyle]::Bold)
$subtitle = New-Label '启动 LanBridge' 85 59 320 24
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(111, 129, 121)
$portLabel = New-Label '管理台端口' 24 101 132 24
$addressLabel = New-Label '管理台地址' 176 101 222 24
$inputPanel = New-Object System.Windows.Forms.Panel
$inputPanel.Location = New-Object System.Drawing.Point(24, 130)
$inputPanel.Size = New-Object System.Drawing.Size(132, 46)
$inputPanel.BackColor = [System.Drawing.Color]::White
$inputPanel.Add_Paint({param($sender, $eventArgs)
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(197, 211, 203))
    $eventArgs.Graphics.DrawRectangle($pen, 0, 0, $sender.Width - 1, $sender.Height - 1)
    $pen.Dispose()
})
$adminPort = New-Object System.Windows.Forms.TextBox
$adminPort.BorderStyle = 'None'
$adminPort.Location = New-Object System.Drawing.Point(12, 10)
$adminPort.Size = New-Object System.Drawing.Size(108, 30)
$adminPort.Font = New-Object System.Drawing.Font('Segoe UI', 14)
$adminPort.MaxLength = 5
$adminPort.AccessibleName = '管理台端口'
$adminPort.Text = '8890'
$inputPanel.Controls.Add($adminPort)
$form.Controls.Add($inputPanel)
$portValue = New-Label '8890' 24 140 132 30
$portValue.Font = New-Object System.Drawing.Font('Segoe UI', 14)
$portValue.Visible = $false
$address = New-Label 'http://127.0.0.1:8890/admin' 176 142 222 26
$address.ForeColor = $subtitle.ForeColor
$feedback = New-Label '启动后在后台运行，无需保留终端窗口。' 24 214 552 34
$feedback.ForeColor = $subtitle.ForeColor
$start = New-Object System.Windows.Forms.Button
$start.Text = '启动 LanBridge'
$start.Location = New-Object System.Drawing.Point(414, 130)
$start.Size = New-Object System.Drawing.Size(162, 46)
$start.BackColor = [System.Drawing.Color]::FromArgb(36, 109, 86)
$start.ForeColor = [System.Drawing.Color]::White
$start.FlatStyle = 'Flat'
$start.FlatAppearance.BorderSize = 0
$form.Controls.Add($start)
$openAfterStart = New-Object System.Windows.Forms.CheckBox
$openAfterStart.Text = '启动后打开管理台'
$openAfterStart.Location = New-Object System.Drawing.Point(414, 184)
$openAfterStart.Size = New-Object System.Drawing.Size(162, 24)
$openAfterStart.Checked = $true
$form.Controls.Add($openAfterStart)
$openAfterStart.Add_CheckedChanged({ Sync-LaunchState })
$form.AcceptButton = $start
$instancesLabel = New-Label '本机已运行实例' 24 254 420 26
$refreshInstances = New-Object System.Windows.Forms.Button
$refreshInstances.Text = '重新检测'
$refreshInstances.Location = New-Object System.Drawing.Point(470, 248)
$refreshInstances.Size = New-Object System.Drawing.Size(106, 32)
$form.Controls.Add($refreshInstances)
$instanceList = New-Object System.Windows.Forms.ListView
$instanceList.Location = New-Object System.Drawing.Point(24, 288)
$instanceList.Size = New-Object System.Drawing.Size(552, 78)
$instanceList.View = 'Details'
$instanceList.FullRowSelect = $true
$instanceList.MultiSelect = $false
$instanceList.HideSelection = $false
$instanceList.ShowItemToolTips = $true
$instanceList.BorderStyle = 'FixedSingle'
$instanceList.Columns.Add('管理端口', 85) | Out-Null
$instanceList.Columns.Add('运行目录', 363) | Out-Null
$instanceList.Columns.Add('位置', 80) | Out-Null
$form.Controls.Add($instanceList)
$instanceEmpty = New-Label '尚未检测实例' 25 320 550 38
$instanceEmpty.BackColor = [System.Drawing.Color]::White
$instanceEmpty.ForeColor = $subtitle.ForeColor
$instanceEmpty.TextAlign = 'MiddleCenter'
$instanceEmpty.BringToFront()
$openInstance = New-Object System.Windows.Forms.Button
$openInstance.Text = '打开选中管理台'
$openInstance.Location = New-Object System.Drawing.Point(410, 378)
$openInstance.Size = New-Object System.Drawing.Size(166, 32)
$openInstance.Enabled = $false
$form.Controls.Add($openInstance)
$stopInstance = New-Object System.Windows.Forms.Button
$stopInstance.Text = '关闭实例'
$stopInstance.Location = New-Object System.Drawing.Point(166, 378)
$stopInstance.Size = New-Object System.Drawing.Size(112, 32)
$stopInstance.Enabled = $false
$form.Controls.Add($stopInstance)
$restartInstance = New-Object System.Windows.Forms.Button
$restartInstance.Text = '重启实例'
$restartInstance.Location = New-Object System.Drawing.Point(288, 378)
$restartInstance.Size = New-Object System.Drawing.Size(112, 32)
$restartInstance.Enabled = $false
$form.Controls.Add($restartInstance)
foreach ($button in @($refreshInstances, $openInstance, $stopInstance, $restartInstance)) {
    $button.FlatStyle = 'Flat'
    $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(197, 211, 203)
    $button.BackColor = [System.Drawing.Color]::White
}
$agentGuide = New-Object System.Windows.Forms.LinkLabel
$agentGuide.Text = 'Agent / CLI 说明'
$agentGuide.Location = New-Object System.Drawing.Point(24, 378)
$agentGuide.Size = New-Object System.Drawing.Size(132, 32)
$agentGuide.TextAlign = 'MiddleLeft'
$agentGuide.LinkColor = $start.BackColor
$agentGuide.ActiveLinkColor = $form.ForeColor
$agentGuide.VisitedLinkColor = $agentGuide.LinkColor
$agentGuide.AccessibleDescription = '点击打开说明；右键或 Shift+F10 复制文件路径和文件 URL'
$form.Controls.Add($agentGuide)
function Open-AgentGuide {
    $guidePath = Join-Path $PSScriptRoot 'docs\agent-startup.md'
    if (-not (Test-Path -LiteralPath $guidePath -PathType Leaf)) {
        $feedback.Text = '未找到说明文件：docs/agent-startup.md'
        return
    }
    try {
        # Markdown may have no Windows file association; Notepad is always readable.
        Start-Process -FilePath (Join-Path $env:WINDIR 'System32\notepad.exe') -ArgumentList ('"{0}"' -f $guidePath)
    } catch {
        $feedback.Text = '无法打开说明，请查看本目录 docs/agent-startup.md'
    }
}
$agentGuide.Add_LinkClicked({ param($sender, $eventArgs)
    # LinkLabel raises LinkClicked for right-clicks too; reserve them for its menu.
    if ($eventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left -or $eventArgs.Button -eq [System.Windows.Forms.MouseButtons]::None) {
        Open-AgentGuide
    }
})
$guideMenu = New-Object System.Windows.Forms.ContextMenuStrip
$copyGuidePath = $guideMenu.Items.Add('复制文件路径')
$copyGuideUrl = $guideMenu.Items.Add('复制文件 URL（仅本机）')
$agentGuide.ContextMenuStrip = $guideMenu
$guideTip = New-Object System.Windows.Forms.ToolTip
$guideTip.SetToolTip($agentGuide, '点击打开；右键或 Shift+F10 复制文件路径 / URL')
function Set-GuideClipboard([string]$text) {
    [System.Windows.Forms.Clipboard]::SetText($text)
}
function Copy-AgentGuide([switch]$AsUrl) {
    $guidePath = Join-Path $PSScriptRoot 'docs\agent-startup.md'
    if (-not (Test-Path -LiteralPath $guidePath -PathType Leaf)) {
        $feedback.Text = '未找到说明文件：docs/agent-startup.md'
        return
    }
    try {
        $guidePath = [IO.Path]::GetFullPath($guidePath)
        $copyValue = if ($AsUrl) { ([Uri]$guidePath).AbsoluteUri } else { $guidePath }
        Set-GuideClipboard $copyValue
        $feedback.Text = if ($AsUrl) { '已复制文件 URL，仅适用于本机' } else { '已复制说明文件路径' }
    } catch {
        $feedback.Text = '复制失败，请重试；说明位于 docs/agent-startup.md'
    }
}
$copyGuidePath.Add_Click({ Copy-AgentGuide })
$copyGuideUrl.Add_Click({ Copy-AgentGuide -AsUrl })
$form.Add_Disposed({ $guideTip.Dispose(); $guideMenu.Dispose() })
$script:currentInstance = $null
function Sync-InstanceActions {
    $selected = if ($instanceList.SelectedItems.Count) { $instanceList.SelectedItems[0].Tag } else { $null }
    $openInstance.Enabled = (-not $script:busy) -and ($null -ne $selected)
    $stopInstance.Enabled = $restartInstance.Enabled = (-not $script:busy) -and ($null -ne $selected) -and $selected.controllable
}
function Sync-LaunchState {
    $running = $null -ne $script:currentInstance
    $adminPort.ReadOnly = $running
    $inputPanel.Visible = -not $running
    $portValue.Visible = $running
    if ($running) { $portValue.Text = $script:currentInstance.port.ToString() }
    $adminPort.BackColor = if ($running) { $form.BackColor } else { [System.Drawing.Color]::White }
    $inputPanel.BackColor = $adminPort.BackColor
    $start.Text = if ($script:busy) { $(if ($script:controlAction -eq 'stop') { '正在关闭…' } elseif ($script:controlAction -eq 'restart') { '正在重启…' } else { '正在启动…' }) } elseif ($running) { '打开管理台' } else { '启动 LanBridge' }
    $subtitle.Text = if ($running) { '本目录已运行' } else { '启动 LanBridge' }
    $openAfterStart.Visible = -not $running
    $openAfterStart.Enabled = (-not $script:busy) -and $script:dependenciesReady
    $start.Enabled = (-not $script:busy) -and $script:dependenciesReady
    if ($running) { $adminPort.Text = $script:currentInstance.port.ToString(); $adminPort.SelectionLength = 0 }
}
function Refresh-Instances($detected) {
    $refreshInstances.Enabled = $false
    $selectedData = if ($instanceList.SelectedItems.Count) { $instanceList.SelectedItems[0].Tag.data } else { $null }
    try {
        if ($null -eq $detected -or $detected.error) { throw '实例检测失败，请重新检测' }
        $instanceList.BeginUpdate()
        try {
            $instanceList.Items.Clear()
            $script:currentInstance = $null
            $preferred = $null
            $selectedItem = $null
            foreach ($instance in $detected.instances) {
                $item = New-Object System.Windows.Forms.ListViewItem($instance.port.ToString())
                $item.SubItems.Add($instance.project) | Out-Null
                $item.SubItems.Add($(if ($instance.current) { '本目录' } else { '其他目录' })) | Out-Null
                $item.ToolTipText = '运行目录：' + $instance.project + "`n数据目录：" + $instance.data
                $item.Tag = $instance
                $instanceList.Items.Add($item) | Out-Null
                if ($instance.current) { $script:currentInstance = $instance; $preferred = $item }
                if ($selectedData -eq $instance.data) { $selectedItem = $item }
            }
            if ($selectedItem) { $preferred = $selectedItem }
            if (-not $preferred -and $instanceList.Items.Count) { $preferred = $instanceList.Items[0] }
            if ($preferred) { $preferred.Selected = $true; $preferred.Focused = $true }
        } finally { $instanceList.EndUpdate() }
        $instancesLabel.Text = '本机已运行实例 · ' + $instanceList.Items.Count
        $instanceEmpty.Text = '暂无运行实例'
        $instanceEmpty.Visible = $instanceList.Items.Count -eq 0
        Sync-LaunchState
        Set-Feedback $(if ($script:currentInstance -and -not $script:currentInstance.controllable) { '旧版本实例需先从管理台退出，再通过启动器启动，才能使用启停操作。' } elseif ($script:currentInstance) { '修改管理端口：进入管理台保存后重启。' } else { '启动成功后记住此端口；服务在后台运行。' })
        return $true
    } catch {
        $instancesLabel.Text = '本机已运行实例 · 检测失败'
        $instanceEmpty.Text = '检测失败，请重新检测'
        $instanceEmpty.Visible = $instanceList.Items.Count -eq 0
        Set-Feedback $_.Exception.Message $true
        return $false
    } finally {
        $refreshInstances.Enabled = -not $script:busy
        Sync-InstanceActions
    }
}
function Open-DetectedInstance($selected, $closeWindow = $false, $checked = $null) {
    if ($null -eq $checked) {
        Invoke-InstanceDetection { param($detected, $context) Open-DetectedInstance $context.selected $context.closeWindow $detected } ([pscustomobject]@{selected=$selected;closeWindow=$closeWindow})
        return
    }
    try {
        $match = @($checked.instances | Where-Object { $_.data -eq $selected.data -and $_.port -eq $selected.port })
        if ($match.Count -eq 0) {
            Refresh-Instances $checked | Out-Null
            Set-Feedback '该实例已退出或无法确认，请重新选择。' $true
            return
        }
        Start-Process ('http://127.0.0.1:' + $match[0].port + '/admin')
        if ($closeWindow) { $form.Close() }
    } catch { Set-Feedback '无法打开管理台，请重新检测后重试。' $true }
}
$refreshInstances.Add_Click({ Invoke-InstanceDetection { param($detected) Refresh-Instances $detected | Out-Null } })
$instanceList.Add_SelectedIndexChanged({ Sync-InstanceActions })
$openInstance.Add_Click({
    if ($instanceList.SelectedItems.Count) { Open-DetectedInstance $instanceList.SelectedItems[0].Tag }
})
function Confirm-InstanceControl($selected, $operation) {
    $verb = if ($operation -eq 'stop') { '关闭' } else { '重启' }
    $message = $verb + '管理端口 ' + $selected.port + " 的 LanBridge？`n" + $selected.project + $(if ($operation -eq 'stop') { "`n关闭后公网转发将停止。" } else { "`n公网转发会短暂中断。" })
    return [System.Windows.Forms.MessageBox]::Show($form, $message, $verb + ' LanBridge', 'YesNo', 'Question') -eq 'Yes'
}
function Control-DetectedInstance($selected, $operation, $checked = $null) {
    if ($null -eq $checked) {
        Invoke-InstanceDetection { param($detected, $context) Control-DetectedInstance $context.selected $context.operation $detected } ([pscustomobject]@{selected=$selected;operation=$operation})
        return
    }
    if ($script:busy) { return }
    try {
        $match = @($checked.instances | Where-Object { $_.data -eq $selected.data -and $_.instance -eq $selected.instance -and $_.port -eq $selected.port -and $_.controllable })
        if ($match.Count -ne 1) { Refresh-Instances $checked | Out-Null; Set-Feedback '该实例已变化或不支持此操作，请重新检测。' $true; return }
        if (-not (Confirm-InstanceControl $match[0] $operation)) { return }
        $script:controlAction = $operation
        Set-Busy $true
        $script:controlPort = $selected.port
        Set-Feedback $(if ($operation -eq 'stop') { '正在关闭选中实例…' } else { '正在重启选中实例…' })
        $script:resultFile = Join-Path ([IO.Path]::GetTempPath()) ('lanbridge-control-' + [Guid]::NewGuid().ToString('N') + '.json')
        $script:launchStarted = [DateTime]::UtcNow
        $arguments = @(('"' + (Join-Path $PSScriptRoot 'run.py') + '"'), '--data-dir', ('"' + $selected.data + '"'), 'control', $operation, '--instance', $selected.instance, '--result', ('"' + $script:resultFile + '"'))
        if ($script:launchProcess -and $script:launchProcess.HasExited) { $script:launchProcess.Dispose(); $script:launchProcess = $null }
        $script:launchProcess = Start-Process -FilePath $pythonWindowless -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
        $timer.Start()
    } catch { Set-Busy $false; $script:controlAction = $null; Set-Feedback '实例操作未确认完成，请重新检测。' $true }
}
$stopInstance.Add_Click({ if ($instanceList.SelectedItems.Count) { Control-DetectedInstance $instanceList.SelectedItems[0].Tag 'stop' } })
$restartInstance.Add_Click({ if ($instanceList.SelectedItems.Count) { Control-DetectedInstance $instanceList.SelectedItems[0].Tag 'restart' } })
$script:controlAction = $null
$script:launchProcess = $null
$script:resultFile = $null
$script:launchStarted = $null
$script:busy = $false
$script:dependenciesReady = $false
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
function Set-Feedback($text, $errorState = $false) {
    $feedback.Text = $text
    $feedback.ForeColor = if ($errorState) { [System.Drawing.Color]::FromArgb(167, 70, 49) } else { $subtitle.ForeColor }
}
function Set-Busy($value) {
    $script:busy = $value
    $adminPort.Enabled = -not $value
    $refreshInstances.Enabled = -not $value
    $instanceList.Enabled = -not $value
    Sync-LaunchState
    Sync-InstanceActions
}
function Clear-Result {
    if ($script:resultFile -and (Test-Path -LiteralPath $script:resultFile)) {
        Remove-Item -LiteralPath $script:resultFile
    }
}
$adminPort.Add_TextChanged({
    $previewPort = 0
    if ([int]::TryParse($adminPort.Text, [ref]$previewPort) -and $previewPort -ge 1024 -and $previewPort -le 65535) {
        $address.Text = 'http://127.0.0.1:' + $adminPort.Text + '/admin'
    } else { $address.Text = '端口范围：1024–65535' }
    if ($script:dependenciesReady -and -not $script:busy -and -not $script:currentInstance) { Set-Feedback '启动成功后记住此端口；网关设置在管理台内处理。' }
})
function Start-LanBridge {
    if ($script:currentInstance) { Open-DetectedInstance $script:currentInstance $true; return }
    $port = 0
    if (-not [int]::TryParse($adminPort.Text.Trim(), [ref]$port) -or $port -lt 1024 -or $port -gt 65535) {
        Set-Feedback '请输入 1024–65535 之间的整数端口。' $true
        $adminPort.Focus() | Out-Null
        return
    }
    # Check availability now; the server's real bind remains authoritative.
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
    $probe.ExclusiveAddressUse = $true
    try { $probe.Start() } catch {
        Set-Feedback ('端口 ' + $port + ' 被占用或被系统保留，请选择其他端口。') $true
        return
    } finally { $probe.Stop() }
    try {
        Set-Busy $true
        Set-Feedback '正在启动管理台，请稍候…'
        $script:resultFile = Join-Path ([IO.Path]::GetTempPath()) ('lanbridge-start-' + [Guid]::NewGuid().ToString('N') + '.json')
        $script:launchStarted = [DateTime]::UtcNow
        $arguments = @(('"' + (Join-Path $PSScriptRoot 'run.py') + '"'), 'serve', '--admin-port', $port.ToString(), '--startup-result', ('"' + $script:resultFile + '"'))
        if ($openAfterStart.Checked) { $arguments += '--open-browser' }
        if ($script:launchProcess -and $script:launchProcess.HasExited) { $script:launchProcess.Dispose(); $script:launchProcess = $null }
        $script:launchProcess = Start-Process -FilePath $pythonWindowless -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
        $timer.Start()
    } catch {
        Set-Busy $false
        Set-Feedback '后台启动失败，请确认 Python 安装及目录权限。' $true
    }
}
$start.Add_Click({ Invoke-InstanceDetection { param($detected) if (Refresh-Instances $detected) { Start-LanBridge } } })
$script:discoveryProcess = $null
$discoveryTimer = New-Object System.Windows.Forms.Timer
$discoveryTimer.Interval = 50
function Invoke-InstanceDetection($callback, $context = $null) {
    if ($script:busy) { return }
    try {
        Set-Busy $true
        Set-Feedback '正在检测本机实例…'
        $script:discoveryCallback = $callback
        $script:discoveryContext = $context
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = $python
        $info.Arguments = ('"{0}" instances' -f (Join-Path $PSScriptRoot 'run.py'))
        $info.WorkingDirectory = $PSScriptRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.Encoding]::UTF8
        $info.StandardErrorEncoding = [Text.Encoding]::UTF8
        $script:discoveryProcess = New-Object System.Diagnostics.Process
        $script:discoveryProcess.StartInfo = $info
        $script:discoveryProcess.Start() | Out-Null
        $script:discoveryOutput = $script:discoveryProcess.StandardOutput.ReadToEndAsync()
        $script:discoveryError = $script:discoveryProcess.StandardError.ReadToEndAsync()
        $script:discoveryStarted = [DateTime]::UtcNow
        $discoveryTimer.Start()
    } catch {
        Fail-Discovery '实例检测无法启动，请重新检测'
    }
}
function Clear-Discovery {
    $discoveryTimer.Stop()
    if ($script:discoveryProcess) {
        try {
            if (-not $script:discoveryProcess.HasExited) {
                # The Windows venv executable can have a Python child. End only
                # this launcher-owned read-only detection tree, never a service.
                $stopInfo = New-Object Diagnostics.ProcessStartInfo
                $stopInfo.FileName = Join-Path $env:WINDIR 'System32\taskkill.exe'
                $stopInfo.Arguments = '/PID ' + $script:discoveryProcess.Id + ' /T /F'
                $stopInfo.UseShellExecute = $false
                $stopInfo.CreateNoWindow = $true
                $stopInfo.RedirectStandardOutput = $true
                $stopInfo.RedirectStandardError = $true
                $stopProcess = [Diagnostics.Process]::Start($stopInfo)
                $stopProcess.StandardOutput.ReadToEndAsync() | Out-Null
                $stopProcess.StandardError.ReadToEndAsync() | Out-Null
                $stopProcess.Dispose()
            }
        } catch { }
        finally { $script:discoveryProcess.Dispose(); $script:discoveryProcess = $null }
    }
    $script:discoveryCallback = $null
    $script:discoveryContext = $null
}
function Fail-Discovery($message) {
    $completedMessage = if ($script:discoveryContext -is [string]) { $script:discoveryContext } else { $null }
    Clear-Discovery
    Set-Busy $false
    if ($completedMessage) { Set-Feedback ($completedMessage + '；检测未完成，请重新检测') $true }
    else { Set-Feedback $message $true }
}
$discoveryTimer.Add_Tick({
    if (-not $script:discoveryProcess) { return }
    try {
        if (-not $script:discoveryProcess.HasExited -or -not $script:discoveryOutput.IsCompleted -or -not $script:discoveryError.IsCompleted) {
            if (([DateTime]::UtcNow - $script:discoveryStarted).TotalSeconds -gt 15) { throw '实例检测超时，请重试' }
            return
        }
        if ($script:discoveryProcess.ExitCode -ne 0) { throw '实例检测失败，请重试' }
        $detected = $script:discoveryOutput.Result | ConvertFrom-Json
        if ($null -eq $detected -or $detected.error) { throw '无法确认本机实例，请重新检测' }
        $callback = $script:discoveryCallback
        $context = $script:discoveryContext
        Clear-Discovery
        Set-Busy $false
        & $callback $detected $context
    } catch {
        Fail-Discovery $_.Exception.Message
    }
})
$timer.Add_Tick({
    try {
        if (Test-Path -LiteralPath $script:resultFile) {
            $result = Get-Content -LiteralPath $script:resultFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $timer.Stop()
            Clear-Result
            if ($result.ok -and $script:controlAction) {
                $operation = $script:controlAction
                $script:controlAction = $null
                Set-Busy $false
                $message = if ($operation -eq 'stop') { '选中实例已关闭' } else { '选中实例已重启，管理端口：' + $result.port }
                Invoke-InstanceDetection { param($detected, $message) Refresh-Instances $detected | Out-Null; Set-Feedback $message } $message
                return
            }
            if ($result.ok) { $script:busy = $false; $form.Close(); return }
            $script:controlAction = $null
            Set-Busy $false
            Set-Feedback $result.error $true
        } elseif ($script:launchProcess.HasExited) {
            $timer.Stop()
            Set-Busy $false
            Set-Feedback $(if ($script:controlAction) { '操作未确认完成，请重新检测。' } else { '管理台未能启动，请查看 data/runtime.log 后重试。' }) $true
            $script:controlAction = $null
        } elseif (([DateTime]::UtcNow - $script:launchStarted).TotalSeconds -gt $(if ($script:controlAction) { 55 } else { 30 })) {
            $timer.Stop()
            Set-Busy $false
            Set-Feedback '操作耗时较长，请重新检测确认状态。' $true
            $script:controlAction = $null
        }
    } catch {
        $timer.Stop()
        Set-Busy $false
        $script:controlAction = $null
        Set-Feedback '无法读取操作结果，请重新检测实例状态。' $true
    }
})
$form.Add_FormClosing({param($sender, $eventArgs)
    if ($script:discoveryProcess) { Clear-Discovery; $script:busy = $false }
    elseif ($script:busy) { $eventArgs.Cancel = $true }
})
try {
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pythonWindowless)) {
        throw '尚未安装依赖，请先运行 install.ps1。'
    }
    $statusText = & $python run.py status
    if ($LASTEXITCODE -ne 0) { throw '无法读取本机配置，请检查 data 目录权限。' }
    $status = $statusText | ConvertFrom-Json
    $adminPort.Text = $(if ($status.result.pending_admin_port) { $status.result.pending_admin_port } else { $status.result.settings.admin_port }).ToString()
    $script:dependenciesReady = $true
} catch {
    Set-Feedback $_.Exception.Message $true
    $start.Enabled = $false
}
$activationTimer = New-Object System.Windows.Forms.Timer
$activationTimer.Interval = 150
$activationTimer.Add_Tick({
    if ($launcherGate.TakeActivation()) {
        [LanBridgeLauncherShell]::ShowWindow($form.Handle, 9) | Out-Null
        $form.BringToFront()
        $form.Activate()
        [LanBridgeLauncherShell]::SetForegroundWindow($form.Handle) | Out-Null
    }
})
$form.Add_Shown({ $activationTimer.Start() })
$form.Add_Shown({ if ($script:dependenciesReady) { Invoke-InstanceDetection { param($detected) Refresh-Instances $detected | Out-Null; $start.Focus() | Out-Null } } })
try { $form.ShowDialog() | Out-Null } finally {
    $activationTimer.Stop()
    $activationTimer.Dispose()
    Clear-Discovery
    $discoveryTimer.Dispose()
    $timer.Stop()
    $timer.Dispose()
    if (-not $script:launchProcess -or $script:launchProcess.HasExited) { Clear-Result }
    if ($script:launchProcess) { $script:launchProcess.Dispose() }
    $brand.Image.Dispose()
    $form.Dispose()
    $launcherIcon.Dispose()
}

} finally { $launcherGate.Dispose() }
