# AI team

Trợ lý vận hành logistics bằng tiếng Việt: chi phí, đối soát kế toán, nhân sự, xe và KPI. Phép tính do Python thực hiện; tám vai trò AI phân tích bằng chứng, hỏi chéo và tổng hợp đề xuất để quản lý duyệt.

Đọc [bàn giao kỹ thuật](docs/ENGINEERING_HANDOFF.md) và [prompt cho Claude Code](PROMPT_CLAUDE_CODE.md) trước khi tiếp tục.

## Chạy local

Windows, Python >=3.10; môi trường đã kiểm chứng dùng Python 3.12. Dùng môi trường Python chia sẻ của tài khoản, không tạo lại nếu đã có:

```powershell
python --version
# Chỉ chạy dòng sau nếu chưa có $HOME\.venv
python -m venv "$HOME\.venv"
& "$HOME\.venv\Scripts\Activate.ps1"
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8506 --server.headless true --browser.gatherUsageStats false
```

Mở `http://127.0.0.1:8506`, chọn dữ liệu demo để kiểm tra mà không cần khóa API. Có thể dùng `Start-Telegram-Pilot.cmd`; launcher kiểm tra port, không tự tắt ứng dụng khác.

Không cần cài Vnstock hoặc API chứng khoán cho dự án logistics này.

## Các phần chính

- Nhập Excel/CSV, ánh xạ cột, một kỳ hoặc so hai kỳ, truy về sheet/dòng nguồn.
- Coordinator, Accounting, Cost, Operations, Workforce, Fleet, KPI và Auditor. Kết luận kiểm toán gắn đúng phiên bản; bất đồng chưa giải quyết được giữ lại.
- Provider adapters cho cấu hình Live và demo tách biệt. Lỗi API/hạn mức không chuyển ngầm sang dữ liệu giả.
- Telegram có pairing owner/group, một worker, bộ nhớ/cấu hình DPAPI và gửi theo receipt. Lịch chỉ chạy khi owner bật và có kế hoạch phù hợp.
- Phân tích Booking/Raw, kg theo xe/ngày, bốc xếp theo GHN/ngày, giá theo loại tải Booking và các ngoại lệ có bằng chứng nguồn.

## Phạm vi bản GitHub

Đây là bản mã sạch lấy từ source commit `a9f991014d495074af1d01ec0965505b16f5202b` của workspace gốc. Lịch sử Git riêng tư không được đưa vào lịch sử repo mới.

Repo có mã nguồn, kiểm thử, tài liệu, `data/DU_LIEU_DEMO.xlsx` và bộ Excel Chief tổng hợp cho test. **Không có token, khóa model, kho DPAPI, dữ liệu doanh nghiệp thật, bảng kê NCC hay báo cáo tài chính thật.** Không có các dữ liệu đó không phải là lỗi cài đặt.

DPAPI gắn với tài khoản Windows. Clone trên máy khác chưa thể khôi phục phiên Telegram/nguồn đang chạy. Xem [trạng thái local](docs/LOCAL_STATE.md); không đưa kho trạng thái lên Git để di chuyển máy.

## Kiểm thử

```powershell
& "$HOME\.venv\Scripts\Activate.ps1"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
python -m pytest -q --tb=short --show-capture=no -p no:cacheprovider
```

Các test dùng dữ liệu giả và transport giả; test DPAPI cần Windows. AppTest và event loop cần quyền loopback/socketpair. Một lần bị kẹt trong sandbox không chứng minh mã lỗi; ghi rõ môi trường rồi kiểm tra bằng runtime được cho phép. Không dùng kiểm thử để gửi Telegram thật hay gọi model thật.

Kết quả kiểm chứng bản chuyển giao nằm trong [TEST_RESULTS.md](TEST_RESULTS.md).

## Tài liệu nghiệp vụ và schema

- [Telegram](docs/TELEGRAM_ASSISTANT.md)
- [Hai nguồn Booking / Raw](docs/VEHICLE_PAIR_SCHEMA.md)
- [Chi phí thủ công](docs/MANUAL_COST_POLICY_SCHEMA.md)
- [Doanh thu và phân bổ](docs/REVENUE_ALLOCATION_SCHEMA.md)
- [Quy tắc owner và ngoại lệ](docs/OWNER_RULE_EXCEPTIONS_SCHEMA.md)
- [Auditor](docs/CHIEF_AUDITOR.md)

Local Streamlit và worker Windows chưa phải dịch vụ cloud 24/7. Thiết kế Cloudflare/Apps Script thuộc bước tiếp theo, cần nguồn dữ liệu được công ty cho phép, quản lý bí mật phù hợp và nghiệm thu trước triển khai.
