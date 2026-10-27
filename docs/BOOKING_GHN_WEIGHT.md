# Tổng kg/GHN từ cột L — 26/10/2026

Owner đã xác nhận cùng nguồn booking DC PHÚ THỌ, gid 0: **L là tổng kg của cả mã GHN, gộp các DO**. Không hỏi lại đơn vị, không cộng L theo DO, điền xuống dòng trống hoặc thay bằng AB/Weight(từng SO)/KG-Lot. Quy tắc khối lượng độc lập với J: J là số sản phẩm cộng theo DO trong phạm vi.

`WEIGHT_RULE` tại `ops_team/supra/booking_weight.py`, version `supra-ghn-weight-owner-2026-10-26-v1`, provenance `USER_CONFIRMED_IN_CODEX`, ngày 26/10/2026, B=mã GHN, L=kg, grain `GHN_TOTAL_ACROSS_DO`. Không tạo Telegram message ID hoặc coi đã đối soát chứng từ. Việc nhãn cột L ghi “tổng cửa hàng” không thay thế xác nhận của owner về grain GHN.

## Cách ghi nhận

- Mỗi GHN trong phạm vi chỉ có một tổng L hợp lệ: ghi nhận một lần. Các tổng giống nhau về giá trị Decimal (ví dụ 0.001 và 0.0010) vẫn tính một lần, giữ nguyên mọi dòng nguồn.
- Dòng DO trống L không làm thiếu kg nếu GHN đã có một tổng hợp lệ. Cả nhóm trống mới là thiếu, không đổi thành 0. Số 0 thực vẫn là 0.
- Một L không trống nhưng lỗi/không rõ nghĩa (ví dụ `#REF!`) làm kg của cả GHN chưa chốt, kể cả dòng khác có số hợp lệ. Hai tổng L khác nhau cũng giữ chưa chốt; không chọn first/last/max hoặc cộng chúng.
- Chỉ nhận số kg không âm. Decimal dấu chấm và nhóm nghìn rõ nghĩa như `6,256.30` được đọc chính xác. Dấu phẩy đơn không có phần thập phân rõ nghĩa như `1,234`, định dạng pha `1.234,56`, số âm, khoa học hoặc lỗi ô được giữ cần kiểm tra. Không đoán dấu phân tách hoặc đổi gram. Các giá trị kg/model metric lưu chuỗi Decimal chính xác; báo cáo hiển thị ba chữ số thập phân.
- So phạm vi **toàn bộ các dòng cùng GHN trong nguồn đã pin** với kỳ và cutoff theo Ngày xuất E. Có DO ngoài kỳ/cutoff hoặc chưa biết Ngày xuất thì giữ riêng toàn bộ kg của GHN đó. Không chia tổng L cho phần DO đang lọc. Các DO khác ngày nhưng đều trong phạm vi vẫn được nhận bình thường.
- Qty J thiếu/lỗi/trùng khóa không tự chặn L đã đủ; L lỗi không làm mất số sản phẩm J hợp lệ. Thiếu/sai mã GHN thì không gán kg vào mã khác.

`booking.weight_kg` là tổng toàn phạm vi, chỉ có giá trị khi đủ các GHN. Còn nhóm chưa đủ thì **N/A**. `booking.eligible_weight_kg` chỉ là subtotal phần GHN đủ nguồn; nhãn không cho dùng thành tổng. Các bộ đếm tách GHN đủ nguồn, thiếu, lỗi, nhiều tổng khác nhau, chỉ thuộc một phần kỳ/cutoff và dòng thiếu mã. Một GHN có thể vướng nhiều loại lỗi; không cộng các counter để suy số GHN hợp lệ.

Các chỉ tiêu là **khối lượng nguồn khai báo**, không phải chứng nhận B2B actual hoặc khối lượng đã giao thành công. Không suy kiện, thùng, CBM, xe/chuyến/drop/SLA, doanh thu, chi phí hoặc GP. `/chief`, `/run`, report intake tiếp tục dùng code 0 HTTP, tiền và GP N/A. Các câu hỏi vai trò có metric kg và policy khi được người dùng gọi, không tự thêm lượt AI.

## Snapshot và migration

