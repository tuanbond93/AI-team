# Đưa bản mới vào bot đang chạy (Windows)

Thư mục đang chạy: `$HOME\Desktop\AI team` (có `.telegram_state`). Thư mục thử: `$HOME\Desktop\AI-team-trial` (clone nhánh mới). Chỉ một worker cho một vault; không bật worker thứ hai.

## 0. Kiểm trước (không đụng bot)

```powershell
cd "$HOME\Desktop\AI-team-trial"; git pull origin claude/awesome-ptolemy-t1t3g0
& "$HOME\.venv\Scripts\Activate.ps1"; $env:PYTHONUTF8='1'
python -m pytest -q -p no:cacheprovider
Remove-Item -Recurse -Force "$HOME\Desktop\AI-team-vault-copy" -ErrorAction SilentlyContinue
robocopy "$HOME\Desktop\AI team\.telegram_state" "$HOME\Desktop\AI-team-vault-copy" /E /XF worker.lock /R:1 /W:1
python -m ops_team.set_area_point_rate --root "$HOME\Desktop\AI-team-vault-copy" --province "Sơn La" --district "Mộc Châu" --vnd <giá>
python -m ops_team.preflight --root "$HOME\Desktop\AI-team-vault-copy" 2026-10-01 2026-10-22
```

`preflight` in mỗi lệnh `OK`/`LỖI`. Chỉ đi tiếp khi không có `LỖI` (trừ "kế hoạch còn thiếu" do dữ liệu).

## 1. Backup (bắt buộc, trước khi dừng)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$bk = "$HOME\Desktop\AI-team-backup-$stamp"
robocopy "$HOME\Desktop\AI team" "$bk\code" /E /XD .telegram_state .supra_state outputs __pycache__ /R:1 /W:1
robocopy "$HOME\Desktop\AI team\.telegram_state" "$bk\vault" /E /XF worker.lock /R:1 /W:1
```

## 2. Dừng worker cũ (chờ owner duyệt)

```powershell
cd "$HOME\Desktop\AI team"; & "$HOME\.venv\Scripts\Activate.ps1"
python -c "from ops_team.telegram_control_ui import stop_worker; stop_worker()"
```

Worker có long poll; chờ tối đa vài phút, kiểm `worker_pid` trong trạng thái và tiến trình `python -m ops_team.telegram_worker` đã thoát (`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'telegram_worker'`). Không chép code khi worker còn chạy. `stop_worker` cũng tạm dừng lịch.

## 3. Chép code mới

```powershell
robocopy "$HOME\Desktop\AI-team-trial" "$HOME\Desktop\AI team" /E /XD .git .telegram_state .supra_state outputs __pycache__ .pytest_cache /XF HANDOVER.md /R:1 /W:1
cd "$HOME\Desktop\AI team"; python -c "import ops_team.telegram_worker, ops_team.drive_import, ops_team.preflight"
python -m ops_team.set_area_point_rate --province "Sơn La" --district "Mộc Châu" --vnd <giá>
```

## 4. Khởi động lại và kiểm

```powershell
python -c "from ops_team.telegram_state import BusinessState; s=BusinessState(); s.set_control('worker_enabled', True); s.close()"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Start-Telegram-Worker.ps1"
```

Trên Telegram: `/status` (worker chạy, một nhóm), `/thieu`, `/chiphi`. Sau đó `/plan` và `/enable <mã>` để bật lại lịch 13h.

## Quay lại (nếu bước 3–4 lỗi)

Dừng worker như bước 2, chép lại code từ backup (`robocopy "$bk\code" "$HOME\Desktop\AI team" /E /R:1 /W:1`), nếu vault bị thay đổi thì chép lại `"$bk\vault"`, khởi động như bước 4, kiểm `/status`.

## 5. Tự động mỗi ngày

1. Apps Script (`scripts/apps_script/ExportSupraTabs.gs`): Script Properties, `installTrigger()`.
2. Google Drive for desktop đồng bộ thư mục xuất về máy.
3. Task Scheduler, 13:01 và 13:06 hằng ngày, thư mục `$HOME\Desktop\AI team`:
   `"$HOME\.venv\Scripts\python.exe" -m ops_team.drive_import --folder "<thư mục Drive đã đồng bộ>" --send-scheduled`
   Mỗi ngày chỉ gửi một lần (receipt); Raw cũ không chặn phần Booking.
4. Đầu tháng: `/giugia` để xác nhận giữ giá; cuối tháng gửi bảng kê NCC để đối soát.
