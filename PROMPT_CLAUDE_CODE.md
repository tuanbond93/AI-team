# Prompt giao việc cho Claude Code

Bạn tiếp quản dự án **AI team** hiện có. Mục tiêu là tiếp tục hoàn thiện trợ lý vận hành logistics của tôi: chi phí, kế toán/đối soát, hiệu quả nhân sự, sử dụng xe và KPI. Tôi giao bạn làm maintainer, được sửa mã, kiểm thử và tài liệu trong phạm vi này. Ghi chú lịch sử yêu cầu Sol là người duy nhất sửa code đã được thay bằng yêu cầu bàn giao cho Claude này.

## Đọc và kiểm tra trước

1. Đọc `README.md`, `docs/ENGINEERING_HANDOFF.md`, `docs/LOCAL_STATE.md`, `TEST_RESULTS.md` và các schema được liên kết. Đọc mã hiện tại trước khi đề xuất thay framework.
2. Xác định đang ở clone phát triển hay workspace Windows đang chạy bot. Repo không chứa dữ liệu thật hoặc secrets. Nếu làm trên máy gốc và có quyền, đọc bản bàn giao mới nhất của đúng app; dùng dữ liệu/policy/proof được bảo vệ trong RAM. Không sao chép chúng vào repo.
3. Kiểm phiên bản Python và môi trường sẵn có; ưu tiên `$HOME\.venv` trên Windows. Không cài Vnstock, công cụ chứng khoán hoặc nâng package hàng loạt cho dự án này.
4. Chạy demo và các kiểm thử cần thiết bằng fixtures/HTTP giả. Ghi đúng kết quả thực tế, phân biệt restricted runtime bị chặn socket với assertion failure. Không gọi model thật hoặc gửi Telegram thật để kiểm thử.

## Việc cần làm tiếp

**Ưu tiên 1: dữ liệu và phép tính đúng.** Rà các chỗ còn giữ riêng để phân biệt thiếu dữ liệu thật với logic join quá rộng/quá chặt. Tự đối chiếu nguồn và tái hiện bằng test tổng hợp trước khi hỏi tôi. Chỉ hỏi dữ liệu mà code và nguồn được phép thực sự không cung cấp được. Không yêu cầu tôi giải trình lại toàn bộ nhóm S khác AG đã được quy tắc bao phủ.

Tuân thủ các xác nhận đã có:

- J Booking là sản phẩm trong thùng; giữ đúng khóa GHN/SO/DO, không quy đổi thành kiện.
- Bốc xếp theo **GHN + ngày E**, cùng ngày một đợt, khác ngày đợt khác. Nhiều DO không nhân kg. Thiếu L không mượn từ ngày khác. Cộng hai phần kg chỉ khi có ngoại lệ source-bound được owner xác nhận.
- Hai chặng GXT: AG từ DC về GXT, S từ GXT giao khách khi đủ bằng chứng liên kết. Không tự coi S khác AG là sai biển. Giữ ngoại lệ NCC/BC/Lai Châu; không sửa nguồn hoặc lấy share S phân bổ AG.
- T là loại tải Booking để tính cước, không tự xác minh tải vật lý. Áp đúng số lượt trong các ngoại lệ được duyệt; không generalize thành một lượt cho mỗi loại T xuất hiện.
- Một đơn khai trương đã được chốt FTL 8T theo Booking và km thực tế, thay mô hình BC theo điểm. Chỉ đúng đơn/ngày đó. Km/giá còn thiếu thì N/A, không lấy giá tỉnh khác, không cộng BC hoặc lương giao GXT cho đơn FTL ấy.
- Tách tiền xe GXT/ngày và lương giao theo kg trả thêm. Chia xe dùng chung bằng kg của chính xe/ngày đủ nguồn; không dùng tỷ lệ toàn đội. Phí điểm không nhân theo DO.
- DC và nhân sự bốc xếp GXT là hai khoản khác nhau. Đối chiếu từng đợt với gói đã được chấp nhận để tránh cộng DC hai lần.
- Giữ phạm vi chi phí owner đã loại; không hỏi lại như khoản thiếu. Không đổi giá/tỷ lệ đã chốt chỉ để làm cho tổng khớp.
- Raw DELIVER dùng J gram/1.000, đối chiếu ALL Booking. Giữ khác biệt giữa ngày xuất, ngày load, xe/ngày, mã chuyến nguồn và lượt thực tế. Không gọi `delivered` là SLA thực tế.

