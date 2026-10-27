# Supra Account AI vận hành local và nghiệm thu

Module được tích hợp trong **cùng app AI Team**, tab **Supra Account AI**. Bản review local: http://127.0.0.1:8503. Nhấp đúp `Start-Supra-Review.cmd`; launcher ASCII chạy được bằng Windows PowerShell 5.1, dùng shared venv. Không push, merge hoặc triển khai production/public. Không thay đổi launcher 8501 hiện hữu.

## Baseline và phần tái sử dụng

Workspace ban đầu chưa có Git. Đã tạo baseline local `baseline/ai-team-free-pilot`, commit `bcc3a68cf4869df9f6e7ce31b15d498a7a470019`, rồi nhánh `feature/supra-account-ai`. Baseline có 105 test đã đạt trước đợt Supra. Không đưa nguồn khách hàng, SQLite, báo cáo, ảnh, khóa hoặc thư mục QA của chat khác vào Git.

Hạ tầng hiện hữu: Python 3.12, Streamlit/pandas/openpyxl/httpx; 8 vai trò theo phiên, Auditor ràng buộc phiên bản, profile Live/Free Pilot, mô phỏng và báo cáo nguồn. Không có database, SSO, role directory, event/follow-up engine, GHN/GTalk connector, OpsPilot module hoặc CI/CD đã cấu hình. Không phát hiện công cụ GHN/GTalk trong các connector phiên này. Không đọc rộng tài khoản/credential hoặc tìm key từ app khác.

Rà soát bổ sung **thư mục nguồn Supra** phát hiện một app React/TypeScript + engine Python và publication JSON đã có: `supra_engine.py`, `supra_store.py`, `src/data/multiMonthSupraData.json`, `rateMaster.json`, cùng Bridge Google Sheets. Đây là repo nguồn riêng, không phải AI Team và không được sửa/publish trong đợt này. Đã tái sử dụng publication và hàm validator chính xác của engine đó qua connector đọc, chỉ chiếu Phú Thọ. Engine lịch sử giữ vai trò tính order/trip nhiều tỉnh; engine mới xử lý topology PLAN 26/30 ngày/5T khoán tháng của workbook, không tính lại hoặc thay lịch sử đã publish. SQLite mới chỉ bổ sung incident/approval/audit, không thay master/publication store hiện có.

Supra dùng lại app/navigation/design system, shared venv, adapter LLM/strict output, Auditor contract, Free Pilot budget và bảo vệ bí mật phiên. Thêm domain Decimal riêng vì mô hình cũ dùng float cho dashboard tổng hợp, chưa đáp ứng bảo toàn phân bổ tài chính. Registry `CustomerOperationsAgent` ánh xạ SUPRA/PHU_THO sang Supra Account AI; không sửa 8 vai trò cũ hoặc mặc định đưa dữ liệu Supra vào câu hỏi chung.

## Cấu trúc mã

