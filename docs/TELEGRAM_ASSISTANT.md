# Telegram AI Team — vận hành local

## Chief Auditor và báo cáo tài chính

Yêu cầu đánh giá qua `/ask` (đánh giá/PNL/P&L/lãi lỗ/chi phí…) có nguồn được chuyển cho Chief; nguồn tài chính có số mới cần Coordinator profile + Auditor độc lập. Booking thiếu tài chính được kiểm bằng code, 0 HTTP, không phụ thuộc quota/key/model. `/chief` gọi trực tiếp. Booking trả bản ngắn gồm số tính được, phần chưa chốt và bước tiếp; `/details` giữ đủ bảng/quy tắc/truy vết. Canonical Chief giữ bốn phần: tóm tắt, bảng chỉ số, điểm CHƯA ĐƯỢC và ba việc cần owner chốt. Greeting/câu hỏi thiếu đầu vào và hỏi từng vai trò vẫn trả lời thông thường.

Booking chưa đủ finance nên giữ VÀNG/PARTIAL: doanh thu, total cost, kiện và GP chưa chốt. J, L và component bốc xếp chỉ tính khi có mapping/điều khoản đủ nguồn; subtotal có nhãn riêng. Owner có thể nhập workbook tài chính bằng `/import chief` rồi chủ động chọn source_id và xem `/plan`; import không bật lịch. Mẫu, hợp đồng 7 bảng và cách dùng tab Chief Auditor: [hướng dẫn Chief](CHIEF_AUDITOR.md). `/details` giữ task/version + exact decisions trong RAM; không nâng PLAN/nguồn owner khai thành verified ACTUAL. Luồng `/team` kho/Supra cũ vẫn riêng, không tự đổi grain hoặc công thức.

[Paired vehicle v4](VEHICLE_PAIR_SCHEMA.md) bổ sung `/xe` hoặc `/vehicles [YYYY-MM-DD] [Vn]` bằng code0 HTTP và ngữ cảnh tổng hợp riêng cho `/fleet`. S đại diện không là xe vật lý; tỷ lệ kg nguồn theo xe/ngày không tự phân bổ chi phí hoặc suy chia sẻ AG. Cơ chế xe độc lập điều kiện phí bốc xếp: TP Lai Châu đi thẳng vẫn thuộc diện phí đã duyệt. Lịch paired cần cả Booking và Raw được nhận trong15 phút; chỉ refresh Booking không làm Raw mới. Nhập v4 lần đầu cần xem lại plan với secondary source; không tự bật lịch hoặc tự gửi tin.

Nếu AI gặp 429/timeout/lỗi quyền, báo cáo code vẫn còn; Chief trả lời trước khi Auditor lỗi được giữ nhưng chưa chấp nhận, không dựng verdict Auditor. Worker chỉ thử lại 429 ở yêu cầu mới sau cooldown, giữ nguyên usage ngày và không gọi nền. Retry-After hợp lệ là mốc client có thể thử, không phải lời hứa quota đã reset; 401/403 cần sửa khóa/quyền. `/details` phản ánh các model thực sự hoàn tất và HTTP attempt hiện tại. `/reset` xóa trace RAM đúng user/topic, không lấy cache cũ trở lại và không thay nguồn/lịch/quota.

## Cách đọc phản hồi trong nhóm

Hội thoại theo vai trò trả lời bằng tiếng Việt thông thường: kết luận trước, tối đa 5 ý xử lý chính và câu hỏi cụ thể về phần còn thiếu. Chief nguồn tài chính đủ bốn phần; Booking trả bản ngắn và giữ báo cáo đầy đủ trong `/details`, gồm ngày nhận bản chụp/ngày xuất mới nhất. Mọi phản biện, giới hạn và yêu cầu bổ sung vẫn hiển thị; nếu dài, tin tiếp có nhãn **Phần bổ sung (tiếp)**.

Chưa có nguồn số liệu thì phản hồi chỉ hướng dẫn chung, chưa đưa mức lấy mẫu, tiết kiệm hay định mức nhân sự/xe làm đề xuất có căn cứ. Nếu model không hỏi đầu vào, giao diện bổ sung một câu phù hợp vai trò để bắt đầu; không tốn thêm lượt AI. Câu hỏi này không được ghi giả thành nội dung model đã trả.

