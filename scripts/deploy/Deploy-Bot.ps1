<#
Cập nhật bot Telegram đang chạy lên bản mới, có kiểm tra trước và tự quay lại khi lỗi.

Chạy trong thư mục bản mới (clone nhánh claude/awesome-ptolemy-t1t3g0), PowerShell thường (không cần Admin):

  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\Deploy-Bot.ps1 -CheckOnly
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\Deploy-Bot.ps1 -DriveFolder "G:\My Drive\Supra export"

  -CheckOnly    chỉ kiểm tra (test, preflight trên bản sao vault, quyền); không dừng bot, không chép gì
  -ScheduleOnly chỉ đặt lịch Task Scheduler 13:01/13:06 (cần -DriveFolder)
  -RestartOnly  dừng an toàn rồi bật lại worker với code đang có (vd. sau khi đặt AI_TEAM_CONVERSATION=legacy)

Các bước: kiểm tra quyền/đường dẫn -> test -> preflight trên BẢN SAO vault -> backup code + vault
-> lưu trạng thái lịch -> dừng worker cũ (chờ thoát hẳn) -> chép code -> kiểm import -> bật worker
-> kiểm heartbeat -> khôi phục lịch 13h nếu kế hoạch không đổi -> đặt lịch Task Scheduler 13:01/13:06.
Bất kỳ lỗi nào từ lúc dừng worker trở đi: tự chép lại code cũ, khôi phục trạng thái và bật lại worker cũ.

Không gửi tin Telegram, không gọi AI, không đọc khóa của ứng dụng khác. Chỉ một worker cho một vault.
#>
[CmdletBinding()]
param(
    [string]$Live = "$HOME\Desktop\AI team",
    [string]$DriveFolder = '',
    [int]$MocChauVnd = 0,
    [string]$From = '',
    [string]$To = '',
    [switch]$SkipTests,
    [switch]$SkipSchedule,
    [switch]$CheckOnly,
    [switch]$ScheduleOnly,
    [switch]$RestartOnly
)
$ErrorActionPreference = 'Stop'
$New = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = "$HOME\.venv\Scripts\python.exe"
$Vault = Join-Path $Live '.telegram_state'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$Backup = "$HOME\Desktop\AI-team-backup-$Stamp"
$VaultCopy = "$HOME\Desktop\AI-team-vault-copy"
$Saved = Join-Path $Backup 'controls.json'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "DỪNG: $text" -ForegroundColor Red; exit 1 }
function Py([string]$Dir, [string[]]$PyArgs) {
    Push-Location -LiteralPath $Dir
    # Output goes to the screen; only the exit code is returned (PowerShell would otherwise mix them).
    try { & $Python @PyArgs | Out-Host; return $LASTEXITCODE } finally { Pop-Location }
}
function Copy-Tree($Src, $Dst, [string[]]$Extra) {
    & robocopy $Src $Dst /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP @Extra | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy lỗi $LASTEXITCODE khi chép $Src -> $Dst" }
}
function Worker-Processes {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'ops_team\.telegram_worker' }
}
function Wait-WorkerStopped([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Worker-Processes)) { return $true }
        Start-Sleep -Seconds 5
    }
    return -not (Worker-Processes)
}
function Start-Worker {
    $null = Py $Live @('-c', "from ops_team.telegram_state import BusinessState; s=BusinessState(); s.set_control('worker_enabled', True); s.close()")
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Live 'Start-Telegram-Worker.ps1')
}

