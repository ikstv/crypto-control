param([ValidateSet('install','uninstall','start','stop','status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$taskName = 'CryptoControlEngine'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$marker = Join-Path $root 'data\engine-stopped'
$port = if ($env:CRYPTO_CONTROL_ENGINE_PORT) { $env:CRYPTO_CONTROL_ENGINE_PORT } else { '8000' }
$url = "http://127.0.0.1:$port/api/v1/status"

function Show-Status {
    try {
        $value = Invoke-RestMethod -Uri $url -TimeoutSec 2
        Write-Output "Engine running. Status: $($value.status). Quotes: $($value.quotes)."
    } catch {
        Write-Output 'Engine is not responding on 127.0.0.1:8000.'
    }
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { Write-Output "Scheduled task: $($task.State)." }
    else { Write-Output 'Scheduled task is not installed.' }
}

switch ($Action) {
    'install' {
        if (-not (Test-Path -LiteralPath $python)) { throw 'Run start.cmd once to create .venv.' }
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
        $program = New-ScheduledTaskAction -Execute (Join-Path $root 'engine-task.cmd') -WorkingDirectory $root
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $program -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Write-Output 'Autostart installed for this Windows user.'
    }
    'uninstall' {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output 'Autostart removed.'
    }
    'start' {
        if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker }
        Start-ScheduledTask -TaskName $taskName
        Show-Status
    }
    'stop' {
        New-Item -ItemType Directory -Path (Split-Path $marker) -Force | Out-Null
        New-Item -ItemType File -Path $marker -Force | Out-Null
        Write-Output 'Engine stop requested. Automatic restart suppressed.'
    }
    'status' { Show-Status }
}
