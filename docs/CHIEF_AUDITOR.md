# Chief Auditor trong AI Team

Mở tab **Chief Auditor** tại app hiện có. Bấm **Nạp ví dụ tài chính tổng hợp — 0 API** để xem báo cáo bốn phần và truy vết số. Đây là dữ liệu giả/PLAN. Nút AI chỉ mở khi cấu hình Live sẵn sàng và người dùng cho phép gửi số tổng hợp; Chief dùng profile Coordinator, Auditor gọi riêng và xét đúng phiên bản. Tám vai trò hỗ trợ vẫn dùng được. Không dán prompt gốc vào mã hoặc kho cấu hình; các quy tắc nghiệp vụ được triển khai bằng code.

UI Chief giữ ngân sách 24 lượt gọi/60.000 token dự phòng mỗi lượt thẩm định, cửa sổ 7.500 token/phút dùng chung với các lượt Live trong phiên và deadline tối đa 600 giây. Worker Telegram tiếp tục dùng ngân sách bền hiện hữu. Đây không phải quota/hóa đơn toàn tài khoản hoặc bộ đếm hoạt động từ các ứng dụng khác.

## Khi chưa đủ tài chính hoặc AI bị lỗi

Với booking chưa có tài chính, `/chief` và báo cáo booking trả bằng code, **0 HTTP**, kể cả đang hết quota hoặc chưa có key/model. Câu trả lời mở đầu chưa thể tính P&L, giữ bốn phần và số tài chính N/A. Ba việc cần chốt nêu dữ liệu bằng tiếng Việt: cước/doanh thu và ngày hiệu lực; khối lượng kèm g/kg và số kiện/thùng đúng grain; liên kết đơn→chuyến/chặng cùng chi phí xe, kho, lương và mẫu số B2B/B2C. Bot hỏi bước tiếp theo cụ thể: gửi trước bảng cước/doanh thu đang áp dụng kèm ngày hiệu lực và đơn vị tính. Phần này ghi rõ tính bằng code, Chief AI/Auditor chưa chạy; không tạo phản hồi model hoặc verdict Auditor giả, không trừ số câu hỏi model.

Với nguồn tài chính có số để thẩm định, lỗi Chief hoặc Auditor không xóa bảng số/bằng chứng đã tính. Nếu Chief đã trả lời rồi Auditor lỗi, câu trả lời và câu hỏi Chief được giữ trong RAM nhưng chưa được chấp nhận. Trạng thái kiểm tra là **UNRESOLVED**, chưa có quyết định Auditor; model thực tế chỉ liệt kê các lượt đã hoàn tất. `/details` ghi phiên bản nguồn/nhận định và số HTTP attempt của yêu cầu hiện tại, không đưa bản cũ vào như kết quả mới. Thiếu cấu hình/key vẫn có báo cáo code, không chuyển model hoặc dùng Demo thay nguồn.

Sau HTTP 429 terminal, worker giữ số lượt/token đã dự phòng và tạm ngưng. Header Retry-After dạng số hữu hạn từ 0 đến 86.400 giây được dùng làm mốc sớm nhất cho phép thử lại; không có header hợp lệ thì backoff client từ 60 giây tăng dần, tối đa 900 giây. Đây không xác nhận quota provider đã phục hồi. Không gọi nền/probe hoặc lặp lại vô hạn: chỉ yêu cầu mới của người dùng sau thời gian này mới có thể thử lại. Một retry ngắn có giới hạn sẵn có trong adapter vẫn tính đủ attempt/token; không tăng trần. 401/403 giữ trạng thái lỗi khóa/quyền, không tự được mở theo thời gian. Chỉ lưu metadata cooldown chuẩn hóa (429, thời điểm, số bậc backoff, cờ header hợp lệ), không lưu body/header thô/key/payload. Budget App mặc định vẫn dừng trong một lượt như cũ.

`/reset` xóa đúng hội thoại và trace RAM theo user/topic, kể cả không có model; các chủ đề khác giữ riêng. `/details` sau reset không lấy lại cache báo cáo cũ. Đổi nguồn/kỳ cũng xóa trace cũ. Reset không đụng vault, source, quyền, lịch hoặc bộ đếm quota ngày.

File mẫu: `outputs/chief-auditor/SYNTHETIC_CHIEF_INPUT.xlsx`, có 7 sheet đầu vào. App cho tải mẫu này và ZIP header CSV trống. Không sửa file gốc của người dùng. Muốn dùng file riêng: chọn workbook chuẩn hoặc nhiều CSV theo đúng tên bảng → **Kiểm tra nguồn tài chính** → xem lỗi gắn file/sheet/dòng → **Dùng bản đã kiểm tra cho thẩm định trong phiên**. Thay file hoặc câu hỏi làm mất kết quả AI cũ. Việc nhập trong tab này không đổi nguồn hoặc lịch Telegram.

