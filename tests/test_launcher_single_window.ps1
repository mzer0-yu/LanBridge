# Cross-process checks of the production window gate; no service or real launcher.
param([string]$Directory, [string]$Result, [switch]$Hold)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSource = [IO.File]::ReadAllText((Join-Path $taskRoot 'start.ps1'))
$taskDefinition = [regex]::Match($taskSource, '(?s)Add-Type -TypeDefinition @"\r?\n(.*?)\r?\n"@')
if (-not $taskDefinition.Success) { throw 'Launcher native definition not found' }
Add-Type -TypeDefinition $taskDefinition.Groups[1].Value
if ($Directory) {
    $taskChildGate = New-Object LanBridgeLauncherGate($Directory)
    try {
        if (-not $taskChildGate.IsOwner) { $taskChildGate.RequestActivation() }
        [IO.File]::WriteAllText($Result, ($taskChildGate.IsOwner.ToString()))
        if ($Hold) { Start-Sleep -Seconds 30 }
    } finally { $taskChildGate.Dispose() }
    return
}
function Assert-Task($condition, $message) { if (-not $condition) { throw $message } }
$taskFixture = Join-Path $taskRoot ('.test-artifacts/launcher-single-window/' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $taskFixture
$taskProcesses = @()
function Start-TaskChild([string]$path, [switch]$keep) {
    $taskResult = Join-Path $taskFixture ([Guid]::NewGuid().ToString('N') + '.txt')
    $taskArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath), '-Directory', ('"{0}"' -f $path), '-Result', ('"{0}"' -f $taskResult))
    if ($keep) { $taskArgs += '-Hold' }
    $taskProcess = Start-Process powershell.exe -ArgumentList $taskArgs -WindowStyle Hidden -RedirectStandardError ($taskResult + '.err') -PassThru
    $null = $taskProcess.Handle
    $script:taskProcesses += $taskProcess
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $taskResult) -and -not $taskProcess.HasExited -and [DateTime]::UtcNow -lt $taskDeadline) { Start-Sleep -Milliseconds 30 }
    if (-not (Test-Path -LiteralPath $taskResult)) { throw ([IO.File]::ReadAllText($taskResult + '.err')) }
    if (-not $keep) { Assert-Task ($taskProcess.WaitForExit(5000) -and $taskProcess.ExitCode -eq 0) 'Child gate failed' }
    return [pscustomobject]@{owner=([IO.File]::ReadAllText($taskResult) -eq 'True'); process=$taskProcess}
}
$taskGate = $null
$taskObserver = $null
try {
    $taskDirectory = Join-Path $taskFixture 'Install'
    $taskGate = New-Object LanBridgeLauncherGate($taskDirectory)
    Assert-Task $taskGate.IsOwner 'First launcher must own gate'
    $taskDuplicate = Start-TaskChild ($taskDirectory.ToLowerInvariant() + '/')
    Assert-Task (-not $taskDuplicate.owner) 'Same directory must reuse owner, including case/trailing slash'
    Assert-Task $taskGate.TakeActivation() 'Duplicate must signal owner'
    Assert-Task (-not $taskGate.TakeActivation()) 'Activation must be consumed once'
    $taskOther = Start-TaskChild (Join-Path $taskFixture 'OtherInstall')
    Assert-Task $taskOther.owner 'Different directory must open independently'
    $taskGate.Dispose(); $taskGate = $null
    $taskReopened = Start-TaskChild $taskDirectory
    Assert-Task $taskReopened.owner 'Closing window must permit reopening'
    $taskHeld = Start-TaskChild $taskDirectory -keep
    Assert-Task $taskHeld.owner 'Held child must own gate'
    $taskObserver = New-Object LanBridgeLauncherGate($taskDirectory)
    Assert-Task (-not $taskObserver.IsOwner) 'Live child must block duplicate'
    $taskHeld.process.Kill()
    Assert-Task ($taskHeld.process.WaitForExit(5000)) 'Owned test child did not exit'
    $taskGate = New-Object LanBridgeLauncherGate($taskDirectory)
    Assert-Task $taskGate.IsOwner 'Abandoned window mutex must permit recovery'
    Write-Output 'Launcher single-window checks passed: cross-process reuse, normalization, activation, independent directories, reopening and abnormal exit recovery.'
} finally {
    if ($taskGate) { $taskGate.Dispose() }
    if ($taskObserver) { $taskObserver.Dispose() }
    foreach ($taskProcess in $taskProcesses) {
        if (-not $taskProcess.HasExited) { $taskProcess.Kill(); $null = $taskProcess.WaitForExit(5000) }
        $taskProcess.Dispose()
    }
}