function Register-DriveTask {
    if (-not $DriveFolder) { Write-Host 'Chưa đặt lịch Drive: chạy lại với -ScheduleOnly -DriveFolder "<thư mục Drive đã đồng bộ>".' -ForegroundColor Yellow; return }
    Step 'Đặt lịch Task Scheduler 13:01 và 13:06 (nhập Drive, gửi báo cáo một lần/ngày)'
    $action = New-ScheduledTaskAction -Execute $Python -Argument "-m ops_team.drive_import --folder `"$DriveFolder`" --send-scheduled" -WorkingDirectory $Live
    $triggers = @((New-ScheduledTaskTrigger -Daily -At '13:01'), (New-ScheduledTaskTrigger -Daily -At '13:06'))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'AI Team - Drive import 13h' -Action $action -Trigger $triggers -Settings $settings -Description 'Nhập CSV Apps Script từ Drive và gửi báo cáo 13h (mỗi ngày một lần theo receipt).' -Force | Out-Null
    Write-Host 'OK: đã đặt lịch "AI Team - Drive import 13h".'
}
function Check-Tasks {
    try { $tasks = Get-ScheduledTask -ErrorAction Stop } catch { Fail 'không đọc được Task Scheduler bằng tài khoản này' }
    $other = $tasks | Where-Object { $_.TaskName -ne 'AI Team - Drive import 13h' -and ($_.Actions | Where-Object { $_.Arguments -match 'drive_import' }) }
    if ($other) { Fail ('đã có task khác chạy drive_import: '+(($other | ForEach-Object TaskName) -join ', ')+'. Xóa/tắt task đó trước để không gửi hai lần.') }
}

if ($ScheduleOnly) {
    if (-not $DriveFolder -or -not (Test-Path -LiteralPath $DriveFolder)) { Fail 'cần -DriveFolder trỏ tới thư mục Drive đã đồng bộ' }
    Check-Tasks
    Register-DriveTask
    exit 0
}
if ($RestartOnly) {
    $Saved = Join-Path $env:TEMP "ai-team-controls-$Stamp.json"
    if ((Py $Live @('-m', 'ops_team.deploy_state', 'save', '--root', $Vault, '--out', $Saved)) -ne 0) { Fail 'không lưu được trạng thái lịch' }
    Step 'Dừng worker'
    $null = Py $Live @('-c', 'from ops_team.telegram_control_ui import stop_worker; stop_worker()')
    if (-not (Wait-WorkerStopped 300)) { Fail 'worker chưa thoát sau 5 phút; kiểm trong AI Team' }
    $null = Py $Live @('-m', 'ops_team.deploy_state', 'restore', '--root', $Vault, '--saved', $Saved, '--no-worker')
    Step 'Bật worker'
    Start-Worker
    Start-Sleep -Seconds 40
    if ((Py $Live @('-m', 'ops_team.deploy_state', 'health', '--root', $Vault)) -ne 0) { Fail 'worker chưa có heartbeat hoặc báo lỗi' }
    Write-Host 'OK: worker đã chạy lại.'
    exit 0
}

# ---------- 0. Kiểm tra trước khi đụng vào bot ----------
Step 'Kiểm tra đường dẫn và quyền'
if (-not (Test-Path -LiteralPath $Python)) { Fail "không thấy Python venv ở $Python" }
if (-not (Test-Path -LiteralPath (Join-Path $Vault 'settings.dpapi'))) { Fail "không thấy vault đang chạy ở $Vault" }
if (-not (Test-Path -LiteralPath (Join-Path $Live 'Start-Telegram-Worker.ps1'))) { Fail "không thấy Start-Telegram-Worker.ps1 trong $Live" }
if ((Resolve-Path $Live).Path -eq $New) { Fail 'đang chạy script ngay trong thư mục bot; hãy chạy từ thư mục bản mới (clone riêng)' }
$probe = Join-Path $Live ".deploy-write-test-$Stamp"
try { Set-Content -LiteralPath $probe -Value 'ok'; Remove-Item -LiteralPath $probe } catch { Fail "không ghi được vào $Live" }
if (@(Worker-Processes).Count -gt 1) { Fail 'đang có nhiều hơn một worker; dừng bớt trong AI Team rồi chạy lại' }
if (-not $SkipSchedule -and $DriveFolder -and -not (Test-Path -LiteralPath $DriveFolder)) { Fail "không thấy thư mục Drive $DriveFolder" }
if (-not $SkipSchedule) { Check-Tasks }
if (-not $From) { $From = (Get-Date -Day 1).ToString('yyyy-MM-dd') }
if (-not $To) { $To = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd'); if ($To -lt $From) { $To = $From } }

if (-not $SkipTests) {
    Step 'Chạy test của bản mới'
    $code = Py $New @('-m', 'pytest', '-q', '-p', 'no:cacheprovider')
    if ($code -ne 0) { Fail 'test chưa đạt; bot cũ vẫn chạy, chưa đụng gì' }
}

Step 'Preflight trên bản sao vault (bot cũ vẫn chạy)'
Remove-Item -Recurse -Force $VaultCopy -ErrorAction SilentlyContinue
Copy-Tree $Vault $VaultCopy @('/XF', 'worker.lock')
if ($MocChauVnd -gt 0) {
    if ((Py $New @('-m', 'ops_team.set_area_point_rate', '--root', $VaultCopy, '--province', 'Sơn La', '--district', 'Mộc Châu', '--vnd', "$MocChauVnd")) -ne 0) { Fail 'không đặt được giá điểm Mộc Châu trên bản sao' }
}
# Exit code 1 = some check failed ("kế hoạch còn thiếu" from data is reported OK, not a failure).
if ((Py $New @('-m', 'ops_team.preflight', '--root', $VaultCopy, $From, $To)) -ne 0) { Fail 'preflight có LỖI (xem dòng LỖI ở trên); bot cũ vẫn chạy, chưa đụng gì' }
if ($CheckOnly) {
    Step 'Kiểm tra xong: mọi bước đạt. Bot cũ vẫn chạy, chưa đụng gì.'
    Write-Host 'Chạy lại không có -CheckOnly để cập nhật thật.'
    exit 0
}

Step "Backup code và vault -> $Backup"
Copy-Tree $Live "$Backup\code" @('/XD', '.telegram_state', '.supra_state', 'outputs', '__pycache__', '.pytest_cache')
Copy-Tree $Vault "$Backup\vault" @('/XF', 'worker.lock')
if ((Py $New @('-m', 'ops_team.deploy_state', 'save', '--root', $Vault, '--out', $Saved)) -ne 0) { Fail 'không lưu được trạng thái lịch' }

# ---------- 1. Dừng, chép, bật lại; lỗi thì quay lại ----------
$stopped = $false
try {
    Step 'Dừng worker cũ'
    $null = Py $Live @('-c', 'from ops_team.telegram_control_ui import stop_worker; stop_worker()')
    $stopped = $true
    if (-not (Wait-WorkerStopped 300)) { throw 'worker cũ chưa thoát sau 5 phút' }

    Step 'Chép code mới'
    Copy-Tree $New $Live @('/XD', '.git', '.telegram_state', '.supra_state', 'outputs', '__pycache__', '.pytest_cache', '/XF', 'HANDOVER.md')
    if ((Py $Live @('-c', 'import ops_team.telegram_worker, ops_team.drive_import, ops_team.preflight, ops_team.conversation')) -ne 0) { throw 'import code mới lỗi' }
    if ($MocChauVnd -gt 0) {
        if ((Py $Live @('-m', 'ops_team.set_area_point_rate', '--province', 'Sơn La', '--district', 'Mộc Châu', '--vnd', "$MocChauVnd")) -ne 0) { throw 'không đặt được giá điểm Mộc Châu' }
    }

    Step 'Bật worker mới'
    if ((Py $Live @('-m', 'ops_team.deploy_state', 'restore', '--root', $Vault, '--saved', $Saved, '--no-worker')) -ne 0) { throw 'không khôi phục được trạng thái lịch' }
    Start-Worker
    Start-Sleep -Seconds 40
    if (@(Worker-Processes).Count -ne 1) { throw 'worker mới không chạy (hoặc có hơn một worker)' }
    if ((Py $Live @('-m', 'ops_team.deploy_state', 'health', '--root', $Vault)) -ne 0) { throw 'worker mới chưa có heartbeat hoặc báo lỗi' }
}
catch {
    Write-Host "LỖI: $($_.Exception.Message). Đang quay lại bản cũ..." -ForegroundColor Red
    if ($stopped) {
        $null = Py $Live @('-c', 'from ops_team.telegram_control_ui import stop_worker; stop_worker()')
        $null = Wait-WorkerStopped 300
        Copy-Tree "$Backup\code" $Live @()
        # The new code may be broken, so restore controls with the backup's Python files (deploy_state is new: copy it in).
        Copy-Item -LiteralPath (Join-Path $New 'ops_team\deploy_state.py') -Destination (Join-Path $Live 'ops_team\deploy_state.py') -Force
        $null = Py $Live @('-m', 'ops_team.deploy_state', 'restore', '--root', $Vault, '--saved', $Saved, '--no-worker')
        Start-Worker
        Start-Sleep -Seconds 40
        $null = Py $Live @('-m', 'ops_team.deploy_state', 'health', '--root', $Vault)
    }
    Fail "đã quay lại bản cũ. Backup giữ ở $Backup"
}

# ---------- 2. Lịch nhập Drive + gửi 13h ----------
if (-not $SkipSchedule) { Register-DriveTask }

Step 'Xong'
Write-Host "Bot mới đang chạy. Backup: $Backup"
Write-Host 'Kiểm trên Telegram: /status, rồi nhắn "chi phí tuần này", "còn thiếu gì không".'
Write-Host 'Quay lại lớp trò chuyện cũ (không đổi code): mở PowerShell mới, chạy [Environment]::SetEnvironmentVariable("AI_TEAM_CONVERSATION","legacy","User"), rồi mở PowerShell mới nữa và chạy script này với -RestartOnly.'
