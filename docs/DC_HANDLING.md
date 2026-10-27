# Phí bốc xếp DC theo điều khoản owner

Component deterministic riêng tại `ops_team/supra/booking_handling.py`, được nối vào booking snapshot, báo cáo code và metric context của tám vai trò. Đây là **phép tính theo điều khoản owner xác nhận**, chưa là số tiền nghiệm thu/thanh toán; không đưa vào totalCost, stage2, revenue, GP hoặc margin. `/chief`, `/run` và báo cáo intake vẫn **0 provider HTTP**, kể cả khi component có số dương.

## Nguồn và quy tắc

Giữ độc lập ba grain: J cộng sản phẩm theo DO trong phạm vi; L là kg cả GHN tính một lần; bốc xếp chỉ tính L một lần cho GHN đủ điều kiện **TRANSFER** trong catalog phí. Đây là enum điều kiện phí đã duyệt, độc lập cơ chế xe vật lý: TP Lai Châu đi thẳng vẫn thuộc diện bốc xếp. Xem [cơ chế xe và paired source](VEHICLE_PAIR_SCHEMA.md). Không dùng tổng kg booking × giá hoặc đổi sản phẩm thành kg. Không lấy L từ AB/cột khác, không forward-fill ô tổng.

Ngày chọn giá là E/Ngày xuất. Catalog đã được owner duyệt cung cấp giá, ngày đóng inclusive, địa bàn và ngoại lệ; code không chứa 35 địa bàn hoặc store ID thật. Component chỉ gồm bốc xếp tại DC, không gồm kho/hub, vận tải hoặc giao theo điểm/gói. Phân loại TRANSFER/DIRECT dùng routing xác nhận, không đoán từ tải xe, tên mô hình hoặc tỉnh.

Một GHN chỉ được tính khi toàn bộ DO trong nguồn đã pin có ngày thuộc cùng kỳ/cutoff, routing đã khớp và cùng một khoảng giá E; tổng L hợp lệ duy nhất. Nhiều DO/ngày trong cùng khoảng giá có thể hợp lệ. Qua mốc giá khi chưa có kg tách, mixed TRANSFER/DIRECT, unknown mapping, thiếu ngày/phạm vi hoặc L thiếu/lỗi/mâu thuẫn của TRANSFER thì giữ GHN riêng, không chọn first/last/max, chia kg hoặc tính nhiều lần.

DIRECT không áp dụng khoản này: L hỏng của DIRECT vẫn làm kg booking chưa đủ theo rule L cũ, nhưng **không làm thiếu bốc xếp vốn không áp dụng**. Toàn phạm vi DIRECT đủ xác định trả component 0 (không áp dụng), không giả đó là tiền thanh toán. L bằng 0 thực của TRANSFER vẫn là nhóm hợp lệ với chi phí 0.

Tổng component `component_vnd` là N/A khi còn nhóm chưa chốt. `eligible_vnd`, `eligible_kg` và các bucket theo giá chỉ là phần đủ điều kiện, không đại diện toàn component. Các counter có thể trùng một GHN; không cộng lỗi để suy số đơn. Dòng thiếu GHN cũng không được gán chi phí 0.

## Matching và catalog

Ưu tiên **STORE trước DISTRICT**. STORE được gắn bằng H đã đối chiếu nguồn riêng, không cần đưa I/tên cửa hàng vào snapshot. STORE khớp ID nhưng khác tỉnh/huyện dự kiến giữ unknown; thiếu H ở địa bàn có ngoại lệ không tự bỏ qua precedence. Mã H là định danh giữ nguyên, không suy hay in/log. Native numeric H cùng None không bị pandas ép thành `100.0` rồi bỏ sót override.

Tên địa bàn chỉ chuẩn hóa NFC/case/khoảng trắng và alias prefix hành chính rõ ràng (`H.`/`Huyện`, `TP.`/`Thành phố`, `TX.`/`Thị xã`, `T.`/`Tỉnh`), cùng alias Hạ Hoà/Hạ Hòa được xác nhận. Không fuzzy, bỏ dấu hoặc gán huyện lân cận. Catalog trùng sau chuẩn hóa/chồng khoảng hiệu lực bị từ chối; runtime cũng không chọn rule đầu để giải quyết mâu thuẫn.

Catalog/rates/store IDs chỉ lưu trong **DPAPI business snapshot**; plaintext descriptor chỉ có policy/version/hash. SHA catalog kiểm lại khi import/load và tham gia provenance/phiên bản báo cáo. Cache model cũng gắn SHA catalog: thay catalog đã duyệt khi bytes Sheets không đổi phải tạo metric context mới, không ghép provenance mới với chi phí cũ. Thay đổi này không làm reset bộ đếm API ngày.

## Decimal và cách trình bày