Mã GHN đọc được chỉ là mã khác nhau trong nguồn. Mã lặp có thể là dòng chi tiết hoặc lịch sử trạng thái, chưa chứng minh lỗi trùng/dòng thừa; không tự xóa, gộp hay giữ dòng cuối. Trạng thái chưa xác định không có nghĩa hàng chưa giao; delivered chưa xác nhận SLA hoặc hoàn tất thực tế. Không suy khối lượng/chuyến xe từ các bộ đếm này.

Chủ sở hữu dùng `/details` để xem truy vết rút gọn của câu trả lời còn trong RAM: nguồn/phạm vi, model, quyết định kiểm tra theo task/version và một phần chỉ số/công thức. Lệnh không gọi AI hoặc đọc lại nguồn; không xuất key, chỉ dẫn phiên, payload hay danh sách mã đơn. Chi tiết mất khi xóa phiên/restart; báo cáo do CLI riêng gửi không có audit trong RAM của worker. Bot nói rõ khi chưa có, chủ sở hữu có thể dùng `/run` để tạo bản mới. Bất đồng còn mở không bị đóng vì đổi cách trình bày.

Phương án chạy liên tục và nguồn Apps Script được đánh giá riêng tại [Phương án 24/7](../reports/PHUONG_AN_24_7.md); chưa triển khai cloud trong đợt chỉnh hiển thị.

