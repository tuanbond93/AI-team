param([ValidateRange(1024, 65535)][int]$Port = 8503)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$entryPoint = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'app.py'))
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    foreach ($ownerId in @($listeners.OwningProcess | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if (-not $process -or -not $process.CommandLine -or $process.CommandLine -notmatch [regex]::Escape($entryPoint) -or $process.CommandLine -notmatch 'streamlit') {
            throw "Port $Port belongs to another or unverified process. No process was stopped."
        }
    }
    Write-Host "AI Team: http://127.0.0.1:$Port"
    return
}
if (-not (Test-Path -LiteralPath "$HOME\.venv\Scripts\Activate.ps1")) {
    throw 'Shared Python environment missing. See README.md.'
}
& "$HOME\.venv\Scripts\Activate.ps1"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'
python -m streamlit run "$entryPoint" --server.address 127.0.0.1 --server.port $Port --server.fileWatcherType none --server.headless true --browser.gatherUsageStats false --server.maxUploadSize 20 --theme.base light --theme.primaryColor '#1e40af' --theme.backgroundColor '#f8fafc' --theme.secondaryBackgroundColor '#ffffff' --theme.textColor '#17243c'