- `ops_team/supra/ingestion.py`: đọc hai tài liệu được chỉ định, chống file nén quá lớn, kiểm tra sheet/đơn vị/khóa, nguồn file + SHA256 + ô hoặc dòng CSV vật lý. Không thực thi công thức/tài liệu như chỉ dẫn. Formula cache không làm căn cứ xác minh tiền hoặc ACTUAL.
- `domain.py`, `finance.py`: Decimal, làm tròn 0,01 VND, residual theo largest remainder. Một pool phân bổ một lần; net last-mile đã trừ Ecom không trừ tiếp. Xe 5T hybrid là một pool 57 triệu/tháng, chia các tuyến theo kg. Phí 8T theo chuyến; hub chỉ phân bổ kg qua hub. NCC sensitivity thay pool last-mile, không cộng chồng.
- `ledger.py`: API sổ chi tiết doanh thu → pool → dòng phân bổ. Hỗ trợ khách hàng/tỉnh/hub/tuyến/ngày/xe/điểm/đơn. Tổng phân bổ phải bằng pool, ID duy nhất, cùng kỳ vận hành, chi phí 0 cũng cần dòng có nguồn. Drop dùng ID duy nhất để không cộng lặp theo dòng đơn. Các sổ route/day, xe, điểm là sổ thay thế/đối soát, không cộng vào nhau.
- `reconciliation.py`: 7 kiểm tra baseline, gồm 6 vấn đề yêu cầu và kiểm tổng mạng. Trạng thái CONFIRMED chỉ xác nhận số học tài liệu; CORRECTED_WITH_EVIDENCE là sửa số học kế hoạch, không là xác thực giá/hóa đơn.
- `capacity.py`, `daily.py`: giới hạn năng lực, tải/Ecom/peak/thể tích, lịch bốc DC, giờ nhận hàng; thiếu đo đạc trả NEEDS_DATA. Quy tắc timeline cho giao trễ, DC trễ, chờ/tồn quá ngưỡng, chi phí chuyến/điểm bất thường, quá tải/thiếu tải và quay vòng muộn. Không tự đặt ngưỡng nghiệp vụ chưa được cấp và không tuyên bố tối ưu toàn cục.
- `service.py`: API nghiệp vụ có scope và permission, PLAN/ACTUAL riêng; nhập có phiên bản, Finance attestation, truy vấn sổ chi tiết và ma trận pilot 14 ngày.
- `store.py`, `workflow.py`: SQLite nghiệp vụ, transaction snapshot + audit cùng commit/rollback, hash chain, idempotency, four-eye approval, shadow execution/rollback/outcome. Adapter GTalk chỉ DRY_RUN_NOT_SENT. Outcome phải dùng hai sổ ACTUAL đã Finance xác minh; chênh lệch quan sát không tự biến thành tiết kiệm nhân quả.
- `api.py`: HTTP read-only cùng engine, chỉ bind 127.0.0.1:8502 khi chủ động bật; Bearer token RAM, no-store, không log request/header/token; không POST tác động vận hành.
- `agent.py`, `ui.py`: agent chuyên tài khoản + Auditor độc lập dùng model hiện hữu khi người dùng chủ động đồng ý. Không fallback hoặc tự gọi model từ nguồn nhập. Năm khu vực dashboard theo yêu cầu, cùng kiểu giao diện hiện tại.
- `legacy.py`: connector read-only nguồn lịch sử có sẵn. Chỉ compile hàm `validate_result` đã review/pin SHA256 của engine nguồn trong namespace tối thiểu; không import toàn module, chạy script sync/publish hoặc đọc credential. Engine đổi hash thì dừng để review. Snapshot 10/2026 hiện qua validator; 06/08 không qua gate hiện tại nên chặn, không sửa ngầm nguồn. Chỉ lưu/hiển thị Phú Thọ, không lấy tổng cước xe đa tỉnh làm cước riêng của tỉnh.

## Đọc nguồn và chạy P&L

1. Mở bản review, chọn **Supra Account AI → Đọc hai tài liệu Supra đã chỉ định**. Có thể tải lên đúng một .xlsx và một .docx thay thế. Xem preview, xác nhận nhập/lưu phiên bản.
2. Nguồn hiện tại có 12 luồng, 130 key điểm tạm, 33.372 kg/ngày. Chưa có mã cửa hàng thật, hóa đơn hoặc nhật ký pilot đã nhập. Mọi con số từ đề án là PLAN/UNVERIFIED, kể cả khi tài liệu có chữ “đã phê duyệt”.
3. Chọn 26/30 ngày cùng tháng. Chi phí cố định xe 5T giữ 57 triệu/tháng; chỉ phí biến đổi/theo chuyến tăng theo ngày. Không nhân tất cả khoản chi bằng 30/26, không ép kết quả khớp benchmark 240,8.
4. Xem Route P&L, phân bổ pool, nguồn từng ô, kiểm tra baseline; tải JSON nghiệp vụ đầy đủ. Chi phí/drop actual để trống khi chưa có drop. Chi phí/xe kế hoạch-ngày là tổng chi phí tài khoản chia 13 xe-ngày kế hoạch, không giả là P&L từng biển số. Chi phí/điểm PLAN chia đều trong tuyến được ghi rõ là giả định, không phải CTS cửa hàng thực.
5. Hồ sơ chỉ nêu tải danh định, thiếu giấy tờ tải hợp pháp, thể tích, GPS/vòng quay, giờ nhận điểm và báo giá/SLA. Bảng capacity không xác nhận tính khả thi thực tế từ công suất lý thuyết.
6. Trong Executive Overview có **Nguồn lịch sử Supra sẵn có**: chủ động đọc snapshot 10/2026 từ project nguồn, kiểm tra rồi liên kết bản đọc riêng. Snapshot có dữ liệu quan sát và chi phí mô hình, không là ACTUAL Finance hoặc pilot tương lai. Nguồn tháng 10 được cập nhật 16/10/2026 và chưa xác nhận đủ kỳ; tập B2B thiếu customer ID độc lập. Không trộn vào các KPI PLAN, sổ ACTUAL hoặc tự gửi lịch sử này tới LLM.

