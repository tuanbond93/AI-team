# Câu hỏi còn mở để chạy tự động

Lập ngày 27/10/2026 từ các lý do code đang giữ N/A. Không lặp lại quy tắc owner đã chốt (xem `ENGINEERING_HANDOFF.md`). Mỗi câu có phương án; **(đề xuất)** là mặc định an toàn nếu chưa trả lời. Trả lời dạng `3B` hoặc ghi rõ. Câu trả lời thành quy tắc chung trong code/policy được duyệt; ví dụ và số liệu thật không ghi vào file này.

Ký hiệu: ⛔ chặn tự động 100%; ⚠ làm số còn N/A thường xuyên; ℹ cải thiện.

## A. Lấy dữ liệu tự động

1. ⛔ File Google Sheets Booking/Raw: ai là chủ file và công ty cho phép cách nào?
   A. Chủ file cấp quyền **Viewer cho một service account** của app (đọc trực tiếp, không cần xuất tay) **(đề xuất)**
   B. Chủ file cài **Apps Script** trong file, đẩy CSV đúng tab theo lịch tới endpoint của app
   C. Không được; vẫn xuất CSV bằng tay (khi đó không thể tự động 100%)
2. ⛔ Lịch: báo cáo ngày gửi lúc mấy giờ, chốt dữ liệu đến giờ nào, Raw thường cập nhật xong lúc nào? Nếu đến giờ gửi mà Raw chưa mới trong 15 phút thì:
   A. Gửi phần Booking, ghi rõ Raw cũ, xe/chi phí N/A **(đề xuất)**
   B. Chờ thêm tối đa N phút rồi mới gửi
   C. Không gửi, báo lỗi cho owner

## B. Bốc xếp DC (đợt GHN + ngày E)

3. ⚠ Đợt thiếu kg cột L:
   A. Giữ N/A, chờ bổ sung **(đề xuất)**
   B. Dùng kg Raw DELIVER (J gram/1.000) của **cùng GHN, cùng ngày** nếu đủ và duy nhất
   C. Khác: …
4. ⚠ Cùng GHN cùng ngày có nhiều giá trị L khác nhau (hiện chỉ cộng khi có ngoại lệ từng case). Có tiêu chí chung không?
   A. Không; vẫn duyệt từng case **(đề xuất nếu không có quy luật rõ)**
   B. Cộng khi các dòng thuộc **SO khác nhau** (mỗi SO một phần hàng)
   C. Cộng khi các dòng thuộc **DO khác nhau**
   D. Lấy giá trị theo nguồn cân/đóng hàng: … (cho biết nguồn)
5. ℹ L sai định dạng (`#REF!`, chữ): A. giữ N/A và báo dòng **(đề xuất)** B. khác: …
6. ⚠ Một đợt vừa có dòng thuộc diện bốc xếp vừa có dòng đi thẳng (khác cửa hàng/huyện):
   A. Giữ N/A, hỏi từng case **(đề xuất)**
   B. Cả đợt tính bốc xếp nếu có ít nhất một dòng thuộc diện
   C. Tính bốc xếp, nhưng chỉ phần kg của các dòng thuộc diện (cần kg theo dòng; hiện L là tổng GHN)
7. ⚠ Cửa hàng/huyện mới chưa có trong danh mục điều kiện phí:
   A. Giữ N/A, báo owner bổ sung danh mục **(đề xuất)**
   B. Áp mặc định theo tỉnh: … (ghi mặc định cho từng tỉnh)

## C. Hai chặng GXT (AG về GXT, S giao khách)

8. ⚠ Đơn GXT có Booking AG nhưng **Raw không có dòng DELIVER** hoặc có nhiều xe S khác nhau:
   A. Giữ N/A **(đề xuất)**
   B. Không có Raw thì coi AG giao thẳng; nhiều S thì giữ N/A
9. ⚠ Giá chặng AG (DC → GXT) khi đã xác định hai chặng:
   A. Nằm trong pool xe trung chuyển dùng chung đã có giá; không tính riêng **(đề xuất nếu đúng hợp đồng)**
   B. Có giá riêng theo chuyến/tải; gửi biểu giá
   C. Khác: …
10. ⚠ Raw DELIVER thiếu hoặc sai biển S:
    A. Giữ N/A, báo dòng **(đề xuất)**
    B. Lấy biển từ dòng Raw khác cùng **mã chuyến + ngày** nếu duy nhất
11. ℹ Raw dùng biển đại diện (không phải xe thật): nguồn nào cho biết xe thật? A. không có, giữ N/A **(đề xuất)** B. …

## D. Xe, loại tải T và FTL

