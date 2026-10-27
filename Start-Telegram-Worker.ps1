$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$HOME\.venv\Scripts\Activate.ps1"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$stateFolder = Join-Path $PSScriptRoot '.telegram_state'
New-Item -ItemType Directory -Path $stateFolder -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $stateFolder 'settings.dpapi'))) {
    throw 'Configure Telegram in AI Team first. No plaintext token is accepted.'
}
$workerProcess = Start-Process -FilePath "$HOME\.venv\Scripts\python.exe" -ArgumentList @('-m', 'ops_team.telegram_worker') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateFolder 'worker.stdout.log') -RedirectStandardError (Join-Path $stateFolder 'worker.stderr.log')
Write-Output ('Worker requested, process ' + $workerProcess.Id + '. Check AI Team status.')