Kết quả Decimal đối chiếu workbook hiện tại:

| Kỳ PLAN | Doanh thu VND | Chi phí VND | Lãi/lỗ VND |
|---|---:|---:|---:|
| 26 ngày | 484.160.976,00 | 626.848.728,80 | -142.687.752,80 |
| 30 ngày | 558.647.280,00 | 714.517.764,00 | -155.870.484,00 |

Đây là P&L phạm vi chi phí khai báo, chưa là lợi nhuận ròng kế toán/thuế. Contribution margin PLAN tạm coi mọi pool ngoài 5T là biến đổi; cơ cấu hub/cost behavior phải Finance xác nhận. Sổ chi tiết trả CM trống nếu cost behavior chưa rõ.

Giá xe 8T: 1.474.307 × 2 / 13.713 = khoảng 215,02 VND/kg; nếu mẫu số 13.700 kg là khoảng 215,23. Không dùng 178 và không cộng khoản sửa lần hai vì pool 8T đã lấy nguyên cước/chuyến. Khoảng cách 18/36 km, lịch chuyến 2 lấy thẳng, điều khoản NCC và tải Ecom vẫn mở.

## ACTUAL và quyền trong phiên

Phiên local mặc định chỉ xem. Chủ máy tin cậy có thể cấu hình tên quản lý và token tối thiểu 24 ký tự trong RAM ở **Incident & Approval**; nhập token để dùng quyền Finance/approval shadow. Đây không thay thế SSO/danh tính GHN và chưa dùng cho máy chủ nhiều người. Không thực hiện bước cấp quyền hoặc gửi dữ liệu ra model trong nghiệm thu trình duyệt.

CSV route/day bắt buộc: `date,route,kg,pcs,drops,on_time,price_pcs,document_ref,dc,linehaul,hub,last_mile,delivery_labor,extra`. Ngày ISO, tiền VND, số thuần, đủ sáu phí kể cả 0. Blank khác 0; pcs hóa đơn không tự suy từ kg. Trạng thái nhập luôn UNVERIFIED, nguồn/hash/dòng vật lý do adapter gắn. Nhập thay sổ actual của phiên bản, không append trùng; duplicate ngày/tuyến hoặc chứng từ/bundle không có dòng phân bổ riêng bị chặn. Finance cần biên bản đối soát để tạo version VERIFIED; app ghi nhận attestation của người có quyền, không tự đọc/xác thực chứng từ bên ngoài.

Sổ JSON chi tiết có ba mảng `revenue`, `pools`, `allocations`. Các cột và khóa được khai báo trong `ledger.py`/`service.py`; trạng thái/source gửi trong file không cấp quyền. Mỗi revenue là một dòng tính cước, có customer/region/date/route/order_id và nguồn gốc. Pool có cost_id, category, amount, behavior FIXED/VARIABLE, period_start/end. Allocation có allocation_id, cost_id, revenue_id, amount. Upload UI chỉ nhận SUPRA/PHU_THO; phần chi phí dùng chung phải có biên bản phân bổ từ Finance. Helper Decimal `allocate` kiểm chứng bảo toàn chia Supra/Ecom; không đọc P&L khách hàng khác trong API.

