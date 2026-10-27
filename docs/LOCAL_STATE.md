# Dữ liệu và trạng thái local

## Repo mới và workspace đang vận hành

Repo này là bản code sạch. Thư mục gốc đang vận hành nằm tại `$HOME\Desktop\AI team` trên máy Windows của chủ dự án. Xuất bản GitHub không đổi source, worker, lịch hoặc trạng thái ở đó.

Các dữ liệu riêng của đúng ứng dụng này được giữ tại:

- `.telegram_state/`: pairing, settings, nguồn tối thiểu, receipts, controls và các file policy/proof DPAPI của app.
- `.supra_state/`: trạng thái nghiệp vụ nếu đã dùng.
- `outputs/supra-vehicle-intake/`: xác nhận owner và bằng chứng kiểm chứng được bảo vệ, các metadata QA local.
- `HANDOVER.md` tại workspace gốc: bản ghi vận hành, mốc mới ở đầu; phía dưới là lịch sử.
- Các báo cáo và Excel giải trình local của owner, không nằm trong repo.

Chỉ đọc những nguồn local này trong phạm vi owner đã giao. Không tìm thông tin xác thực ở Codex, Claude, trình duyệt hoặc ứng dụng khác. Không in token, khóa, private config, payload hay raw records vào log/chat/Git.

## Cùng máy, cùng tài khoản Windows

Có thể đọc DPAPI của đúng app trong RAM bằng hàm sẵn có. Kiểm owner/group/source pins/approved plan trước khi chạy nghiệp vụ. Đọc `HANDOVER.md` mới nhất tại workspace gốc và các descriptor/proof để biết chính xác quy tắc đang active.

Một clone ở thư mục khác có `ROOT` trạng thái khác. Đừng khởi động một bot thứ hai hoặc tự copy/link vault sang clone. Dùng clone cho phát triển và test; chỉ đưa bản sửa đã nghiệm thu vào workspace vận hành bằng quy trình có rollback. Không sửa hoặc restart ứng dụng đang chạy chỉ để thử code.

## Máy hoặc tài khoản Windows khác

DPAPI không phải định dạng backup di động. Không upload `.telegram_state` lên GitHub hoặc giả định có thể giải mã trên Linux/cloud. Cần owner cấu hình thông tin xác thực của chính app trong UI bảo mật, pairing và nguồn được cấp quyền tại môi trường mới. Trong lúc chưa có, vẫn chạy demo/test tổng hợp; phân tích dữ liệu thật để N/A.

## Chuyển 24/7

Owner cho phép thiết kế lưu cấu hình/bí mật mã hóa và trạng thái công việc để phục hồi; payload AI vẫn chỉ trong RAM. Việc di chuyển sang secret manager/backend mới cần đúng quyền nguồn và phạm vi đã duyệt. Không đưa plaintext secrets/config/payload vào repo, webhook logs hoặc lịch sử chat; không dùng DPAPI như kho bí mật cloud.