12. ⛔ Loại tải T chưa có giá FTL: gửi **bảng giá đủ mọi loại tải** đang dùng (theo tuyến/tỉnh, hiệu lực từ ngày). Nếu Booking ghi loại chưa có trong bảng:
    A. Giữ N/A, báo owner **(đề xuất)**
    B. Dùng giá loại tải nhỏ nhất ≥ loại ghi trên Booking
13. ⛔ Cùng xe AG/ngày có nhiều loại T (anh đã không cho tự suy một lượt cho mỗi loại). Số lượt thật lấy từ đâu để tự động?
    A. Bảng kê/nhật trình NCC (gửi mẫu cột) **(đề xuất)**
    B. Số mã chuyến Raw của xe S/ngày tương ứng
    C. Vẫn xác nhận từng case
14. ⚠ Booking thiếu T hoặc thiếu biển AG: A. giữ N/A, báo dòng **(đề xuất)** B. lấy từ … (nguồn)
15. ⚠ Kg vượt tải của loại có cách tính "một lượt nếu trong tải":
    A. Giữ N/A **(đề xuất)**
    B. Tính thêm lượt theo làm tròn lên kg/tải
16. ⚠ Một GHN nằm trên nhiều xe/ngày: A. giữ N/A **(đề xuất)** B. chia kg theo Raw gram từng xe

## E. NCC vùng xa, km và đơn đặc biệt

17. ⛔ Km thực tế và biểu giá km NCC: gửi **mẫu nhật trình** (cột: ngày, biển, tuyến/đơn, km…) và đơn giá theo hợp đồng. Em làm schema nhập + kiểm nguồn. Cần ai cung cấp, bao lâu một lần?
18. ⛔ Đơn đặc biệt (như đơn khai trương FTL 8T): làm sao nhận diện tự động thay vì nạp ngoại lệ từng đơn?
    A. Thêm một cột "Ngoại lệ" trong Booking (vd. `FTL_8T`), owner/điều phối điền **(đề xuất)**
    B. Dựa vào ghi chú/tên đơn có sẵn: … (cột nào)
    C. Vẫn nạp ngoại lệ từng đơn
19. ⚠ Hai trường hợp BC/GXT còn chờ: quy luật chung khi **đơn BC đi chung xe GXT cùng ngày**?
    A. Tính BC theo điểm, xe GXT không phân bổ cho đơn đó
    B. Tính theo GXT (xe/ngày theo kg + lương giao), không tính phí điểm BC
    C. Từng case **(đề xuất cho tới khi có quy luật)**

## F. Phí giao theo điểm

20. ⚠ Cùng cửa hàng/ngày có dòng thuộc hai loại phí điểm (vd. BC và Lai Châu) hoặc chưa có giá: A. giữ N/A **(đề xuất)** B. ưu tiên loại: …

## G. Doanh thu, VAT, thực chi và lãi lỗ

21. ⛔ VAT: báo cáo cuối dùng trước hay sau VAT? Thuế suất áp cho doanh thu và từng loại chi phí?
22. ⛔ Thực chi: nguồn nào là số thực trả (hóa đơn/bảng kê NCC, kế toán)? Định dạng, ai gửi, khi nào chốt tháng? Có thì em đối soát dự kiến với thực chi; chưa có thì P&L cuối vẫn N/A.
23. ⚠ Khi nào được công bố "tổng chi phí/lãi lỗ" cho một kỳ?
    A. Chỉ khi mọi khoản trong phạm vi đủ nguồn và đã có thực chi **(đề xuất)**
    B. Khi đủ nguồn dự kiến, ghi rõ "dự kiến"

## H. KPI, SLA và nhân sự (phạm vi app nhưng chưa có nguồn)

24. ⚠ SLA: nguồn nào có giờ giao thực tế và cam kết (delivered chưa phải SLA)? Định nghĩa đúng hạn là gì?
25. ℹ Hiệu quả nhân sự kho/bốc xếp: có chấm công/giờ công/sản lượng theo ca không? Chưa có thì phần này N/A.

## I. Vận hành tự động

26. ⛔ Khi lịch chạy mà có dữ liệu thiếu:
    A. Gửi báo cáo phần đủ nguồn vào nhóm + danh sách `/thieu` gửi riêng owner **(đề xuất)**
    B. Gửi cả danh sách thiếu vào nhóm
27. ⛔ Owner trả lời câu hỏi/ngoại lệ ngay trên Telegram (vd. nút "Đúng, cộng hai phần kg") để bot lưu thành quy tắc có ràng buộc nguồn, thay cho nạp file DPAPI:
    A. Đồng ý, chỉ owner, có xem lại trước khi lưu và ghi audit **(đề xuất)**
    B. Không, vẫn qua bước nạp file đã duyệt
