# Engineering handoff — 27/10/2026

## Trạng thái mã

Snapshot từ source `a9f991014d495074af1d01ec0965505b16f5202b`. SHA-256 của tập `.py` trong `ops_team` và `tests`, theo thứ tự đường dẫn tương đối rồi bytes, là `959d4112ce44cb94bd4f0b54661972753fe7242750c2b578cacd49b1b31e8f7c`. Những file source/test được chuyển nguyên bytes; README, bàn giao và ignore rules được chuẩn bị riêng cho repo mới.

Tại máy gốc, bản quy tắc owner mới đã được kiểm chứng và kích hoạt. Một xác nhận bổ sung về số lượt xe đã được nạp bằng cấu hình nguyên tử, không đổi source. Không nhầm việc clone được code với việc có sẵn các cấu hình đó trong repo.

## Cấu trúc cần đọc

| Khu vực | File/thư mục |
|---|---|
| UI | `app.py`, `ops_team/ui_app.py`, `ops_team/views.py` |
| Điều phối 8 vai trò | `ops_team/orchestrator.py`, `agents.py`, `providers.py`, `schema.py` |
| Telegram | `telegram_assistant.py`, `telegram_bridge.py`, `telegram_worker.py`, `telegram_state.py`, `telegram_secrets.py` |
| Nguồn được pin | `telegram_snapshot.py`, `telegram_vehicle_snapshot.py`, `supra/booking_*.py` |
| Tiền và giá | `supra/manual_cost_report.py`, `manual_cost_rules.py`, `manual_cost_overlay.py` |
| Policy được bảo vệ | `telegram_manual_cost_policy.py`, `telegram_vehicle_forecast.py`, `telegram_cost_overlay.py`, `telegram_rule_layer.py` |
| Bốc xếp / hai chặng | `supra/vehicle_cost.py`, `supra/two_leg_evidence.py` |
| Finance/Auditor | `ops_team/chief/`, `ops_team/supra/finance.py`, `reconciliation.py`, `ledger.py` |

## Quy tắc đã được owner chốt

- Booking J là sản phẩm bên trong thùng. Cộng các dòng DO hợp lệ theo khóa nguồn; không đổi pcs thành kiện/thùng. Doanh thu theo ngày/phạm vi và biểu giá owner đã duyệt.
- Bốc xếp: một GHN + ngày xuất E là một đợt. Cùng ngày nhiều DO không nhân L; khác ngày là đợt khác. Không mượn L của ngày khác cho đợt trống. Một trường hợp hai phần kg cùng ngày đã được owner cho cộng; đó là ngoại lệ theo đúng nguồn, không phải quy tắc sum mọi L khác nhau.
- Chọn mức phí DC bằng ngày E. Bốc xếp DC và nhân sự bốc xếp GXT là hai khoản khác nhau. Trừ phần DC đã được chứng minh nằm trong gói được chấp nhận, theo từng đợt, để tránh tính hai lần.
- Với GXT lấy thẳng cũ, S khác AG được owner cho hiểu là hai chặng khi liên kết đủ rõ: AG từ DC về GXT, S giao cuối. Giữ ngoại lệ NCC, BC, Lai Châu. Không sửa biển nguồn, không gán tỷ lệ kg của S cho AG.
- T là loại tải Booking dùng tính cước, không đồng nhất tải vật lý. Hai ngày có nhiều loại Booking đã được chốt số lượt cụ thể trong protected rules. Không suy một lượt cho mọi loại tải khác nhau, lấy max T hoặc cộng tiền theo từng DO.
- Có một đơn khai trương tại địa bàn mặc định BC nhưng owner chốt FTL 8T theo Booking và km thực tế. Ngoại lệ chỉ áp đúng đơn/ngày; chưa có km/biểu giá thì để N/A, không dùng giá FTL tỉnh khác hoặc tiếp tục tính BC theo điểm.
- GXT trả tiền xe theo xe/ngày, lương giao theo kg trả thêm. Phân bổ dùng chính kg của xe/ngày có đủ nguồn; không dùng tỷ lệ toàn đội. Một số pool đã được owner chỉ định phân bổ riêng; đọc cấu hình được duyệt, không hỏi lại hoặc suy từ quan sát.
- Phí điểm dùng cửa hàng/ngày theo giả định owner chốt, không nhân theo DO. Xe/ngày, mã chuyến Raw và lượt xe thực tế là các khái niệm khác nhau.
- Raw chỉ lấy DELIVER, J gram chia 1.000 thành kg; membership đối chiếu ALL Booking, không chỉ Booking trong kỳ. `delivered` của nguồn chưa chứng minh SLA hoặc hoàn tất thực tế.
- Những khoản ngoài gói đã bị owner loại khỏi phạm vi không được tiếp tục gọi là khoản thiếu. Km NCC theo hợp đồng vẫn thuộc phạm vi.