Đây là kênh Telegram của cùng AI Team. Nhóm đích được khóa trong ứng dụng; owner nhập User ID và token tại máy, xác minh bằng mã ghép nối. Không tự chọn người nhắn đầu tiên làm owner. Bot chỉ cần quyền thành viên, Group Privacy bật; lệnh `/ask@ten_bot` và trả lời tin bot hoạt động rõ ràng trong nhóm nhiều bot. [Quy tắc Privacy của Telegram](https://core.telegram.org/bots/features#privacy-mode).

## Khởi động và bảo vệ phiên cũ

Mở `Start-Telegram-Pilot.cmd` → http://127.0.0.1:8506 → tab **Telegram**. Launcher mở cùng `app.py`, hỗ trợ PowerShell 5.1 và chỉ loopback. Phiên Groq 8504 được giữ nguyên; key ở RAM phiên đó **không tự chuyển** sang 8506. Nếu muốn worker dùng Groq, nhập lại một lần trong **Cấu hình Live** tại 8506 rồi chọn **Dùng model Live đang cấu hình trong phiên** khi lưu Telegram. Không đọc khóa từ tab Groq, browser storage hoặc ứng dụng khác.

1. Nhập Telegram token vào ô password, User ID owner và User ID người được hỏi thêm nếu cần. Người ngoài danh sách không được đọc dữ liệu qua bot; thành viên nhóm vẫn có thể nhìn thấy tin đã gửi vào nhóm.
2. Bật xác nhận lưu DPAPI, bấm **Lưu cấu hình Telegram mã hóa**. Chưa có model vẫn bật onboarding được; bot ghi rõ chưa có AI.
3. Bấm **Khởi động worker Telegram**, cập nhật trạng thái. Owner gửi đúng mã `/pair@ten_bot ...` trong đúng nhóm trong 10 phút. Mã không xác minh được User ID khác. Nếu chưa biết ID: lưu với owner trống, khởi động để dùng `/id@ten_bot`, dừng, điền ID rồi ghép lại. Nhánh khám phá ID không cấp quyền hỏi AI.
4. Dùng `/status`, `/onboard`, `/memory`. Chỉ owner được sửa nguồn/lịch/quyền/bí mật; quyền và bí mật được sửa tại giao diện local sau khi dừng worker.

Worker là `python -m ops_team.telegram_worker`, chạy nền bằng `Start-Telegram-Worker.ps1` với cửa sổ Hidden. Khóa file Windows ngăn hai process cùng workspace; lease ngăn hai worker cùng bot trong process. Đóng tab không dừng worker. Máy ngủ/tắt/mất mạng vẫn gián đoạn; chưa cài service/admin, tự khởi động cùng Windows, host cloud hoặc public webhook.

## Trí nhớ có nguồn và quyền xác nhận

`/onboard` hỏi lần lượt khách hàng/phạm vi, kho, kỳ, cutoff, giờ báo cáo, múi giờ, mục tiêu, đầu mối. Những gì đã cấu hình local được giữ và bỏ qua khi hỏi. Giá trị do trả lời tự nhiên chỉ thành bản nháp RAM; `/confirm` mới lưu. `/set trường giá_trị` là xác nhận trực tiếp của owner.

Các trường gồm `customer`, `warehouses`, `period`, `cutoff`, `report_time`, `timezone`, `goals`, `contacts`, `kpi_formulas`, `mapping`, `numeric_format`, `source_id`, `sheets_id`, `sheets_tab`, `sheets_gid`, `sheets_ranges`.

- `/memory`: xem giá trị, phiên bản, người xác nhận và tin nguồn. `message_id=0` được ghi **LOCAL_OWNER_SETUP / chủ máy cấu hình tại local**, không dựng nguồn Telegram #0.
- `/set`: sửa giá trị, tăng phiên bản, vô hiệu context AI cũ và tạm dừng lịch. Cần xem `/plan` và bật lại.
- `/forget trường` hoặc `/forget all`: xóa giá trị và lịch sử giá trị nghiệp vụ, tạm dừng lịch, xóa hội thoại RAM liên quan. Audit chỉ giữ hành động quên. Lệnh này **không xóa** file nguồn DPAPI, snapshot đã lưu hoặc lịch sử trên Telegram.
- Thông tin owner cung cấp/xác nhận vẫn chưa được đối soát độc lập. Công thức KPI tự nhập là định nghĩa để trao đổi; chỉ các công thức code đã kiểm tra mới được tính. Không thực thi biểu thức tùy ý, suy luận nhân quả hoặc xác nhận tiết kiệm.

## Nguồn và phạm vi dữ liệu

**Excel/CSV:** gửi file tối đa 20 MB kèm `/import`, hoặc `/import costs` cho CSV chi phí. `/template` liệt kê hợp đồng; app cũng có Excel DEMO để thử. Kiểm tra header, dòng, đơn vị, trùng, workbook giải nén/macro. File sai không thay nguồn cũ. File qua kiểm tra được lưu DPAPI; owner chọn bằng `/set source_id mã_nguồn`. Thiếu bảng vẫn được nêu rõ là PARTIAL, không điền số 0 thay thiếu.

**Thư mục export:** owner cho phép một đường dẫn tuyệt đối ở UI rồi `/set source_id folder`. Chỉ đọc đúng thư mục, không đệ quy, tối đa 100 file; kiểm tra symlink/junction không thoát root. Chọn workbook mới nhất, hoặc CSV mới nhất theo từng nhóm canonical. Không tự đọc thư mục nguồn khác của máy. File chỉ được đọc lại mỗi lượt, không sửa/publish.

**Google Sheets API:** lựa chọn bổ sung nếu chủ file cấp Viewer cho service account. Upload JSON credential tại UI rồi lưu DPAPI; worker dùng duy nhất scope `spreadsheets.readonly`, không dùng browser cookie, không cấp domain-wide delegation và không tự công khai file. [Google scopes](https://developers.google.com/workspace/sheets/api/scopes), [service account chính thức](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html).

Owner xác nhận `sheets_id`, `sheets_tab`, `sheets_gid`, `sheets_ranges`. Mỗi lượt xác minh lại tên tab/gid rồi đọc đúng ranges bằng [values.batchGet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet). 403 hoặc pin đổi sẽ chặn đọc, không đổi tài khoản/provider/nguồn hoặc dùng cache thay thế. App chưa triển khai OAuth người dùng; quyền Viewer trong trình duyệt không tự cấp quyền cho worker.

**Connector snapshot cho tài khoản chỉ có Viewer:** một lượt đọc Google connector đã được cấp quyền có thể nạp snapshot qua stdin. Không chuyển OAuth token/cookie sang worker. Owner cần khóa file/tab/gid/range, chọn `source_id snapshot`; CLI không bật lịch. Chưa tạo automation Codex hoặc xác minh connector tài khoản thật trong đợt code này. Khi connector còn 403, không có nguồn mới để nghiệm thu.

## Booking tab DC PHÚ THỌ

Adapter `ops_team/supra/booking.py` được kiểm thử bằng dữ liệu tổng hợp theo header đã khảo sát. Đọc độc lập canonical P&L. Phạm vi là **tab/DC xuất hàng**, không lọc `Tỉnh` nhận hàng thành tỉnh Phú Thọ.

Ví dụ cấu hình range (owner phải kiểm tra header và phạm vi thật):

```text
/set sheets_tab DC PHÚ THỌ
/set sheets_gid 0
/set sheets_ranges {"supra_booking":"'DC PHÚ THỌ'!A1:AK10000"}
```

Không hardcode private file ID. Header tối thiểu: `Ngày LTC`, `Mã đơn GHN`, `Trạng thái`, `Ngày xuất`. Ngày xuất dùng DD/MM/YYYY hoặc ISO, Google serial ngày được chuyển theo chuẩn; không tự lấy Ngày LTC thay Ngày xuất thiếu. Chỉ tính số dòng đã biết ngày trong kỳ/cutoff, mã GHN khác nhau đọc được định dạng, dòng có mã lặp, mã thiếu, ngày chưa phân được vào kỳ và trạng thái nguồn theo dòng. Các số này chưa chứng minh đơn hợp lệ hoặc lỗi trùng. `delivered` không chứng minh giao đúng hạn. Mã GHN lặp được nêu để xác minh, không tự giải quyết.

Chưa tính Qty/PCS/kg/CBM/xe/chuyến/drop/chi phí/OT/SLA/tải sử dụng khi thiếu khóa và định nghĩa. Không cộng hoặc forward-fill ô tổng gộp/lot/xe. Driver name/phone, biển số, siêu thị/địa chỉ bị loại khỏi dữ liệu an toàn trước lưu snapshot hoặc tạo model payload. Model chỉ nhận aggregate và source row, không nhận danh sách mã GHN.

## Snapshot stdin

Chạy `python -m ops_team.telegram_snapshot` với JSON trên **stdin**, tối đa 20 MB. Không đặt payload, token hoặc dữ liệu gốc trong command line/env/tempfile. Producer nên chỉ yêu cầu cột **A:C và E** của tab đã chọn, ghép theo số dòng trong RAM; tránh đọc tên tài xế/SĐT ngay từ connector.

Schema có đúng các trường:

```json
{"version":1,"source":"GOOGLE_SHEETS_CONNECTOR","spreadsheet_id":"synthetic_sheet_id_for_example","tab":"DC PHÚ THỌ","gid":0,"range":"'DC PHÚ THỌ'!A1:AK10000","retrieved_at":"2026-10-25T13:00:00+07:00","headers":["Ngày LTC","Mã đơn GHN","Trạng thái","Ngày xuất"],"rows":[]}
```

Giữ dòng trống tương ứng khi ghép để provenance vật lý đúng. Producer truyền object trong RAM vào subprocess, ví dụ `subprocess.run([python_exe, '-m', 'ops_team.telegram_snapshot'], input=json.dumps(snapshot).encode(), check=True)`. Ví dụ thời điểm trên không phải snapshot thật, không được dùng để giả nguồn hiện tại.

CLI xác minh pin, timestamp có timezone/không tương lai, giới hạn dòng/range, loại PII và cột tổng mơ hồ rồi mới lưu phiên bản DPAPI + pointer atomic. Không nhận credential/quyền từ payload. Chỉ in hash/count/status. Snapshot cũ không ghi đè snapshot mới. Worker giải mã lại vào RAM, kiểm hash và pin mỗi lượt. Giá trị snapshot là nguồn connector được cung cấp, chưa là đối soát độc lập.

## Kế hoạch và lịch 13:00

Owner đặt `/set report_time 13:00`, `/set timezone Asia/Ho_Chi_Minh`, cùng kỳ/kho/cutoff/mục tiêu/đầu mối và nguồn thật. Các giá trị đã được owner chốt tại local không phải nhập lại. Không tự giả cutoff hoặc phạm vi còn thiếu.

`/plan` in đích nhóm, nguồn/pin/range, kỳ/phạm vi, cutoff, giờ gửi/múi giờ, dữ liệu còn thiếu, model và giới hạn. Scope rỗng hoặc kho chưa có dòng, lỗi nguồn/auth/pin hoặc snapshot quá tuổi sẽ chặn bật. `/enable mã_kế_hoạch` mới bật đúng kế hoạch. `/pause` hoặc `/stop` dừng lịch; bot còn nhận lệnh. Nút **Dừng worker** dừng cả nhận/gửi và lịch.

Mỗi ngày có job ID bền theo ngày/giờ/plan. Không gửi bù nhiều ngày máy tắt; trễ trên một giờ được ghi SKIPPED. Với snapshot, chờ dữ liệu mới tối đa 10 phút sau giờ báo cáo; quá 15 phút tuổi thì chỉ báo thiếu nguồn, không dùng số cũ/model thay báo cáo mới. Tuổi fetch/upload và min/max ngày nghiệp vụ được phân biệt; file mới upload chứa ngày cũ vẫn bị cảnh báo. Canonical báo tháng lũy kế đến ngày cutoff; chưa chứng nhận cutoff theo giờ nếu nguồn chỉ có ngày.

Model dùng lại adapter của AI Team: hội thoại theo vai trò chưa qua Auditor; `/team` canonical dùng orchestrator hiện có, Supra PLAN dùng Cost+Auditor, booking dùng Operations+Auditor với exact task/version và metric IDs. Không coi accept của Auditor là đối soát Finance/chứng minh nhân quả/phê duyệt vận hành. Toàn worker có 60 HTTP attempts và 120.000 token dự phòng ước tính/ngày local, 7.500 token/phút; reservation bền trước HTTP và được giữ khi restart. Đây không phải quota/billing toàn tài khoản. Đủ deadline cả lượt để chờ cửa sổ token, từng request vẫn timeout riêng; lỗi không fallback model/provider/Demo.

## Độ bền và giới hạn giao tin

getUpdates/cursor và claim job bền trước xử lý. Mất mạng/timeout/5xx/429 giữ worker sống, reconnect có backoff tối đa 60 giây và kiểm tra stop mỗi giây; 401/403/409/cấu hình sai dừng để sửa. Không tạo poller thứ hai để khôi phục.

sendMessage được ghi SENDING trước HTTP, SENT sau phản hồi xác nhận. Kết quả mơ hồ ghi UNKNOWN, **không tự gửi lại** khi restart hoặc reconnect. Không có giao dịch atomic giữa Telegram và SQLite nên không hứa exactly-once delivery: có thể mất một phản hồi nếu crash/mạng lỗi ở thời điểm bất định. Owner xem nhóm rồi chủ động yêu cầu lại nếu cần. Raw câu hỏi, response body Telegram và model payload không nằm trong DB; delivery chỉ có ID, trạng thái, digest, thời điểm và message ID.

`.telegram_state/` bị loại khỏi Git: DPAPI settings/memory/config/source/snapshot; SQLite chỉ để metadata kỹ thuật tối thiểu plaintext. HTTP diagnostics được lọc token URL và Sheets identifiers/ranges. Không ghi .env plaintext, không lấy credential app khác. DPAPI gắn với tài khoản Windows này; chưa có phục hồi sang máy/tài khoản khác được nghiệm thu. RAM/GC không là cam kết xóa vật lý từng byte.

Nút **Xóa kho token/model DPAPI** trong tab Telegram dừng worker và xóa cấu hình bí mật, giữ nghiệp vụ/snapshot. Nút **Xóa dữ liệu & khóa phiên** của app chỉ xóa RAM của phiên, không xóa DPAPI, paired hay dừng worker độc lập. Mọi AppTest/test dùng fixture root tạm để không đụng runtime thật; có test bảo toàn vault byte-for-byte khi reset phiên.

## Nguồn được chọn: browser Viewer export

Nếu tài khoản công ty không cho kết nối ứng dụng ngoài, có thể dùng quyền Viewer hiện có để xuất CSV đúng tab qua menu của Google Sheets. Không lấy cookie/OAuth token, không public sheet, không bypass bước xác minh. Built-in export không bảo đảm tab đang chọn; phải guard file xuất thực tế. CSV 57 cột không khớp 37 cột đã khảo sát sẽ bị từ chối, không được dùng làm nguồn. Chưa coi export thật là đạt chỉ vì tải được file.

Owner chọn `/set source_id browser_snapshot` và pin file/tab/gid/range. Chạy:

```text
python -m ops_team.telegram_snapshot --file "C:\absolute\owner-selected-export.csv"
```

Descriptor đi trên stdin, không đặt thông tin nguồn hay dữ liệu dòng trong command line. Schema descriptor:

```json
{"version":1,"source":"GOOGLE_BROWSER_VIEWER_EXPORT","source_url":"https://docs.google.com/spreadsheets/d/synthetic_sheet_id_for_example/edit#gid=0","spreadsheet_id":"synthetic_sheet_id_for_example","tab":"DC PHÚ THỌ","gid":0,"range":"'DC PHÚ THỌ'!A1:AK10000","retrieved_at":"2026-10-25T13:00:00+07:00","expected_data_rows":2}
```

Đây chỉ là ví dụ tổng hợp, không phải metadata của file thật. Phải dùng thời điểm xuất thật, URL/gid thật và số dòng không rỗng đã kiểm tra. CLI đọc file gốc nguyên trạng, kiểm đúng 37 header theo thứ tự (chuẩn hóa Unicode/case/khoảng trắng/dấu phân tách), số dòng/range, mẫu mã GHN/ngày trong 10 dòng đầu. Không tự sửa file nguồn. Chỉ dữ liệu booking đã bỏ PII và cột tổng mơ hồ mới được ghi DPAPI. Snapshot ghi rõ **GOOGLE_BROWSER_VIEWER_SNAPSHOT**, không giả API live. CLI trả hash, số dòng và bộ đếm aggregate toàn nguồn để đối chiếu golden; báo cáo kỳ/cutoff dùng tập con theo Ngày xuất, không đồng nghĩa hoàn tất thực tế.

Mã GHN trùng và mã có trạng thái mâu thuẫn được đếm riêng. Không chọn trạng thái ở dòng cuối. `consistent_delivered_ids` chỉ đếm mã có tập trạng thái nguồn đúng một giá trị delivered trong phạm vi, không phải SLA. Dòng thiếu/sai Ngày LTC và Ngày xuất được nêu riêng; không tự gán ngày khác hoặc cộng PCS/lot/xe. Qty(Units) chỉ được cộng theo mapping/quy tắc owner xác nhận cho đúng nguồn, dưới chỉ tiêu sản phẩm riêng; xem [BOOKING_PRODUCT_UNITS.md](BOOKING_PRODUCT_UNITS.md) về DPAPI migration, grain và cách ly.

L được owner xác nhận là **kg cả mã GHN**, tính một lần qua các DO. Các anchor cùng tổng không cộng trùng; L lỗi/mâu thuẫn/thiếu toàn nhóm hoặc GHN chưa đủ phạm vi Ngày xuất giữ kg chưa chốt. Tổng toàn phạm vi N/A nếu còn nhóm thiếu; subtotal phần đủ nguồn có nhãn riêng. J vẫn cộng DO độc lập. Xem [BOOKING_GHN_WEIGHT.md](BOOKING_GHN_WEIGHT.md) về staging v2 chín cột, legacy N/A, restart và metadata model không mất bằng chứng. Chưa thêm phép tính doanh thu/cost/GP.

Bổ sung [bốc xếp DC theo điều khoản owner](DC_HANDLING.md): staging v3 11 cột và catalog/rates/store override nằm trong DPAPI. Full37 CSV và connector cùng pin giữ policy đã duyệt; source khác không tự kế thừa. Routing/giá/ngày/L chưa đủ giữ component N/A, phần đủ nguồn có subtotal riêng; DIRECT không chịu khoản này dù L hỏng. Không đưa handling vào tổng cost/P&L hoặc tự chạy AI. Context cache gắn SHA catalog để thay policy đúng cả khi bytes Sheets chưa đổi; source/facts/plan/lịch13h và cap/429 giữ nguyên.

Chuỗi lịch phải tuần tự: **xuất đúng tab → CLI import/version DPAPI → kiểm freshness/phạm vi → gửi lịch**. Dùng thêm `--send-scheduled` sau `--file` để gửi qua cùng cơ chế receipt/dedupe. Nhánh gửi chỉ hoạt động khi owner+nhóm đã paired, worker cho chạy, source là browser_snapshot và plan đã được owner duyệt. Không tạo getUpdates/poller thứ hai. Worker không tự gửi trước từ snapshot browser cũ, kể cả snapshot còn dưới 15 phút tuổi. Hash phiên bản mới đúng nguồn không reset plan; đổi nguồn/phạm vi/giờ mới phải duyệt lại.

`--send-scheduled` trả state xác nhận SENT chỉ khi có receipt. Nếu lỗi mơ hồ, giữ UNKNOWN/DELIVERY_UNCONFIRMED, không tự gửi lại; source đã nhập vẫn được báo đúng là đã nhập. Khi chưa paired/plan/source, trả lý do mà không gửi dữ liệu thật. Chưa tạo automation browser 13:00 trong đợt code này; automation phải do chat điều phối/người dùng cấu hình sau nghiệm thu export/nguồn. Google đòi xác minh hoặc export mismatch thì dừng chuỗi, không gọi gửi với snapshot cũ.

Booking cơ bản không bị chặn chỉ vì chưa có goals/contacts hay công thức chi phí. `/plan` ngắn gọn gồm nguồn/tab, phạm vi/kỳ/cutoff, giờ Việt Nam, chỉ số tính được, AI/ngân sách, thiếu và giả định, rồi `/enable`. Nếu chưa có khai báo riêng, giả định phải ghi rõ để owner xác nhận, không lưu như lời user đã cung cấp. Kỳ mặc định là **CURRENT_MONTH / tháng hiện tại**: `/enable` xác nhận mode này, mã plan ổn định khi sang tháng, context/metrics chuyển theo tháng; chưa có dòng tháng mới thì báo thiếu. Owner `/set period YYYY-MM` chuyển sang tháng cố định và hash đúng tháng đó. Đổi mode/phạm vi/giờ cần duyệt lại; hash snapshot cùng nguồn và rollover CURRENT_MONTH không tự pause. Goals/contacts/SLA/OT/chi phí thiếu là checklist, không phải số liệu được bịa.

Booking Free Pilot dùng preset tối đa **1.500 token đầu ra/lần**, trong giới hạn trên người dùng cấu hình. Scope/period/status chung và source-row catalog chỉ gửi một lần; giữ value/formula/label/unit/source_count/sample flags, không cắt exact answer cho Auditor. Output phải dẫn metric ID, không dùng rowref làm evidence ID. Full source vẫn ở vault. Ba lượt chat gần nhất dùng question excerpt ≤400 ký tự, business excerpt ≤900 ký tự, ghi rõ là trích đoạn, không phải bằng chứng/answer hoàn chỉnh. Renderer hiện câu hỏi `requests`, bỏ nhãn rỗng, đánh dấu phần cần bổ sung chưa hoàn tất.

Mỗi câu trả lời booking có header do app cố định: lúc nhận snapshot, Ngày xuất mới nhất, nhãn không phải live/đến hiện tại. Chat vẫn có thể hỏi snapshot cũ với nhãn này; guard 15 phút dành cho báo cáo mới theo lịch. Source metadata cũng được gửi vào model, không chỉ trông chờ model tự nhắc. Đã test ba lượt chat có history và Operations→Auditor với phản hồi thực dụng, trần thật 7.500 token/phút, fake clock/HTTP qua provider adapter thật; không nâng trần trong test.

Booking giữ `task_id`, toàn bộ `audit_answer` và `audit_decisions` trong kết quả review. Chat và báo cáo hiển thị verdict/lý do đúng phiên bản, đề nghị sửa, câu hỏi bổ sung và bất đồng Auditor. Quyết định trỏ sai phiên bản hoặc thiếu quyết định không làm Operations được chấp nhận. Bất đồng chưa xử lý giữ kiểm toán còn mở, kể cả model ghi accept; kiểm tra nguồn của app vẫn là gate riêng. Không tự thực thi yêu cầu bổ sung hoặc đóng bất đồng. Phần hiển thị quá dài được đánh dấu rút gọn; structured audit trong RAM vẫn giữ đầy đủ.

## Nghiệm thu

Đã kiểm tra code/HTTP giả: owner+group+pairing, auth, chống trùng update, topic/user isolation, secret redaction cả HTTP logs, DPAPI thật trên Windows, memory edit/forget/provenance local, import/giải nén/hash, Google scopes/pin/403, snapshot encryption/PII projection, ngày/DD-MM/cutoff, booking trùng/thiếu, budget pacing qua real provider adapter + fake HTTP/fake clock, lịch/restart/UNKNOWN và reconnect quá ba lần lỗi.

Chat điều phối báo đã kiểm tra Telegram thật/owner paired và Groq thật với model `qwen/qwen3.8-27b` trên câu hỏi tổng hợp không có nguồn; khóa do người dùng nhập local, không được gửi sang chat triển khai. Retest thật sau sửa renderer trả rõ ba nhóm dữ liệu cụ thể trong một HTTP. Đây là QA riêng được báo lại, không gộp với HTTP giả hoặc nghiệm thu phân tích dữ liệu thật.

Chat điều phối báo CSV browser thật đã được nhập và bốn bộ đếm tháng 10 (dòng, mã GHN duy nhất, mã có trạng thái mâu thuẫn và delivered nhất quán) khớp golden tính độc lập. Không đưa số kinh doanh/PII thật vào tài liệu này. Các counter mã GHN dùng đơn vị **mã GHN**, không phải dòng; trạng thái nguồn không xác nhận hoàn tất thực tế/SLA.

Sau bàn giao, chat điều phối chạy full suite độc lập: 240 passed in 34,03 giây, production encrypted vault unchanged=True. Đã refresh đúng tab/nhập CSV thật lần nữa, counters khớp bản trước và không gửi model/nhóm qua lượt CLI đó. Worker reload và activation do chat điều phối thực hiện riêng; reload UI không khởi động/dừng worker hoặc đổi cấu hình/lịch.

Chưa nghiệm thu bởi đợt code này: private Google connector/Sheets API đúng quyền, gửi báo cáo dữ liệu thật vào nhóm, lịch 13:00 chạy đủ ngày trên máy thật, tự phục hồi khi Windows khởi động, enterprise SSO/RBAC hoặc production hosting.

Cập nhật runtime do chat điều phối báo ngày 25/10/2026: sau paired verified và plan missing=[], đã kích hoạt schedule theo chỉ thị người dùng bằng **LOCAL_OWNER_SETUP_APPROVAL / message_id=0**, không dựng lệnh Telegram. `schedule_enabled=true`, approved_plan đã lưu và automation `dc-ph-th-b-o-c-o-telegram-13h` **ACTIVE**, 13:00 giờ Việt Nam. Vì đã qua giờ chạy hôm nay, lượt đầu dự kiến 26/10/2026. Worker browser vẫn chờ pipeline xuất/nhập nguồn mới, không tự gửi snapshot cũ. Đây là thao tác activation của chat điều phối, không phải hiệu ứng của sửa code/test; child không đổi runtime hoặc automation. Parent sẽ reload worker để nhận fix Auditor cuối.

## Kết luận trước, danh sách thiếu dữ liệu `/thieu`

`/chiphi` và báo cáo `/run` mở đầu bằng một dòng **Kết luận** và **Việc cần làm**. Chỉ nêu "đã đủ nguồn" cho chỉ số có quy tắc và đủ dữ liệu; còn nhóm giữ riêng thì nói chưa chốt và chỉ sang `/thieu`.

`/thieu [YYYY-MM-DD] [YYYY-MM-DD]` (chỉ owner, không gọi AI) lấy đúng kết quả code của `/chiphi` và liệt kê từng nhóm còn giữ riêng: thiếu trường nào (vd. kg cột L, loại tải cột T, biển S), ngày nào nếu tính theo đợt GHN/ngày, **số dòng trong tab Booking/Raw đã ghim** và cần gửi gì. Không in mã GHN, cửa hàng hay biển xe; mỗi nhóm tối đa 6 số dòng, tối đa 10 nhóm, phần còn lại ở `/details audit`. Chưa có chính sách chi phí thì dùng chỉ số Booking (kg, sản phẩm, bốc xếp). Chính sách stale hoặc khoảng ngày ngoài chính sách thì báo lý do, không lập danh sách từ cách tính khác. Các khoản thiếu luôn giữ N/A, không tính là 0.

## Tự động lấy dữ liệu bằng Apps Script + Google Drive for desktop

Owner chốt 27/10/2026: công ty không cấp Viewer cho tài khoản dịch vụ, nên dùng Apps Script chạy bằng chính tài khoản của owner.

1. Tạo project ở script.google.com, dán `scripts/apps_script/ExportSupraTabs.gs`, đặt Script Properties theo chú thích đầu file (ID/gid/tab/range đúng như `/plan`, `FOLDER_ID` là thư mục Drive riêng), chạy `installTrigger()` một lần.
2. Script chạy mỗi 5 phút trong 12:35–12:58, ghi `booking.csv`, `raw.csv` rồi `supra-export-manifest.json` (hash SHA-256, số dòng, nguồn).
3. Google Drive for desktop đồng bộ thư mục về máy chạy bot. Task Scheduler chạy `python -m ops_team.drive_import --folder "<thư mục đồng bộ>" --send-scheduled` lúc 13:01 và 13:06.
4. `drive_import` kiểm manifest/hash/header (Booking 37 cột, Raw 25 cột), nguồn/tab/gid/range phải trùng bản paired đã duyệt; catalog và policy luôn lấy từ vault, không lấy từ thư mục. Sai bất kỳ điểm nào thì từ chối, nguồn cũ giữ nguyên. Lịch chỉ gửi một lần cho mỗi job (receipt), Raw cũ không chặn gửi phần Booking.

Lần nạp paired đầu tiên vẫn làm tay để có catalog đã duyệt. Máy tắt thì không gửi; xem phương án 24/7 trước khi chuyển lên dịch vụ cloud.

## Xuất danh sách thiếu dữ liệu ra Excel

`python -m ops_team.export_checklist --out "<đường dẫn>.xlsx" [--root <bản sao vault>] [YYYY-MM-DD YYYY-MM-DD]` dùng đúng phép tính của `/thieu`, ghi file gồm sheet "Tóm tắt" (nhóm, lý do, cần bổ sung, số dòng) và "Chi tiết" (mỗi dòng Booking/Raw đang giữ, kèm GHN, SO, DO, ngày, kg L, tải T, biển AG/S, gram Raw). File chứa định danh nghiệp vụ nên chỉ lưu trên máy owner, không gửi Telegram/model, không commit. Không gửi tin, không gọi AI.
