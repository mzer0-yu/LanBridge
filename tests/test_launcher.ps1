# Isolated launcher state checks: no real browser, server or configuration writes.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSource = [IO.File]::ReadAllText((Join-Path $taskRoot 'start.ps1'))
$taskTokens = $null
$taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseInput($taskSource, [ref]$taskTokens, [ref]$taskErrors)
if ($taskErrors.Count) { throw ($taskErrors | Out-String) }
$taskBegin = $taskSource.IndexOf('$form = New-Object')
$taskEnd = $taskSource.IndexOf('$timer.Add_Tick')
Invoke-Expression $taskSource.Substring($taskBegin, $taskEnd - $taskBegin).Replace('$PSScriptRoot', '$taskRoot')
$null = $instanceList.Handle
$script:dependenciesReady = $true
$script:fixture = [pscustomobject]@{ instances = @(); error = '' }
$script:failDetection = $false
$script:opened = @()
function Get-DetectedInstances {
    if ($script:failDetection) { return [pscustomobject]@{instances=@();error='isolated detection failure'} }
    return $script:fixture
}
function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    $script:opened += $FilePath
    $script:controlArguments = $ArgumentList
    if ($PassThru) { return [pscustomobject]@{ HasExited=$false } }
}
$taskAsyncDetector = ${function:Invoke-InstanceDetection}
function Invoke-InstanceDetection($callback, $context = $null) { & $callback (Get-DetectedInstances) $context }
function Confirm-InstanceControl { return $true }
function Set-GuideClipboard([string]$text) { $script:guideCopied = $text }
function Assert-Task($condition, $message) { if (-not $condition) { throw $message } }
$taskCurrent = [pscustomobject]@{ port=8890; project='C:\Example\LanBridge'; data='C:\Example\LanBridge\data'; current=$true; controllable=$true; instance=('a'*32) }
$taskOther = [pscustomobject]@{ port=8892; project='C:\Other\LanBridge'; data='C:\Other\LanBridge\data'; current=$false; controllable=$true; instance=('b'*32) }
try {
    Assert-Task ($agentGuide.Right -le $stopInstance.Left -and $agentGuide.Top -eq $stopInstance.Top) 'Guide link overlaps instance controls'
    Open-AgentGuide
    Assert-Task ($script:opened.Count -eq 1 -and $script:opened[0].EndsWith('notepad.exe') -and $script:controlArguments -eq ('"{0}"' -f (Join-Path $taskRoot 'docs\agent-startup.md'))) 'Guide entry did not open checkout documentation'
    $script:opened = @()
    $taskLinkEvent = $agentGuide.GetType().GetMethod('OnLinkClicked', [Reflection.BindingFlags]'Instance,NonPublic')
    foreach ($taskButton in @([Windows.Forms.MouseButtons]::Right, [Windows.Forms.MouseButtons]::Middle)) {
        $taskLinkArgs = New-Object Windows.Forms.LinkLabelLinkClickedEventArgs($agentGuide.Links[0], $taskButton)
        $taskLinkEvent.Invoke($agentGuide, [object[]]@($taskLinkArgs.PSObject.BaseObject)) | Out-Null
    }
    Assert-Task ($script:opened.Count -eq 0) 'Right or middle click unexpectedly opens guide'
    foreach ($taskButton in @([Windows.Forms.MouseButtons]::Left, [Windows.Forms.MouseButtons]::None)) {
        $taskLinkArgs = New-Object Windows.Forms.LinkLabelLinkClickedEventArgs($agentGuide.Links[0], $taskButton)
        $taskLinkEvent.Invoke($agentGuide, [object[]]@($taskLinkArgs.PSObject.BaseObject)) | Out-Null
    }
    Assert-Task ($script:opened.Count -eq 2) 'Left click or keyboard activation does not open guide'
    $script:opened = @()
    $copyGuidePath.PerformClick()
    Assert-Task ($script:guideCopied -eq (Join-Path $taskRoot 'docs\agent-startup.md')) 'Guide path copy is incorrect'
    $copyGuideUrl.PerformClick()
    Assert-Task ($script:guideCopied.StartsWith('file:///') -and ([Uri]$script:guideCopied).LocalPath -eq (Join-Path $taskRoot 'docs\agent-startup.md')) 'File URL does not round-trip to guide path'
    Assert-Task ($agentGuide.ContextMenuStrip -eq $guideMenu -and $feedback.Text.Contains('仅适用于本机')) 'Copy menu or local URL feedback missing'
    Assert-Task ($form.Icon -and $form.Icon.Width -ge 16) 'Launcher icon not loaded'
    Assert-Task ($form.ControlBox -and $null -eq $form.CancelButton) 'Window close control missing or duplicate cancel button retained'
    Assert-Task (-not ($form.Controls | Where-Object { $_.Text -eq '关闭启动器' })) 'Duplicate launcher close button retained'
    Assert-Task ($stopInstance.Top -eq $restartInstance.Top -and $restartInstance.Top -eq $openInstance.Top -and $restartInstance.Left-$stopInstance.Right -eq 10 -and $openInstance.Left-$restartInstance.Right -eq 10) 'Instance actions not evenly aligned'
    $script:fixture.instances = @($taskOther, $taskCurrent)
    Assert-Task (Refresh-Instances (Get-DetectedInstances)) 'Running-instance refresh failed'
    Assert-Task ($adminPort.ReadOnly -and $start.Text -eq '打开管理台') 'Running state did not switch primary action'
    Assert-Task (-not $inputPanel.Visible -and $portValue.Text -eq '8890') 'Running port still appears editable'
    Assert-Task ($brand.Image -ne $null) 'Brand image missing'
    Assert-Task $openInstance.Enabled 'Selected current instance cannot be opened'
    $instanceList.Items[0].Selected = $true
    Assert-Task $openInstance.Enabled 'Other instance cannot be opened'
    $instanceList.Items[1].Selected = $true
    Assert-Task ($instanceList.SelectedItems.Count -eq 1 -and $instanceList.SelectedItems[0].Tag.current) 'Current instance not selected'
    Assert-Task ($instanceList.Items[1].SubItems[2].Text -eq '本目录') 'Directory marker missing'
    Assert-Task ($instanceList.Items[1].SubItems[1].Text -eq $taskCurrent.project) 'Marker polluted path column'
    Assert-Task ($instanceList.Items[1].ToolTipText.Contains($taskCurrent.data)) 'Full data path missing from tooltip'
    Open-DetectedInstance $taskOther
    Assert-Task ($script:opened.Count -eq 1 -and $script:opened[0] -eq 'http://127.0.0.1:8892/admin') 'Wrong instance opened'
    $script:failDetection = $true
    Assert-Task (-not (Refresh-Instances (Get-DetectedInstances))) 'Detection error misreported as success'
    Assert-Task $adminPort.ReadOnly 'Detection error discarded known running state'
    $script:failDetection = $false
    $script:fixture.instances = @()
    Open-DetectedInstance $taskCurrent
    Assert-Task ($script:opened.Count -eq 1) 'Exited instance was opened'
    Assert-Task (-not $adminPort.ReadOnly -and $start.Text -eq '启动 LanBridge') 'Exited state failed to restore port editing'
    Assert-Task (-not $openInstance.Enabled) 'Open action enabled without a selected running instance'
    Assert-Task ($instanceEmpty.Text -eq '暂无运行实例') 'Empty instance list has no explanation'
    Assert-Task ($openAfterStart.Left -eq $start.Left -and $openAfterStart.Top -ge $start.Bottom -and $feedback.Top -ge $openAfterStart.Bottom) 'Start preference/status hierarchy overlaps'
    Set-Busy $true
    Assert-Task (-not $start.Enabled -and -not $refreshInstances.Enabled) 'Busy state permits duplicate operation'
    Set-Busy $false
    Assert-Task ($start.Enabled -and $refreshInstances.Enabled) 'Retry controls did not recover'
    foreach ($taskControl in $form.Controls) {
        Assert-Task ($taskControl.Right -le $form.ClientSize.Width -and $taskControl.Bottom -le $form.ClientSize.Height) 'Control exceeds window bounds'
    }
    $script:fixture.instances = @($taskCurrent)
    $taskOpenedBeforeControls = @($script:opened)
    Refresh-Instances (Get-DetectedInstances) | Out-Null
    Assert-Task ($stopInstance.Enabled -and $restartInstance.Enabled) 'Selected instance lifecycle controls disabled'
    $taskPrevious = $script:opened.Count
    Control-DetectedInstance $taskCurrent 'stop'
    Assert-Task ($script:busy -and $script:controlAction -eq 'stop') 'Stop operation not busy'
    Assert-Task (-not $stopInstance.Enabled -and -not $restartInstance.Enabled -and -not $openInstance.Enabled) 'Duplicate control allowed'
    Assert-Task ($script:controlArguments -contains 'control' -and $script:controlArguments -contains 'stop' -and $script:controlArguments -contains $taskCurrent.instance) 'Wrong instance control arguments'
    $timer.Stop(); Set-Busy $false; $script:controlAction = $null
    Assert-Task $openInstance.Enabled 'Open action not restored after busy state'
    Control-DetectedInstance $taskCurrent 'restart'
    Assert-Task ($script:controlAction -eq 'restart' -and $script:controlArguments -contains 'restart') 'Restart operation missing'
    $timer.Stop(); Set-Busy $false; $script:controlAction = $null
    $taskBeforeStale = $script:opened.Count
    $taskStale = [pscustomobject]@{port=$taskCurrent.port;data=$taskCurrent.data;instance=('c'*32);controllable=$true}
    Control-DetectedInstance $taskStale 'stop'
    Assert-Task ($script:opened.Count -eq $taskBeforeStale) 'Replaced instance was controlled'
    $taskArtifacts = Join-Path $taskRoot '.test-artifacts'
    [IO.Directory]::CreateDirectory($taskArtifacts) | Out-Null
    foreach ($taskLayoutMode in @('running','idle')) {
        $script:fixture.instances = if ($taskLayoutMode -eq 'running') { @($taskCurrent) } else { @() }
        Refresh-Instances (Get-DetectedInstances) | Out-Null
        $openAfterStart.Checked = $taskLayoutMode -eq 'running'
        $taskImageName = if ($taskLayoutMode -eq 'running') { 'launcher-control-layout.png' } else { 'launcher-start-only-layout.png' }
    $taskBitmap = New-Object System.Drawing.Bitmap($form.Width, $form.Height)
    try { $form.DrawToBitmap($taskBitmap, (New-Object System.Drawing.Rectangle(0, 0, $form.Width, $form.Height)));
        $taskGraphics = [System.Drawing.Graphics]::FromImage($taskBitmap)
        foreach ($taskDrawControl in $form.Controls) {
            if ($script:currentInstance -and $taskDrawControl -in @($inputPanel,$openAfterStart)) { continue }
            if (-not $script:currentInstance -and $taskDrawControl -eq $portValue) { continue }
            if ($taskDrawControl -eq $instanceEmpty) { continue }
            $taskControlBitmap = New-Object System.Drawing.Bitmap($taskDrawControl.Width, $taskDrawControl.Height)
            try { $taskDrawControl.DrawToBitmap($taskControlBitmap, (New-Object System.Drawing.Rectangle(0,0,$taskDrawControl.Width,$taskDrawControl.Height))); $taskGraphics.DrawImageUnscaled($taskControlBitmap, $taskDrawControl.Left+8, $taskDrawControl.Top+31) } finally { $taskControlBitmap.Dispose() }
        }
        if (-not $script:currentInstance) {
            $taskInputBitmap = New-Object System.Drawing.Bitmap($adminPort.Width,$adminPort.Height)
            try { $adminPort.DrawToBitmap($taskInputBitmap,(New-Object System.Drawing.Rectangle(0,0,$adminPort.Width,$adminPort.Height))); $taskGraphics.DrawImageUnscaled($taskInputBitmap,$inputPanel.Left+$adminPort.Left+8,$inputPanel.Top+$adminPort.Top+31) } finally { $taskInputBitmap.Dispose() }
        }
        if ($instanceList.Items.Count -eq 0) {
            $taskEmptyBitmap = New-Object System.Drawing.Bitmap($instanceEmpty.Width,$instanceEmpty.Height)
            try { $instanceEmpty.DrawToBitmap($taskEmptyBitmap,(New-Object System.Drawing.Rectangle(0,0,$instanceEmpty.Width,$instanceEmpty.Height))); $taskGraphics.DrawImageUnscaled($taskEmptyBitmap,$instanceEmpty.Left+8,$instanceEmpty.Top+31) } finally { $taskEmptyBitmap.Dispose() }
        }
        if (-not $script:currentInstance) { $taskGraphics.FillRectangle([System.Drawing.Brushes]::White,$inputPanel.Left+$adminPort.Left+8,$inputPanel.Top+$adminPort.Top+31,$adminPort.Width,$adminPort.Height); [System.Windows.Forms.TextRenderer]::DrawText($taskGraphics,$adminPort.Text,$adminPort.Font,(New-Object System.Drawing.Point(($inputPanel.Left+$adminPort.Left+8),($inputPanel.Top+$adminPort.Top+31))),$form.ForeColor) }
        $taskGraphics.Dispose(); $taskBitmap.Save((Join-Path $taskArtifacts $taskImageName)) } finally { $taskBitmap.Dispose() }
    }
    $script:opened = $taskOpenedBeforeControls
    # Exercise the real asynchronous process/timer path with a delayed fake CLI.
    # DoEvents pumps timers offscreen; no browser, production service or clipboard.
    $taskOriginalRoot = $taskRoot
    $python = Join-Path $taskRoot '.venv\Scripts\python.exe'
    $taskRoot = Join-Path $taskRoot '.test-artifacts\launcher-async-fixture'
    [IO.Directory]::CreateDirectory($taskRoot) | Out-Null
    [IO.File]::WriteAllText((Join-Path $taskRoot 'run.py'), "import time; time.sleep(.5); print('{""instances"":[],""error"":""""}')")
    $script:asyncCompleted = $false; $script:heartbeatTicks = 0
    $taskHeartbeat = New-Object Windows.Forms.Timer
    $taskHeartbeat.Interval = 30
    $taskHeartbeat.Add_Tick({ $script:heartbeatTicks++ })
    try {
        $taskHeartbeat.Start()
        $taskWatch = [Diagnostics.Stopwatch]::StartNew()
        & $taskAsyncDetector { param($detected) $script:asyncCompleted = $true; $script:asyncDetected = $detected }
        Assert-Task ($taskWatch.ElapsedMilliseconds -lt 200 -and $script:busy) 'Detection blocks the UI thread'
        while (-not $script:asyncCompleted -and $taskWatch.ElapsedMilliseconds -lt 3000) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 10 }
        Assert-Task ($script:asyncCompleted -and -not $script:busy -and $script:heartbeatTicks -ge 5 -and $script:asyncDetected.instances.Count -eq 0) 'Delayed detection freezes timers or loses completion'
        [IO.File]::WriteAllText((Join-Path $taskRoot 'run.py'), "print('not-json')")
        & $taskAsyncDetector { throw 'Invalid JSON must not reach callback' } '选中实例已关闭'
        $taskWatch.Restart()
        while ($script:busy -and $taskWatch.ElapsedMilliseconds -lt 3000) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 10 }
        Assert-Task (-not $script:busy -and $null -eq $script:discoveryProcess) 'Failed detection leaves launcher locked'
        Assert-Task ($feedback.Text.Contains('选中实例已关闭') -and $feedback.Text.Contains('检测未完成')) 'Detection failure hid completed instance operation'
        [IO.File]::WriteAllText((Join-Path $taskRoot 'run.py'), 'import time; time.sleep(20)')
        & $taskAsyncDetector { throw 'Timed-out detection must not reach callback' }
        $script:discoveryStarted = [DateTime]::UtcNow.AddSeconds(-20)
        $taskWatch.Restart()
        while ($script:busy -and $taskWatch.ElapsedMilliseconds -lt 3000) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 10 }
        Assert-Task (-not $script:busy -and $null -eq $script:discoveryProcess) 'Timed-out helper not cleaned up'
    } finally { Clear-Discovery; $taskHeartbeat.Stop(); $taskHeartbeat.Dispose(); $taskRoot = $taskOriginalRoot; Set-Busy $false }
    $taskClick = $taskAst.Find({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Expression.Extent.Text -eq '$start' -and $node.Member.Value -eq 'Add_Click'
    }, $true).Arguments[0].ScriptBlock.GetScriptBlock()
    $taskClick = [scriptblock]::Create($taskClick.ToString().Replace('$PSScriptRoot', '$taskRoot'))
    $script:fixture.instances = @()
    $adminPort.Text = 'abc'
    & $taskClick
    Assert-Task ($script:opened.Count -eq 1 -and $feedback.Text.Contains('1024')) 'Invalid port started a process'
    # Exercise real port availability checking while mocking process/browser startup.
    $taskListener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $taskListener.Start(); $taskPort = $taskListener.LocalEndpoint.Port; $taskListener.Stop()
    $adminPort.Text = $taskPort.ToString()
    $openAfterStart.Checked = $false
    Assert-Task ($start.Text -eq '启动 LanBridge') 'Unchecked choice still implies opening a browser'
    & $taskClick
    Assert-Task ($script:controlArguments -contains 'serve' -and $script:controlArguments -notcontains '--open-browser') 'Start-only mode launches browser'
    Assert-Task (-not $openAfterStart.Enabled) 'Browser choice editable while launching'
    $timer.Stop(); Set-Busy $false
    $openAfterStart.Checked = $true
    Assert-Task ($start.Text -eq '启动 LanBridge') 'Browser preference changes the stable primary action label'
    & $taskClick
    Assert-Task ($script:controlArguments -contains '--open-browser') 'Start-and-open mode lost browser flag'
    $timer.Stop(); Set-Busy $false
    $script:opened = $taskOpenedBeforeControls
    $script:fixture.instances = @($taskCurrent)
    & $taskClick
    Assert-Task ($script:opened.Count -eq 2 -and $script:opened[1] -eq 'http://127.0.0.1:8890/admin') 'Primary action did not reuse current instance'
    Write-Output 'Launcher checks passed: running/idle/error/busy states, selection, opening and layout.'
} finally { $discoveryTimer.Dispose(); $timer.Dispose(); $brand.Image.Dispose(); $form.Dispose(); $launcherIcon.Dispose() }