Kg và giá được tính bằng Decimal chính xác; không làm tròn DO, GHN hoặc tổng component. Báo cáo VND bỏ phần thập phân bằng 0 (owner chốt 27/10/2026) và giữ đủ mọi chữ số thập phân khác 0 của số nguồn; không làm tròn. Không áp quy tắc làm tròn thanh toán chưa được xác nhận. Ví dụ số `...915.700` không tự biến thành `...916` và gọi là actual.

Báo cáo giữ J/L cũ, thêm component toàn phạm vi, subtotal đủ nguồn, kg/cost từng mức, DIRECT excluded, số nhóm giữ riêng và nguồn dòng. Phần thoại dùng footer riêng cho handling để không nói “chưa có khối lượng/chi phí nào” sau khi đã trình bày metric code. Toàn bộ vẫn chưa đủ total cost/P&L, kiện/thùng, xe/chuyến/drop hoặc SLA.

## Import và giữ cấu hình qua pipeline

[DC_HANDLING_SCHEMA.md](DC_HANDLING_SCHEMA.md) quy định chính xác staging v3 **11 cột A/B/C/E/F/G/H/J/L/N/O**, policy, catalog và descriptor. Hàm RAM-only `catalog_from_owner_confirmations(...)` chuyển JSON business đã chốt cùng store-match DPAPI thành catalog; hàm không đọc/ghi file hoặc lấy key.

`ingest_minimized_snapshot(...)` kiểm source/schema/policy/hash/version/hiệu lực, header, sheet_row và retrieved_at trước khi đổi pointer; record schema 4 giữ catalog và tối thiểu business evidence dưới DPAPI. Các v1/v2/record cũ vẫn đọc, handling N/A. Không tự nâng source cũ thành số bốc xếp đã xác nhận.

Sau lần nạp v3, full37 CSV refresh lấy lại catalog đã duyệt của **cùng spreadsheet/tab/gid/range**; connector qua Assistant cũng nhận `state` để dùng đúng catalog này. Khác pin không tự áp; source/header sai bị chặn. Refresh thiếu cột khi đã có policy không được lưu thành snapshot hỏng. Không tự đọc JSON business ở thư mục ngoài để thay policy. Cấu hình/rules mới phải đi qua import rõ ràng.

Migration không đổi facts/source_id/paired/settings/approved plan/lịch13h/API usage hoặc gửi báo cáo. Cancel/validation error giữ pointer cũ. Import không restart worker/UI. Freshness 15 phút và chống snapshot cũ ghi đè bản mới giữ nguyên; parent xuất fresh rồi kích hoạt nguyên tử riêng, không sửa timestamp để vượt guard.

## Model và bằng chứng

Chỉ gửi metric tổng hợp, điều khoản tóm tắt, quality counter và provenance; không raw GHN/SO/DO/H, địa bàn/store catalog hoặc dòng nguồn. Với handling Free Pilot:

- `metrics` là bảng array theo `metric_fields`; `formula_ref` và `source_evidence` là index 0-based vào `formula_catalog` / `metric_evidence_catalog`.
- Nhãn `@STATUS` khôi phục bằng `label_templates` và hậu tố key metric. Các nhãn còn lại giữ nguyên. `source_row_template`, khi có, ghép prefix + giá trị row catalog + suffix để khôi phục chính xác source ref; không có thì dùng nguyên ref.
- Count/sample flags, label/formula/unit/value và metric_context giữ nguyên. Policy versions/catalog version+hash chỉ lưu một lần ở quality records thay vì lặp trong source_provenance; timestamp và source digest vẫn giữ. Có thể khôi phục đúng provenance ban đầu.
- Ba history excerpt giữ đúng nội dung/hợp đồng cũ (question≤400, assistant≤900 ký tự). Lượt thứ tư được kiểm với đủ ba history, rồi Operations→Auditor nhận nguyên answer gồm requests/hypotheses/proposals và đúng task/version. Không cắt answer/bằng chứng hoặc nâng cap.

Cap vẫn 7.500 token/phút, output1.500 Free Pilot, 60 calls/120.000 token dự phòng/ngày và cơ chế 429 cũ. Input khác quá lớn vẫn bị chặn theo cap, không tự đổi model/provider/gói.

## Control đối chiếu được phép

Kết quả đối chiếu với dữ liệu thật đã được kiểm trong vault tạm và chỉ lưu ở bản ghi local của owner (`HANDOVER.md` tại workspace gốc); không ghi số liệu doanh nghiệp vào repo công khai. Tổng component vẫn N/A khi còn nhóm giữ riêng; subtotal không phải tiền nghiệm thu/thanh toán.

Mẫu [SYNTHETIC_DC_HANDLING_REPORT.txt](../outputs/chief-auditor/SYNTHETIC_DC_HANDLING_REPORT.txt) là dữ liệu giả để minh họa subtotal lẻ `0,100 VND` và tổng component N/A.