Trên Telegram, owner gửi workbook kèm `/import chief`, xem mã nguồn trả về rồi chủ động chọn `/set source_id <mã>` và xem `/plan`. Import không tự chọn nguồn, không bật lịch. `/run` tạo báo cáo; `/ask` có yêu cầu đánh giá/PNL/lãi lỗ đi qua Chief + Auditor; `/chief` gọi thẩm định trực tiếp; `/operations`, `/cost`… vẫn hỏi từng chuyên viên. Chào hỏi và hỏi thiếu đầu vào thông thường không ép thành báo cáo dài. `/details` dành cho owner, xem truy vết rút gọn còn trong RAM, không gọi thêm model. Muốn nhập nhiều CSV đầy đủ, dùng tab Chief Auditor; Telegram nhận từng file không tự ghép các nguồn khác phiên.

## Data dictionary

Header ở dòng 1, đúng tên/thứ tự bất kỳ nhưng không thêm cột. Mỗi file CSV đặt tên đúng bảng dưới đây, UTF-8; workbook dùng đúng tên sheet. Số thuần, không dùng chuỗi phân nhóm; ngày ISO `YYYY-MM-DD` hoặc ô ngày Excel. Tiền VND, cước/chi phí đầu vào nguyên đồng. Ô nguồn là giá trị, không nhận công thức, code, macro hoặc external link. Số thiếu khác 0. Không có chuyển đổi ngoại tệ hoặc thuế ngầm định.

| Bảng | Grain và trường |
|---|---|
| `scopes` | Một phạm vi/kỳ/kho. `scopeId`, `period` (YYYY-MM), `warehouse`, `sourceMode` (PLAN/ACTUAL), `populationComplete`, `completenessConfirmed` (true/false), `outsideCostsVnd`, `marginTargetPct` (0..100, để trống nếu chưa chốt), `roundingPolicy` = HALF_UP_ORDER_VND. |
| `orders` | Facts một mã đơn: `orderCode`, `scopeId`, `serviceDate`, `province`, `vehicleType` (B2B/B2C), `weight`, `weightUnit` (g/kg), `pieces`, `cartons`, `quantityGrain` (pieces/cartons), `laborRateId`. Ít nhất số lượng trỏ bởi quantityGrain phải có; số pieces/cartons được nhập phải nguyên ≥1. Không suy đổi thùng sang kiện. |
| `legs` | Thành viên manifest: `legId`, `orderCode`, `tripId`. Một đơn có thể nhiều chuyến/chặng; không lặp cùng đơn trong một manifest. |
| `trips` | Một chuyến/ngày/nhóm tuyến: `tripId`, `scopeId`, `serviceDate`, `routeGroup`, `stage` (1/3), `plate`, `equipmentType` (loại xe, khác B2B/B2C), `capacityKg` >0, `vehicleRateVnd`, `currency` = VND, `allocationBasis` (kg/pieces/cartons), `manifestComplete` (true/false). |
| `rates` | `rateId`, `province`, `validFrom`, `validTo` (hai ngày hiệu lực được tính), `billingBasis` (pieces/cartons/weight_bracket), `minKg`, `maxKg`, `rateVnd`, `currency`. Theo pieces/cartons: VND/đơn vị. Weight bracket: cước cố định VND/đơn trong [minKg,maxKg); maxKg trống là vô hạn. Cần phủ liên tục từ 0, không chồng hiệu lực/nấc. |
| `labor_rates` | `laborRateId`, `validFrom`, `validTo`, `minKg`, `maxKg`, `ratePerKgVnd`, `currency`. Lũy tiến trên kg của từng đơn, từng nấc liên tục từ 0; maxKg trống là vô hạn. Đúng trần cuối đã phủ đủ kg lương, khác cước bracket có trần loại trừ. |
| `pools` | Pool xử lý kho: `poolId`, `scopeId`, `allocationBasis` (kg/pieces/cartons), `amountVnd`, `currency` = VND. Tất cả thành viên cùng scope gồm B2B/B2C. |

`populationComplete=true` là khai báo rằng đã đủ B2B/B2C cùng phạm vi; `manifestComplete=true` tương tự với từng chuyến. Không phải app tự xác minh khai báo này. Thiếu thành viên, đơn cách ly hoặc pool không gán được scope sẽ chặn số chịu ảnh hưởng, không phân bổ lại từ subset hợp lệ. Phạm vi hợp lệ bị người dùng lọc ra được phân biệt với tham chiếu rỗng/không tồn tại. Pool tháng không tự được phân bổ từ phần đơn trước cutoff khi còn đơn sau cutoff trong cùng pool.

## Số học và giới hạn