Các đầu vào còn chờ gồm nhật trình/biểu giá km NCC, kg/biển còn trống hoặc lỗi, liên kết đợt chưa rõ và hai case BC/GXT khác. Lấy định danh cụ thể từ local proof nếu được phép; không bịa chúng từ repo. Chưa có dữ liệu thì tạo test/mẫu tối thiểu và câu hỏi cụ thể, giữ N/A; không biến thiếu thành 0 hoặc công bố P&L cuối.

**Ưu tiên 2: trợ lý nói dễ hiểu.** Tin nhắn Telegram mở đầu bằng kết luận và việc cần làm. Khi thiếu dữ liệu, nói rõ thiếu trường nào, thuộc đơn/ngày/xe nào và người dùng cần đưa gì. Tránh Coordinator/Auditor labels, SHA, tên metric nội bộ và trace dài trong câu trả lời thường. Giữ chi tiết kỹ thuật ở audit và đảm bảo auditor xét đúng phiên bản sửa, không mất phản biện chưa giải quyết.

**Ưu tiên 3: kiểm tra các đường đi của ứng dụng.** Xác minh rule layer được dùng nhất quán trong `/chiphi`, `/xe`, model context và báo cáo lịch. Hiện lớp tính thủ công đã được làm; không khẳng định mọi đường đi khác đã đồng bộ. Nếu nối thêm, chuẩn bị code/test và tác động đến plan/source identity rõ ràng; không tự bật lịch hay sửa approved plan. Lỗi API 429/timeout phải được báo đúng, không fallback ngầm sang demo/provider khác hoặc tự tăng gói.

**Ưu tiên 4: phương án 24/7 sau khi tính đúng.** Tôi có Cloudflare Workers Paid và quan tâm Apps Script đẩy dữ liệu. Hãy kiểm tra code và tài liệu chính thức hiện hành rồi thiết kế bước chuyển nhỏ có thể thử và rollback. Streamlit/DPAPI Windows không thể được coi là ứng dụng Workers có sẵn. Apps Script/Google source phải đúng quyền công ty; không lách hạn chế bằng cookie, API ẩn hay tài khoản khác. Chưa mua dịch vụ, public deploy, đổi sharing hoặc di chuyển secrets/dữ liệu thật khi chưa có phạm vi và phê duyệt phù hợp. Nếu chưa được phép triển khai, hoàn thành thiết kế, cấu hình mẫu không bí mật và test local để tôi xem.

## Cách triển khai và nghiệm thu

- Làm từng thay đổi nhỏ trên nhánh riêng; giữ chức năng cũ khi layer mới chưa được cài, fail closed khi binding không hợp lệ. Không làm lại toàn bộ hệ thống.
- Kiểm arithmetic độc lập theo đúng grain; thử cả kỳ đầy đủ và hai nửa, blank so với zero, nhiều DO/ngày, nhiều ngày, các ngoại lệ đúng/sai source, tránh double count, schema/atomic rejection và audit replay chính xác.
- Model chỉ diễn giải kết quả có bằng chứng; tính toán bằng code. Không thay quy tắc nghiệp vụ bằng suy luận LLM.
- Tuyệt đối không commit vault, token, khóa, CSV/Excel thật, báo cáo doanh nghiệp, config/proof DPAPI, prompt/payload của ứng dụng hoặc log có dữ liệu riêng tư. Dùng dữ liệu tổng hợp trong tests và tài liệu repo.
- Một poller/worker cho một vault. Với thay đổi code production, chuẩn bị đầy đủ kiểm tra, backup, rollback và quyền stop/import/restart trước khi dừng; kiểm worker cũ thoát và worker mới khỏe. Cấu hình đọc theo request có thể cập nhật nguyên tử không restart nếu đã kiểm chứng.
- Không tự gửi tin Telegram thay tôi, không chi tiền, điều xe hay đổi nhân sự. Không lấy thông tin xác thực từ ứng dụng khác.
- Cập nhật tài liệu bàn giao sau thay đổi. Không ghi dữ liệu thật vào bản HANDOVER nằm trong Git; bản ghi local riêng giữ ngoài repo.

Bắt đầu bằng kiểm tra repo và báo ngắn: phần nào đã chạy, phần nào còn thiếu dữ liệu, lỗi kỹ thuật nào có thể tự sửa ngay. Sau đó thực hiện phần đã được giao, không dừng ở một bản kế hoạch. Chỉ hỏi tôi khi câu trả lời thực sự cần để xác định nghiệp vụ hoặc quyền triển khai; chuẩn bị kết quả cụ thể trước khi xin phê duyệt cuối. Trả lời tiếng Việt, ngắn và dễ hiểu, nêu đúng đã thay gì, đã kiểm thế nào và còn thiếu gì.
