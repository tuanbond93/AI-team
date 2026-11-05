# Trạng thái đội AI vận hành theo mảng

Rà soát ngày 05/11/2026 trên nhánh `claude/awesome-ptolemy-t1t3g0` từ commit `1044d28` (lúc đó 706 test đạt, 29 bỏ qua). Bảng dưới là hiện trạng lúc rà soát; phần đã làm xem mục **Tiến độ** và **Sau đợt này**. Toàn bộ chỉ kiểm bằng dữ liệu tổng hợp, chưa chạy trên vault thật.

Cột "Thiếu do": **code** nghĩa là làm được ngay; **dữ liệu** nghĩa là cần người gửi file; **quyết định** nghĩa là cần owner chốt quy tắc (ghi ở `docs/OWNER_QUESTIONS.md`, nhóm J).

## Bảng đối chiếu

| Mảng | Đã làm được (đã kiểm bằng code) | Còn thiếu | Thiếu do | Ai cung cấp |
|---|---|---|---|---|
| Chi phí | `/chiphi`: bốc xếp DC, phí điểm, FTL, xe GXT + lương giao, overlay doanh thu, tổng dự kiến trước VAT khi đủ nguồn | (1) Báo cáo 13h và ngữ cảnh model chỉ có bốc xếp DC, còn in cứng "Tổng chi phí và lãi lỗ: N/A" dù `/chiphi` tính được hơn → hai nơi cho hai kết quả khác nhau. (2) Chi phí/đơn vị theo tuyến và theo khách | (1) code. (2) quyết định: cách phân bổ từng khoản xuống tuyến/cửa hàng | (2) Owner: J1 |
| Kế toán | `/ketoan`: dự kiến trước/sau VAT 8%; đối soát bảng kê NCC (VAT, trùng, ngày không chạy, tiền xe GXT theo giá ngày) | (1) Doanh thu và lãi gộp khi đủ nguồn chưa hiện trong `/ketoan`. (2) Nhật trình km chưa có schema/mẫu nhập. (3) Đối soát thực chi chờ bảng kê thật | (1) code. (2) code (mẫu) + dữ liệu. (3) dữ liệu | (2) NCC gửi nhật trình hằng tháng (câu 17). (3) Kế toán gửi bảng kê cuối tháng |
| Nhân sự | `/nhansu`: lương giao GXT theo xe/ngày (đơn, kg, tiền), điểm BC tách riêng | Theo từng người; OT; năng suất; tỷ lệ lỗi | dữ liệu (không có chấm công, câu 25); mẫu nhập dùng lại hợp đồng `workforce` cũ | Owner/điều phối: danh sách tài xế–xe–ngày; chấm công nếu có |
| Đội xe | `/xe` (tỷ trọng Supra theo kg), tỷ lệ tải trong `/kpi`, kịch bản ca xe thủ công (`telegram_vehicle_forecast`) | Tỷ lệ xe rỗng, giờ chờ, chi phí/chuyến, dự báo nhu cầu xe tự động | Xe rỗng, giờ chờ: dữ liệu (Raw không có km rỗng/giờ chờ). Chi phí/chuyến: quyết định (chuyến = mã chuyến Raw, xe/ngày hay lượt thực tế là ba khái niệm khác nhau). Dự báo: quyết định (phương pháp) | NCC/điều phối: nhật trình có km rỗng, giờ chờ. Owner: J2, J3 |
| Vận hành | `/sla` (D+1/D+2 theo `time_ket_thuc_chuyen`) | Sản lượng theo ngày (đơn, sản phẩm, kg) và tồn đọng (đã xuất chưa giao thành công, quá hạn/chưa tới hạn) chưa có agent | code: dùng đúng quy tắc SLA và nguồn đã có | — |
| KPI | `/kpi`: SLA %, chi phí/kg, chi phí/đơn, tỷ lệ tải theo mục tiêu owner | Xu hướng theo kỳ, cảnh báo khi lệch mục tiêu; OT; tỷ lệ xe rỗng | Xu hướng, cảnh báo: code. OT, xe rỗng: dữ liệu | Như Nhân sự / Đội xe |
| Kiểm toán | `/kiemtoan`: tính lại độc lập (tổng = tổng từng xe/ngày, kg khớp Raw, Raw đủ ngày, điều kiện công bố tổng) | Đối soát chứng từ | dữ liệu: bảng kê NCC (đã có bộ đọc), nhật trình km | Kế toán, NCC |
| Điều phối | `/tongquan`: một dòng mỗi mảng | Chưa thành báo cáo ngày: chưa nêu ưu tiên, người phụ trách, dữ liệu cần bổ sung | code; người phụ trách lấy theo nhãn đã có trong code (CS bổ sung cột L, kế toán gửi bảng kê, NCC gửi nhật trình…) | — |
| Vai trò AI cũ (`agents.py`, `/team`, `/ask`, `/cost`…) | Chỉ chạy khi có model. Ngữ cảnh lấy từ `booking_context` (bốc xếp DC) hoặc từ schema kho cũ khi nguồn là file kho | AI và lệnh code có thể nói hai con số khác nhau, vì AI không thấy bảng chi phí của `/chiphi` | code: đưa đúng số code (cùng hàm) vào ngữ cảnh AI; AI chỉ giải thích | — |

