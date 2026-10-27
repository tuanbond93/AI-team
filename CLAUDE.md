# AI team: hướng dẫn duy trì

Đây là ứng dụng vận hành logistics Python/Streamlit, không phải dự án Vnstock. Claude được người dùng giao tiếp tục sửa mã; các ghi chú cũ yêu cầu Sol là bên duy nhất sửa mã không còn hạn chế vai trò coding của Claude.

Đọc `docs/ENGINEERING_HANDOFF.md` và `PROMPT_CLAUDE_CODE.md` trước khi sửa. Không xây lại MVP từ đầu.

- Phép tính do code, LLM giải thích và đề xuất. Thiếu dữ liệu giữ N/A, không thành 0.
- Không đổi nguồn/scope/giá/ngoại lệ/approved plan chỉ để vượt validation. Chỉ dùng xác nhận owner đã có hoặc đầu vào mới rõ ràng.
- S/AG là các chặng khác nhau; loại tải Booking không tự chứng minh tải vật lý. Tuân thủ schema và ngoại lệ có ràng buộc nguồn.
- Không commit `.telegram_state`, `.supra_state`, DPAPI, khóa, CSV thật, Excel giải trình thật, nhật trình, payload hoặc báo cáo doanh nghiệp.
- Không đọc khóa từ ứng dụng khác, lấy cookie, vượt xác minh Google hoặc lách chính sách công ty.
- Test dùng fixtures/HTTP giả; không gửi Telegram hoặc gọi AI thật trong test. Một worker cho một vault; không tạo poller thứ hai.
- Không tự chi tiền, điều xe, đổi nhân sự, public deploy hoặc tự tăng gói dịch vụ.
- Với thay đổi worker, chuẩn bị rollback và toàn bộ quyền cần cho stop/import/restart trước khi dừng. Với cấu hình được đọc mỗi request, ưu tiên atomic update không restart khi phù hợp.

Người dùng cần câu trả lời tiếng Việt ngắn, dễ hiểu: kết luận, số liệu đủ nguồn, việc cần làm và thiếu gì. Để hash, role nội bộ, mã metric và trace dài ở phần audit.