Timeline JSON là danh sách event_id có timestamp ISO kèm timezone, route/hub, source; chỉ sử dụng ngưỡng người dùng đã cung cấp. Không nhập ngưỡng thì báo thiếu dữ liệu. Chưa kết nối booking/GHN/GPS; không có worker tự chạy 14 ngày. Ma trận hiện tại bắt đầu 26/10/2026, kết thúc 08/11/2026, 168 ngày-tuyến, 0 dòng được xác minh. Sổ điểm 1.820 dòng và sổ xe chưa đối soát, nên không tự đánh dấu pilot hoàn tất hoặc tiết kiệm đã xác minh.

## API và lưu dữ liệu

Bật API chỉ đọc bằng nút trong tab Supra; token được hiển thị trong ô password của phiên, không ghi đĩa hoặc log. Các endpoint:

```text
GET /v1/supra/pnl?mode=PLAN&days=26&route=DH
GET /v1/supra/pnl?mode=ACTUAL&start=2026-10-26&end=2026-11-08
GET /v1/supra/detail?group_by=vehicle_id
GET /v1/supra/reconciliation
Authorization: Bearer <token trong RAM>
```

Đổi nguồn/version dừng API để tránh trả dữ liệu cũ; bật lại khi cần. Xóa phiên thu hồi API/token và đóng kết nối DB; không xóa lịch sử nghiệp vụ đã lưu. Khởi động lại mất các khóa, quyền phiên, chỉ dẫn/skill và kết quả AI RAM. Source data lưu ở `.supra_state/supra.sqlite3`, schema version 1; migrations chỉ tạo hai bảng snapshots/events, không chạm bảng của app cũ. Các phiên bản nguồn có thể xem lại; DOCX chỉ lưu source/hash/citations cần thiết, không lưu toàn văn hoặc prompt. Bản gốc hai tài liệu không bị sửa; cần giữ bản gốc để tra cứu evidence sau này.

Kiểm tra hash chain phát hiện thay đổi không nhất quán, không là WORM/chữ ký chống chủ máy viết lại toàn file. Business DB local chưa mã hóa riêng; quyền phiên chưa là danh tính doanh nghiệp. Production cần IDP/SSO, ACL quản trị, phân tách host/tenant, mã hóa/backup/retention và audit trust anchor phù hợp. Không có secret store hoặc job 24/7 được tự bật.

## Kiểm thử và rollback

```powershell
& "$HOME\.venv\Scripts\Activate.ps1"
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q -p no:cacheprovider
python verify_supra.py
python verify_demo.py
```

Test Supra đọc đúng hai file local người dùng cung cấp; một số case dùng dữ liệu tổng hợp rõ nhãn để thử điều kiện biên, LLM giả và quyền/shadow. Không dùng dữ liệu tổng hợp để nhận P&L ACTUAL hay nghiệm thu pilot thật. Máy CI khác cần mount hai file private và cùng thư viện; chưa có CI/CD hoặc quyền cloud được cấu hình.

Rollback triển khai: dừng riêng process review 8503 bằng Ctrl+C ở launcher, giữ nguyên DB/nguồn; quay về nhánh baseline bằng checkout/worktree riêng nếu cần (không tự chạy thao tác bỏ thay đổi). Chưa push/merge/deploy. Rollback dữ liệu: chọn version cũ trong UI, không ghi đè bản gốc hoặc xóa audit. Rollback proposal chỉ là trạng thái shadow, không điều xe/đổi hợp đồng thực tế.

## Gate còn mở

Kiểm thử kỹ thuật local đạt không chứng minh dữ liệu kế hoạch đúng, pilot đã hoàn tất, tối ưu toàn cục, SLA thực tế, tiết kiệm nhân quả, quyền GTalk hoặc an toàn production. **Đã có publication lịch sử từ Data Supra/booking của project nguồn**, không yêu cầu cấp lại như thể chưa tồn tại. Còn cần raw/manifest đúng kỳ, customer ID và quyền đồng bộ nguồn đó, chứng từ Finance/phân bổ, mã điểm/xe/đơn đã xác minh, tải hợp pháp/thể tích/GPS, hợp đồng NCC/SLA, GTalk sandbox và danh tính approver để qua các gate còn mở. Không cần chặn đọc PLAN/snapshot đủ điều kiện hoặc kiểm thử độc lập hiện tại để chờ các nguồn này.