- Doanh thu B2B một lần/mã; B2C chỉ góp mẫu số chi phí chung, không cần cước doanh thu B2C để tính P&L B2B. Chặng xe giữ riêng từng trip/leg. Facts cùng mã mâu thuẫn bị cách ly toàn bộ, không chọn dòng đầu/cuối; facts giống nhau chỉ ghi nhận doanh thu một lần, chưa gọi là dòng thừa.
- Kg = g/1000 hoặc kg nguồn. Không đoán đơn vị bằng độ lớn. Plate viết hoa/bỏ khoảng trắng; xe chưa rõ biển số vẫn giữ tripId riêng, chi phí tạm tính.
- Turns = max(1,ceil(kg manifest/capacity)); fill = kg manifest/capacity ×100%, chưa chia turns. Đây là ước tính theo tải trên grain chuyến/ngày/tuyến đã khai báo; chưa chứng minh số lượt thực, tải hợp pháp hoặc lịch khả thi. Không lấy kg cả tháng chia một xe để kết luận quá tải. Fill <60% tạo WARNING.
- Xe chặng 1/3 = giá mỗi lượt × lượt ước tính; phân bổ đúng basis đã khai báo, đủ mẫu số B2B/B2C. Handling dùng Largest Remainder nguyên VND, tie-break theo mã ổn định. Thuật toán phân bổ cent của Supra cũ giữ nguyên. Lương tính lũy tiến riêng từng đơn, làm tròn HALF_UP một lần/đơn theo chính sách đã khai báo.
- Cước thiếu/trùng, nấc hở/chồng, đơn vị, chi phí chặng hoặc mẫu số thiếu không được tự thành 0. Có facts B2B cách ly thì tổng chịu ảnh hưởng là N/A; subset được giữ riêng trong cấu trúc `covered_subset`, không dùng làm tổng toàn phạm vi. Có chi phí ngoài ba chặng đã khai báo thì P&L ba chặng là PARTIAL, chưa bao phủ toàn bộ chi phí.
- GP = revenue − ba chặng; doanh thu 0 thì GP% = N/A. Đơn lỗ vẫn CRITICAL dù tổng B2B còn lãi. XANH chỉ khi đủ kiểm tra/thành phần và mục tiêu biên đã khai báo đạt; vẫn UNVERIFIED, không nâng PLAN hoặc ACTUAL do người dùng khai thành chứng từ đã xác minh.

Báo cáo có ba dòng sản lượng/doanh thu/GP%, trạng thái, bảng chỉ số, từng điểm CHƯA ĐƯỢC với bằng chứng/nguyên nhân/ảnh hưởng/bước xử lý-người-hạn, và đúng ba quyết định. Nguyên nhân chưa biết được ghi chưa xác minh và hỏi kiểm chứng; không dựng năm tầng 5 Whys. Chênh lỗ chỉ lượng hóa trong phạm vi có công thức và nguồn, không suy thành thiệt hại/ngày hoặc tiết kiệm thu hồi. Owner/deadline chưa chốt là đề xuất cần xác nhận. Telegram dùng các dòng ngắn thay bảng rộng và chia tin có nhãn tiếp nối, không cắt phản biện.

Phần nhận xét AI trong báo cáo chính giữ các phản biện/câu hỏi/lý do chưa chốt, bỏ lời tự bình luận và đề xuất kỹ thuật/định dạng không có căn cứ nghiệp vụ. Nội dung bị loại được đánh dấu chờ sửa, không coi là đã giải quyết. Chỉ các ý hướng dẫn phụ được giới hạn; phản hồi gốc còn trong RAM, `/details` có nhãn rút gọn. Lọc cách trình bày không đổi quyết định hoặc tự chấp nhận một version sai.

Booking DC bổ sung sản phẩm J và tổng kg/GHN L theo hai rule owner-confirmed độc lập; xem [BOOKING_PRODUCT_UNITS.md](BOOKING_PRODUCT_UNITS.md) và [BOOKING_GHN_WEIGHT.md](BOOKING_GHN_WEIGHT.md). L tính một lần/GHN, thiếu/lỗi/conflict/partial dispatch scope thì tổng toàn phạm vi N/A, subtotal phần đủ nguồn giữ riêng. Chưa có kiện/thùng hoặc đủ doanh thu/chi phí ba chặng; Chief từ booking vẫn VÀNG/PARTIAL, tài chính N/A và intake 0 HTTP. Mã lặp không chứng minh lỗi/đơn hợp lệ; unknown không là chưa giao; delivered chưa xác nhận SLA. Ngày nhận/ngày xuất và source/rule version còn nguyên. Không tự đổi nguồn, quyền, khóa hoặc lịch 13:00.

[Bốc xếp DC](DC_HANDLING.md) là component riêng theo approved business catalog DPAPI, chỉ TRANSFER và một tổng L/GHN tại một mức giá E. Có số handling không làm đủ finance gate: stage2/totalCost/revenue/GP vẫn N/A, intake vẫn 0 HTTP. Whole component N/A khi còn nhóm giữ riêng; subtotal exact theo mức giá không phải tiền nghiệm thu/thanh toán. Cache context gắn SHA catalog, không dùng metric cũ với policy mới.

## Kiểm chứng local

Trong shared venv, `python -m pytest -q tests/test_chief.py tests/test_chief_ui_telegram.py -p no:cacheprovider --basetemp=.test-tmp-chief-qa`. HTTP dùng giả lập; fixture chuyển vault/state/UI sang tmp_path. Kiểm chứng đủ các đường số học, importer, file XLSX mẫu, UI demo, source DPAPI, Telegram Chief/Auditor đúng version và trần Free Pilot cũ. Chưa có tài chính/chứng từ thực để nghiệm thu P&L ACTUAL, nguyên nhân gốc hoặc tối ưu xe/lương. Chưa triển khai cloud/receiver hoặc gọi dịch vụ thật trong đợt code này.