## Thứ tự làm

Đi từ giá trị cao nhất mà không bị chặn vì dữ liệu:

1. **Chi phí → báo cáo 13h và ngữ cảnh model dùng cùng bảng chi phí `/chiphi`.** Giá trị cao nhất: báo cáo hằng ngày không còn mâu thuẫn với lệnh.
2. **Vận hành:** sản lượng và tồn đọng.
3. **Điều phối:** báo cáo ngày có ưu tiên, người phụ trách, dữ liệu cần bổ sung.
4. **KPI:** xu hướng so với kỳ trước và cảnh báo lệch mục tiêu.
5. **Kế toán:** lãi gộp khi đủ nguồn; schema và mẫu nhật trình km.
6. **Nhân sự, Đội xe:** schema và mẫu nhập (tài xế–xe, chấm công, nhật trình), trả N/A rõ ràng và nói ai cần gửi gì.
7. **Vai trò AI:** chung nguồn số với code.

## Tiến độ

| # | Mảng | Trạng thái | Commit |
|---|---|---|---|
| 1 | Chi phí: báo cáo lịch + ngữ cảnh model dùng bảng `/chiphi` | xong (dữ liệu giả); chi phí theo tuyến chờ J1 | xem git log |
| 2 | Vận hành: sản lượng + tồn đọng (`/vanhanh`) | xong (dữ liệu giả); định nghĩa chờ J5 xác nhận | xem git log |
| — | Bảo mật: agent không in biển xe (mã V1/V2, số dòng) | xong | xem git log |
| 3 | Điều phối: `/tongquan` thành báo cáo ngày | xong (dữ liệu giả); phụ trách chờ J8; lịch 13h giữ mẫu cũ | xem git log |
| 4 | KPI: xu hướng so kỳ trước + cảnh báo lệch mục tiêu | xong (dữ liệu giả); OT, xe rỗng chờ dữ liệu; J6, J7 chờ xác nhận | xem git log |
| 5 | Kế toán: lãi gộp khi đủ nguồn + nhật trình km (schema, mẫu, nhập, đối chiếu) | xong (dữ liệu giả); chờ bảng kê và nhật trình thật, biểu giá km | xem git log |
| 6 | Đội xe (`/doixe`) + Nhân sự (theo người, chấm công): schema, mẫu, nhập, N/A rõ ràng | xong phần code (dữ liệu giả); chờ nhật trình, danh sách tài xế–xe, chấm công; J2, J3, J4, J10, J11 | xem git log |
| 7 | Vai trò AI chung số với code; Kiểm toán đối soát chứng từ | xong (dữ liệu giả, model giả) | xem git log |

## Sau đợt này (05/11/2026)

| Mảng | Còn thiếu | Thiếu do | Ai cung cấp |
|---|---|---|---|
| Chi phí | Chi phí/đơn vị theo tuyến và cửa hàng | quyết định | Owner: J1. Có bảng kê thì thêm nguồn thực chi |
| Kế toán | Đối soát thực chi, tiền theo km | dữ liệu | Kế toán: bảng kê NCC. NCC: nhật trình km. Owner: biểu giá km (câu 17) |
| Nhân sự | Theo người, OT, năng suất, lỗi | dữ liệu + quyết định | Owner/điều phối: danh sách tài xế–xe, chấm công. Owner: J10, J11 |
| Đội xe | Km rỗng, giờ chờ thật; chi phí/chuyến; dự báo | dữ liệu + quyết định | NCC: nhật trình. Owner: J2, J3, J4 |
| Vận hành | — (đã có) | — | Owner xác nhận J5 |
| KPI | OT, xe rỗng | dữ liệu | Như Nhân sự / Đội xe. Owner: mục tiêu (`/muctieu`), J6, J7 |
| Kiểm toán | Chứng từ thật | dữ liệu | Kế toán, NCC |
| Điều phối | Người phụ trách chính thức; dùng mẫu này cho báo cáo 13h | quyết định | Owner: J8; duyệt đổi mẫu báo cáo lịch |
| Vai trò AI | Chất lượng câu chữ với model thật | chưa cấu hình model | Owner cấu hình model khi muốn |

## Sau câu trả lời J1–J11 (05/11/2026)

- **Chi phí:** `/tuyen` theo tỉnh/huyện (J1).
- **Đội xe:** chi phí/chuyến = tiền xe ÷ xe/ngày (J2); tỷ lệ tải là chỉ số xe (J4).
- **KPI:** cảnh báo cả khi xấu đi so với kỳ trước (J6, J7).
- **Nhân sự:** lương theo ID người được gán đơn (J10).
- **Còn chờ:**
  - file tồn kho và hàng sắp về để dự báo xe (J3);
  - owner review công thức chấm công (J11);
  - dữ liệu thật: bảng kê NCC, nhật trình km, danh sách tài xế có mã đơn, chấm công, biểu giá km.