Giá, mã đơn, biển xe, số dòng, effective dates, tỷ lệ phân bổ và bằng chứng cụ thể nằm trong policy/proof local. Không đưa các thông tin đó thành hằng số trong code hoặc fixtures thật.

## Phần còn mở

1. Nhật trình km thực tế, biểu giá/định mức và căn cứ tiền NCC cho các tuyến vùng xa và ngoại lệ khai trương.
2. Các đợt thiếu/lỗi L, Raw thiếu/sai biển S và quan hệ đợt/chặng chưa xác định chắc chắn.
3. Hai trường hợp BC/GXT khác chưa được owner kết luận cách thực hiện và trả tiền. Không áp ngoại lệ khai trương cho chúng.

Không coi các cảnh báo cũ đã được quy tắc mới giải quyết là câu hỏi mới cho owner. Không đẩy mọi trường hợp bảo thủ của phần mềm thành lỗi dữ liệu của người dùng; đối chiếu trước, gửi từng case ngắn có số dòng và câu hỏi cụ thể khi thực sự cần.

## Giới hạn cần giữ rõ

Lớp rule áp dụng `/chiphi` và `/xe`. Từ 27/10/2026 (Claude), **bốc xếp DC** trong `/run`/báo cáo lịch, `/chief` và model context cũng lấy từ lớp rule qua `AssistantResponder._booking_context` → `vehicle_cost.booking_rule_handling` (đợt GHN + ngày E, cùng resolver hai chặng). Quy tắc:

- Chưa cài lớp rule: giữ nguyên engine cũ (GHN cả kỳ).
- Lớp rule đã cài nhưng stale/sai binding: bốc xếp N/A với lý do `OWNER_RULES_INVALID_OR_STALE`; không quay về cách tính cũ.
- Kỳ báo cáo (đầu tháng → cutoff) vượt `window` của rule: bốc xếp N/A với lý do `OWNER_RULE_WINDOW_NOT_COVERING_SCOPE`. Muốn có số cho cả kỳ, owner cần duyệt rule có window phủ kỳ.
- Approved plan, lịch, source identity không đổi. Nhãn cache model thêm SHA rule để không dùng context cũ.

Các khoản khác của `/chiphi` (điểm, FTL, GXT, overlay doanh thu) **chưa** đưa vào báo cáo lịch/model context; vẫn là hạng mục riêng.

Current manual subtotal là phần đủ nguồn, không là tổng chi phí hoặc GP cuối. Không công bố số tiền mới dựa trên repo không có dữ liệu thật. Việc thay layer có thể làm invalid bindings; phải kiểm hash source, catalog, base/forecast/overlay và owner/group theo schema.

Nguồn Google công ty trước đây không cho kết nối ứng dụng ngoài; service account chưa được chủ file cấp quyền. Luồng đã dùng là export đúng tab bằng Google Sheets UI trong trình duyệt được owner đăng nhập. Không dùng cookie, API ẩn hoặc connector tài khoản khác để thay quyền còn thiếu.

Cloudflare Workers Paid là hướng 24/7 owner quan tâm. Streamlit/DPAPI Windows không chạy nguyên trạng trên Workers. Apps Script push chỉ được triển khai khi tài khoản và công ty cho phép đọc/gửi đúng nguồn. Cần thiết kế backend/secret store/jobs/receipt phù hợp và nghiệm thu; chưa public deploy hoặc mua thêm dịch vụ.

## Cách tiếp tục

Đọc các test ở `test_booking_vehicles.py`, `test_booking_handling.py`, `test_manual_cost_rules.py`, các test Telegram và Chief. Tái hiện lỗi bằng dữ liệu giả trước; nếu có dữ liệu local được phép, dùng vault tạm và kiểm nguồn trong RAM, không sửa production để thử.

Khi sửa, kiểm đúng grain, nguồn và arithmetic độc lập; test cả kỳ đầy đủ và hai nửa, giữ subtotal/additivity theo điều kiện, missing khác zero, các ngoại lệ chỉ đúng nguồn, legacy compatibility, atomic rejection, exact audit replay. Chỉ mở rộng test khi có thay đổi/failure/rủi ro cần xác minh.

Đổi cấu hình được đọc mỗi request có thể không cần restart. Nếu đổi worker code, chuẩn bị rollback và thao tác stop/import/restart đầy đủ trước khi dừng. Worker có long poll/handler và join timeout; không giả định dừng trong vài giây. Chờ có giới hạn, kiểm PID/lock thực, không bật worker thứ hai hoặc bỏ máy ở trạng thái không còn worker.
