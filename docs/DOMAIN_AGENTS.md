# Agent theo mảng trên dữ liệu thật Supra

Các agent dưới đây chạy bằng code trên đúng nguồn Booking + Raw đã ghim, cùng bảng chi phí với `/chiphi`. Không gọi AI. Thiếu dữ liệu thì để N/A và nói lý do. Gọi bằng lệnh hoặc nhắn tự nhiên (đã @bot hoặc trả lời bot). Chỉ owner xem được số.

Hiện có 9 mảng; 4 mảng có từ trước, 5 mảng làm thêm đợt này:

| Mảng | Lệnh | Nhắn tự nhiên, ví dụ | Tính gì | Còn thiếu |
|---|---|---|---|---|
| Chi phí | `/chiphi` | "chi phí tuần này" | (đã có) | |
| SLA | `/sla` | "SLA hôm qua" | (đã có) | |
| Đội xe | `/xe` | "xe ngày 15/10" | (đã có) | |
| Dữ liệu thiếu | `/thieu` | "còn thiếu gì không" | (đã có) | |
| **Vận hành** | `/vanhanh` | "tồn đọng tuần này", "sản lượng hôm qua" | Số đợt (GHN + ngày E) và sản phẩm theo ngày xuất; kg cho cả khoảng (cùng hàm báo cáo 13h). Tồn đọng = đã xuất chưa có DELIVER thành công trên Raw, chia quá hạn / chưa tới hạn theo D+1/D+2 đã chốt; dùng chung phân loại đợt với `/sla`. Quá hạn liệt kê số dòng Sheet. | Owner xác nhận định nghĩa tồn đọng (J5) |
| **Kế toán** | `/ketoan` | "đối soát kế toán tháng 10" | Dự kiến trước VAT, VAT 8%, sau VAT (chỉ khi mọi phần đủ nguồn). Đối soát bảng kê NCC: VAT đúng 8%, tổng sau VAT, trùng biển+ngày, ngày tính tiền không thấy xe chạy, xe chạy chưa có trên bảng kê, biển lạ. Tiền xe GXT so với giá ngày đã duyệt, tính một lần mỗi xe/ngày. | Bảng kê NCC thật (kế toán gửi cuối tháng); nhật trình km cho FTL/NCC |
| **Nhân sự** | `/nhansu` | "lương giao tuần này" | Lương giao GXT theo xe/ngày: số đơn, kg, tiền theo bậc kg từng đơn. Đơn đi 2 chuyến cùng xe/ngày tính 1 lần; giao thẳng không tính lương. Điểm giao BC tách riêng. | Danh sách nhân viên/tài xế theo xe để tính theo từng người |
| **KPI** | `/kpi` | "KPI tháng 10", "tỷ lệ tải xe" | SLA %, chi phí/kg, chi phí/đơn, tỷ lệ tải. Chấm đạt/chưa đạt theo mục tiêu owner đặt. Chi phí/kg và chi phí/đơn chỉ có khi tổng chi phí đã chốt, không dùng tổng tạm. | Mục tiêu (`/muctieu`), tải trọng xe S (`/taitrong`) |
| **Kiểm toán** | `/kiemtoan` | "kiểm toán lại số", "số có khớp không" | Tính lại độc lập: tổng tiền xe/lương = tổng từng xe/ngày; phần Supra không vượt tiền xe; kg bảng chi phí khớp kg Raw; Raw cập nhật đủ; tổng chỉ công bố khi đủ nguồn; còn nhóm giữ riêng; dấu thời gian hàng loạt (SLA). | Chưa thay đối soát chứng từ |
| **Điều phối** | `/tongquan` | "tổng quan tuần này" | Một dòng cho mỗi mảng trong một tin. | |

## Owner đặt mục tiêu và tải trọng

```
/muctieu                      xem mục tiêu hiện tại
/muctieu sla 95               SLA đúng hạn ≥ 95%
/muctieu chiphi_kg 1500       chi phí ≤ 1.500đ/kg
/muctieu chiphi_don 20000     chi phí ≤ 20.000đ/đơn
/muctieu tai 80               tỷ lệ tải ≥ 80%
/taitrong 29C12345 1.25t      tải trọng xe (kg hoặc tấn); ưu tiên hơn cột T Booking
```

- Mục tiêu mới thay mục tiêu cũ. Bản cũ vẫn giữ kèm giờ và tin nguồn.
- Nhắn thường kiểu "đặt mục tiêu SLA 95" chỉ được nhắc gửi đúng lệnh, không lưu.

## Bảng kê NCC

Kế toán gửi file `.xlsx` hoặc `.csv` vào nhóm, chú thích `/import ncc`.

- Bot tự tìm dòng tiêu đề có các cột: biển số, ngày, thành tiền (chưa VAT). Thêm được: thuế GTGT, sau VAT, số chuyến, km, ghi chú.
- Dòng tổng cộng được bỏ qua. Dòng thiếu biển, ngày hoặc tiền được giữ riêng, không tính là 0.
- File được mã hóa DPAPI trong vault, mỗi tháng một file.
- Nếu file thật dùng tên cột khác: thêm tên đó vào `COLUMNS` trong `ops_team/supra/ncc_invoice.py` (một dòng), có test.

## Tệp

- `ops_team/supra/domain_agents.py`: năm agent.
- `ops_team/supra/ncc_invoice.py`: đọc, lưu, đối soát bảng kê.
- `ops_team/kpi_targets.py`: mục tiêu và tải trọng.
- `manual_cost_rules.gxt_daily_cost` có thêm chi tiết `by_group` theo xe/ngày. Tổng không đổi; tổng từng xe/ngày bằng tổng chung, có test.

Các vai trò dùng AI cũ (`/accounting`, `/workforce`, `/cost`, `/fleet`…) vẫn giữ nguyên và chỉ chạy khi có cấu hình model. Riêng `/kpi` nay là agent KPI bằng code ở trên; vai trò KPI của AI vẫn gọi được qua đội AI (`/team`).

## Báo cáo 13h và AI dùng chung bảng chi phí (05/11/2026)

- Báo cáo lịch thay dòng cố định "Tổng chi phí và lãi lỗ: N/A…" bằng tình trạng lấy từ đúng bảng `/chiphi` cho kỳ từ ngày 1 tới ngày chốt: đã đủ nguồn, hoặc N/A với cùng lý do `/chiphi` nêu.
- Chính sách chi phí đã duyệt không phủ đủ kỳ: N/A kèm lý do; không tự thu hẹp phạm vi.
- Tin nhóm không in số tiền mới (OWNER J9 đề xuất A); owner xem số bằng `/chiphi`.
- Ngữ cảnh AI có `cost_report` copy nguyên các khoản từ bảng đó. Chỉ gửi khi owner tự hỏi; lượt chạy theo lịch thì không. AI chỉ giải thích, không tính lại.
- Kiểm: cả kỳ = hai nửa kỳ cho từng khoản cộng được; khoản N/A không thành 0; lý do giống hệt `/chiphi`.