28. ℹ Chạy 24/7 trên Cloudflare Workers Paid: đồng ý để em làm **thiết kế + cấu hình mẫu + test local** (chưa deploy, chưa chuyển secrets/dữ liệu)? A. Đồng ý **(đề xuất)** B. Chưa
29. ℹ Kg cũng hiển thị bỏ số 0 thập phân như tiền (vd. `27,5 kg` thay vì `27,500 kg`)? A. Có **(đề xuất)** B. Giữ 3 số

## Ghi nhận câu trả lời

| Câu | Trả lời | Ngày | Đã đưa vào code/policy |
|---|---|---|---|
| 1 | B — Apps Script; công ty không cấp Viewer cho service account | 27/10/2026 | Chờ thiết kế (xem câu hỏi bổ sung) |
| 2 | A | 27/10/2026 | Có: lịch vẫn gửi phần Booking khi Raw cũ; bốc xếp theo quy tắc hai chặng N/A (`RAW_STALE`) |
| 3 | Lấy khối lượng của mã GHN | 27/10/2026 | Chờ làm rõ nguồn (Raw hay ngày khác) |
| 4 | A | 27/10/2026 | Giữ nguyên |
| 5 | A | 27/10/2026 | Giữ nguyên; `/thieu` báo dòng |
| 6 | A, báo owner check và đưa phương án | 27/10/2026 | `/thieu` báo dòng |
| 7 | A | 27/10/2026 | Giữ nguyên |
| 8 | Theo GHN chắc chắn phải có Raw; không có thì báo owner | 27/10/2026 | `/thieu` báo dòng (`two_leg_unresolved`) |
| 9 | A | 27/10/2026 | Không tính giá AG riêng |
| 10 | A, bắt owner bổ sung | 27/10/2026 | `/thieu` báo dòng |
| 11 | Không có; bắt owner bổ sung | 27/10/2026 | `/thieu` báo dòng biển đại diện |
| 12 | A, bắt owner bổ sung | 27/10/2026 | `/thieu` báo dòng |
| 13 | Cước theo loại tải cột T của chuyến | 27/10/2026 | Chờ làm rõ cách đếm lượt |
| 14 | A, bắt owner bổ sung | 27/10/2026 | `/thieu` báo dòng |
| 15 | A, bắt owner thông tin lại | 27/10/2026 | `/thieu` báo dòng |
| 16 | B | 27/10/2026 | Chờ làm rõ (AG Booking vs S Raw) |
| 17 | NCC gửi, mỗi tháng một lần | 27/10/2026 | Chờ file mẫu |
| 18 | Cột Y ghi chú: Khai trương, CHKT, KT… | 27/10/2026 | Chờ làm rõ tên cột/từ khóa/quy tắc |
| 19 | Báo owner check, xác nhận từng xe | 27/10/2026 | Giữ N/A + báo |
| 20 | Báo owner | 27/10/2026 | Giữ N/A + báo |
| 21 | Trước VAT; VAT 8% | 27/10/2026 | Báo cáo trước VAT |
| 22 | Hóa đơn/bảng kê NCC, gửi cuối tháng | 27/10/2026 | Chờ file mẫu |
| 23 | B | 27/10/2026 | Có: `/chiphi` hiện TỔNG DỰ KIẾN trước VAT chỉ khi mọi phần đủ nguồn |
| 24 | Phú Thọ D+1; Sơn La, Điện Biên, Lai Châu D+2 từ cột E | 27/10/2026 | Chờ nguồn giờ giao thực tế |
| 25 | Không có chấm công | 27/10/2026 | Nhân sự N/A |
| 26 | A | 27/10/2026 | Nhóm nhận Kết luận + số nhóm thiếu; danh sách dòng chỉ qua `/thieu` của owner |
| 27 | A | 27/10/2026 | Có: `/duyet` (cộng phần kg), quy tắc v2 giữ qua làm mới nguồn |
| 28 | B — chưa làm Cloudflare | 27/10/2026 | Không làm |
| 29 | A | 27/10/2026 | Có: kg bỏ số 0 thập phân |
| C1 | Có Apps Script, có Drive for desktop, gửi 13h | 28/10/2026 | Có: `scripts/apps_script/ExportSupraTabs.gs` + `python -m ops_team.drive_import` |
| C2 | A — kg Raw cùng GHN, cùng ngày | 28/10/2026 | Có: đợt trống L lấy kg Raw DELIVER cùng ngày khi rõ ràng |
| C3 | A — mỗi loại T khác nhau trên cùng xe/ngày = 1 lượt theo T đó; cùng T = 1 lượt | 28/10/2026 | Có (FTL và xe trung chuyển, khi lớp quy tắc đang bật) |
| C4 | Đúng — chia kg theo gram Raw từng xe, biển Raw phải khớp AG | 28/10/2026 | Có: FTL chia L theo tỷ lệ gram Raw khi biển khớp AG |
| C5 | Cột "Ghi chú 2" (Y), ghi chú tự do có KT/CHKT/Khai trương; tính FTL theo T + km thực tế | 28/10/2026 | Có: tự nhận khai trương từ Ghi chú 2 |
| C6 | `time_updated` của DELIVER `is_succeeded`; D+1/D+2 đến 23:59 | 28/10/2026 | Có: lệnh `/sla` |
| C7 | VAT 8% cho doanh thu và cước NCC | 28/10/2026 | Có: dòng đối chiếu hóa đơn trong tổng dự kiến |
| C8 | Chưa có mẫu; kế toán gửi cuối tháng | 28/10/2026 | Chờ file đầu tiên |
| Tháng | A — giữ nguyên giá/quy tắc sang tháng sau, nhắc xác nhận | 28/10/2026 | Có: chuyển tháng tự động + `/giugia` |
| R1 | Raw DELIVER trống biển = xe đi thẳng (nhân viên bấm "không gán giao") | 29/10/2026 | Có: GHN giao thẳng (GXT/NCC/Lai Châu) trống biển = xe AG tự giao, không thiếu biển, không hai chặng; GHN trung chuyển GXT vẫn báo thiếu |
| R2 | SLA lấy `time_ket_thuc_chuyen` | 29/10/2026 | Có |
| R3 | Kg Raw cho đợt trống L: từ ngày xuất tới trước lần xuất kế tiếp | 29/10/2026 | Có |
| R4 | Đơn vừa đi xe AG vừa đi xe S: A — chia L theo gram Raw | 29/10/2026 | Có: phần xe S tính hai chặng (có bốc xếp DC), phần xe AG giao thẳng; gram lỗi thì giữ |
| R5 | GHN 2 chuyến cùng xe S/ngày: A — cộng kg, lương giao một lần | 29/10/2026 | Có: cộng kg theo mã chuyến khác nhau; trùng đúng mã chuyến vẫn giữ |
| R6 | Xe GXT/ngày thiếu/lệch dữ liệu: lấy theo Raw | 29/10/2026 | Có: chi phí xe GXT dùng biển S trên Raw làm xe thật kể cả khi khác AG; bốc xếp DC vẫn dùng kiểm tra hai chặng |
| R7 | Kg cột L thiếu hoặc sai định dạng: lấy theo Raw | 29/10/2026 | Có: ưu tiên DELIVER thành công, rồi DELIVER, rồi PICK trong khoảng đợt; không cộng trùng lần giao thất bại |
| R8 | "Raw chưa cập nhật": file Raw anh gửi cũng chỉ tới load 22/10 (xuất 23/10 08:00), 440 GHN mới có PICK | 29/10/2026 | Đúng là đang chờ giao; không phải lỗi |
| R9 | 199 dòng thiếu biển S (đơn qua GXT): Mộc Châu tính theo điểm (giá owner đặt, lưu trong kho quy tắc mã hóa); khu vực khác là giao thẳng, không lương giao | 29/10/2026 | Có: `python -m ops_team.set_area_point_rate` đặt giá; điểm = cửa hàng × ngày load |
| R10 | Đợt chưa có Raw (327 dòng): lấy theo file CS (ngày E) | 29/10/2026 | Có: không có chặng Raw thì theo danh mục tuyến CS của ngày E; đếm riêng `booking_routing_fallback_events` |
| R11 | 139 dòng xe GXT: "biển S có mà sao không thấy" | 29/10/2026 | File Excel ghi lý do thật từng xe/ngày (chở lẫn, kg không khớp, dòng lỗi...) |
| R12 | 4 dòng thiếu kg: lấy đúng ngày E của CS | 29/10/2026 | Excel liệt kê mọi dòng Raw của GHN đó để đối chiếu; chưa có kg ở ngày E thì vẫn giữ |
| R13 | Xe S chở lẫn đơn Supra nhiều mô hình; đơn lặp do giao thất bại hôm trước: lấy log ngày mới nhất, ghi nhận 1 lần | 29/10/2026 | Có: đơn GXT chia chi phí xe theo kg + lương giao; đơn Supra mô hình khác (BC/FTL/NCC/khai trương) tính vào hàng khác vì đã trả theo mô hình riêng; đơn chỉ tính ở ngày load mới nhất, lặp cùng chuyến tính 1 lần |
| R14 | Thiếu kg: lấy cột L theo mã GHN, không có thì theo Raw | 29/10/2026 | Có: L của cùng GHN (một giá trị duy nhất) trước, rồi Raw; báo số đợt mượn L để kiểm tránh trùng |
| R15 | 3 dòng chưa có dữ liệu: ghi chú CS bổ sung | 29/10/2026 | Có: nhãn "CS bổ sung cột L" |