- Schema staging mới: `supra-quantity-weight-source-intake-v2`, chín header business **A/B/C/E/F/G/J/L/N**. `confirmed_business_rules` giữ nguyên policy sản phẩm; `confirmed_weight_rules` phải khớp chính xác policy khối lượng đã xác nhận. Descriptor gồm raw hash, hash plaintext minimized, retrieved_at thật, 37 cột nguồn và số dòng.
- Dùng cùng `ingest_minimized_snapshot(...)` hoặc CLI `--minimized` như [hướng dẫn sản phẩm](BOOKING_PRODUCT_UNITS.md). Hàm nhận DPAPI staging được caller chỉ định, kiểm schema/hash/policy/source pin/range/sheet_row/time trước pointer commit; không mở raw CSV có PII. Ghi record schema 3 cùng `quantity_rules` và `weight_rules` độc lập. `business_evidence` chỉ thêm L vào tám cột business cũ, giữ sheet_row/giá trị nguồn.
- Full CSV và connector phải đúng source pin và 37 header trước khi áp mapping J/L. Không lấy kg từ cột khác. Trường `source_ghn_weight` lưu L nguyên giá trị được phép; numeric/None không tạo JSON NaN hoặc bị đổi 0.
- Staging v1 tám cột, record quantity schema 2 và record cũ bốn trường vẫn đọc; kg N/A nếu không có L/rule. Không tự áp rule L hồi tố vào các số ở cột khác. Rule/hash/version kiểm lại khi load sau restart.
- Migration không chọn source, sửa facts/settings/paired/plan/lịch13h/API usage, restart hoặc gửi Telegram. Cancel/lỗi không đổi pointer. Guard snapshot 15 phút và không thay bản mới bằng cũ vẫn giữ; activation phải dùng export thực, không sửa retrieved_at để vượt guard. Parent thực hiện activation/runtime QA riêng.

## Bằng chứng gửi vào model

Không gửi raw GHN/SO/DO, source values hoặc group records. Metric source samples/count/sample flags và version nguồn/rule còn nguyên. Với context có KG trong Free Pilot, metadata định nghĩa được tách không mất thông tin: `metric_definition_fields` mô tả thứ tự `definition` của từng metric; `source_evidence` trỏ `metric_evidence_catalog`, rồi `source_row_catalog`. Khôi phục được đúng label/formula/unit và tham chiếu/count/flag của metric code ban đầu. Mã evidence đầu ra vẫn là **key metrics**, không phải alias catalog.

Auditor nhận nguyên answer/requests và đúng task version. Không nâng cap 7.500 token/phút, 1.500 output Free Pilot, 60 calls/120.000 token/ngày; không cắt bằng chứng, phản biện hoặc câu hỏi để vượt cap. Payload quá lớn khác vẫn bị chặn theo cơ chế cũ.

## Kiểm tra và control nguồn

35 ca mới kiểm grain một lần/GHN, anchor lặp, blank trong nhóm, toàn nhóm thiếu, `#REF!`, conflict, cutoff/khác kỳ/unknown date, Decimal/zero, không fallback AB, độc lập J/L, v2 migration/source/policy/hash/cancel, full CSV multiline và connector numeric+None, restart/plan13h/0HTTP. Adapter thật với HTTP/đồng hồ giả kiểm ba lượt Operations có history và Operations→Auditor, metadata khôi phục đầy đủ và exact review task. Các kiểm tra cũ về quantity/cap/429 tiếp tục giữ.

Control staging được phép, kiểm trong vault tạm: tháng 10 có **9.453 dòng DO / 4.007 GHN / 1.862.473 sản phẩm**. **3.941 GHN đủ kg**, subtotal **743.374,002 kg**; 61 GHN thiếu tổng, 3 GHN có lỗi, 2 GHN có tổng mâu thuẫn, không có partial-scope GHN trong lượt control. Tổng kg toàn phạm vi **N/A**. Raw hash staging `aa4ab5c585f4af5fa0672d464d8a74478f7178524f5cac8f71ea553b9ca72fdb`. Đây là kiểm tính đúng quy tắc từ nguồn, không phải đối soát tài chính hoặc activation production. Không đưa staging/dữ liệu thật vào Git.

Mẫu [SYNTHETIC_GHN_WEIGHT_REPORT.txt](../outputs/chief-auditor/SYNTHETIC_GHN_WEIGHT_REPORT.txt) chỉ là dữ liệu tổng hợp để xem cách hiển thị N/A và subtotal riêng.
