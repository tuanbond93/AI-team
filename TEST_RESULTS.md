# Kiểm chứng bản chuyển giao

## Nguồn trước khi xuất repo

- Source snapshot: `a9f991014d495074af1d01ec0965505b16f5202b`.
- 240/240 kiểm thử liên quan được người điều phối chạy độc lập, đạt trong 59,82 giây trên Windows với quyền event loop phù hợp.
- Đối chiếu ba khoảng nguồn local đã xác minh grain GHN/ngày, kg/rate, phần DC nằm trong gói, hai chặng, ngoại lệ số lượt, đơn khai trương chưa có km/giá và exact audit replay.
- Cập nhật thêm một ngoại lệ số lượt bằng cấu hình được kiểm ba khoảng: chỉ pool trung chuyển thay đổi đúng theo giá đã duyệt, các thành phần khác giữ nguyên.
- Không gọi AI thật hoặc gửi Telegram thử trong nghiệm thu. Không coi API Live, dữ liệu kế toán thực chi hoặc P&L cuối đã được chứng minh bởi các test này.
- Kết quả full suite của các source cũ không được dùng làm kết quả full suite cho snapshot mới.

## Repo mới

Các file source/test được so byte-for-byte với workspace gốc. Bản này có fixture Excel demo và Chief tổng hợp; vault và dữ liệu riêng tư được loại khỏi gói xuất.

**654/654 kiểm thử toàn bộ đạt trong 110,11 giây** trên chính bản repo sạch, Windows/Python 3.12, ngày 27/10/2026. Lệnh:

```powershell
python -m pytest -q --tb=short --show-capture=no -p no:cacheprovider --basetemp=.test-tmp-github-transfer-final
```

Lượt kiểm tra đầu phát hiện thiếu `data/DU_LIEU_DEMO.xlsx` do quy tắc bỏ dữ liệu local. Đã bổ sung duy nhất fixture demo này sau khi so sánh đủ sáu sheet với `demo_tables()` trong code, không sửa mã để bỏ qua test. Bộ test toàn bộ được chạy lại và đạt. Các thư mục test/vault thật không được đưa vào commit.

## Clone Linux (cloud), 27/10/2026 — Claude

Python 3.13, venv riêng cài đúng `requirements.txt`. Lệnh như trên với `PYTHONUTF8=1`.

- Lần đầu: 454 đạt, 178 fail, 22 error. Nguyên nhân: 168 test cần DPAPI Windows; 29 test đọc workbook/JSON Supra thật trên Desktop owner. Không có assertion failure do logic.
- Sửa test: DPAPI giả chỉ trong `tests/conftest.py` khi không phải Windows (app vẫn fail closed); test cần nguồn Supra local skip có lý do.
- Sau sửa và thêm 2 test đồng bộ rule layer: **627 đạt, 29 skip, 0 fail** (~35 giây).
- Lưu ý: con số 654/654 ở trên đạt được vì máy owner có sẵn nguồn Supra local; trên clone sạch 29 test đó không chạy được.

## Nhánh claude/awesome-ptolemy-t1t3g0, 29/10/2026 — Claude (cloud, Linux)

670 đạt, 29 skip (nguồn Supra local), 0 fail. Phiên trên máy owner báo 685/685 đạt trên Windows/Python 3.12 ở commit 2e7a0a8. Kiểm trên vault tạm của owner bằng `/thieu`/Excel; chưa triển khai vào worker đang chạy.
