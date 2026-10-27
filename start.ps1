param([switch]$Restart)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$entryPoint = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'app.py'))
$listeners = @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $owners = @($listeners.OwningProcess | Select-Object -Unique)
    foreach ($ownerId in $owners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if (-not $process -or -not $process.CommandLine -or $process.CommandLine -notmatch [regex]::Escape($entryPoint) -or $process.CommandLine -notmatch 'streamlit') {
            throw "Port 8501 dang do process khac/khong xac minh duoc su dung (PID $ownerId). Khong tu dung process nay."
        }
    }
    if (-not $Restart) {
        Write-Host 'AI Operations Team da chay: http://127.0.0.1:8501'
        Write-Host 'Khong khoi dong them server. Dung -Restart sau khi luu/xuat phien neu can nap ma moi; restart se mat RAM phien.'
        return
    }
    foreach ($ownerId in $owners) {
        Stop-Process -Id $ownerId -Force
    }
}
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'
if (-not (Test-Path -LiteralPath "$HOME\.venv\Scripts\Activate.ps1")) {
    throw 'Chua co moi truong dung chung. Xem README.md de cai dat.'
}
& "$HOME\.venv\Scripts\Activate.ps1"
python -m streamlit run "$entryPoint" --server.address 127.0.0.1 --server.port 8501 --server.fileWatcherType none --server.headless true --browser.gatherUsageStats false --server.maxUploadSize 20 --theme.base light --theme.primaryColor '#1e40af' --theme.backgroundColor '#f8fafc' --theme.secondaryBackgroundColor '#ffffff' --theme.textColor '#17243c'
