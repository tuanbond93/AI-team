# Đưa bản mới vào bot đang chạy (Windows)

Thư mục đang chạy: `$HOME\Desktop\AI team` (có `.telegram_state`). Thư mục thử: `$HOME\Desktop\AI-team-trial` (clone nhánh mới). Chỉ một worker cho một vault; không bật worker thứ hai.

## Cách nhanh: một script (khuyên dùng)

```powershell
cd "$HOME\Desktop"; if (-not (Test-Path AI-team-trial)) { git clone -b claude/awesome-ptolemy-t1t3g0 https://github.com/tuanbond93/AI-team AI-team-trial }
cd "$HOME\Desktop\AI-team-trial"; git pull origin claude/awesome-ptolemy-t1t3g0
# Lần 1: chỉ kiểm tra, không đụng bot
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\Deploy-Bot.ps1 -CheckOnly
# Lần 2: cập nhật thật + đặt lịch 13:01/13:06 (thay đường dẫn Drive)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\Deploy-Bot.ps1 -DriveFolder "<thư mục Drive đã đồng bộ>"
```

Script làm đúng các bước 0–5 bên dưới:
- Kiểm quyền và chạy test.
- Preflight trên bản sao vault, gồm cả tin nhắn tự nhiên.
- Backup, rồi dừng worker và chờ tiến trình thoát hẳn.
- Chép code, bật worker, kiểm heartbeat.
- Bật lại lịch 13h chỉ khi mã kế hoạch owner đã duyệt không đổi. Nếu đổi, script báo cần `/plan` rồi `/enable`.
- Đặt task "AI Team - Drive import 13h".

Lỗi ở bất kỳ bước nào sau khi dừng worker: script tự chép lại code cũ, khôi phục trạng thái và bật lại worker cũ.

Thêm `-MocChauVnd <giá>` nếu vault chưa có giá điểm Mộc Châu. Giá chỉ lưu trong DPAPI, không vào Git.

Các chế độ khác:
- `-ScheduleOnly -DriveFolder ...`: chỉ đặt lịch.
- `-RestartOnly`: dừng/bật lại an toàn.

### Sẵn sàng cho 13h ngày mai

1. Apps Script: đã chạy `installTrigger()`, xuất file trước 13:00.
2. Google Drive for desktop đang chạy và đồng bộ đúng thư mục `-DriveFolder`.
3. Máy bật, đăng nhập đúng tài khoản Windows lúc 13:00 (DPAPI cần tài khoản này), có mạng, không ngủ.
4. Trên Telegram, `/status` báo lịch **ĐANG BẬT**. Nếu script báo kế hoạch đổi: gửi `/plan` rồi `/enable <mã>`.
5. Đầu tháng: `/giugia` (hoặc gửi bảng giá mới) để lịch không bị dừng vì chưa xác nhận giá.

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
