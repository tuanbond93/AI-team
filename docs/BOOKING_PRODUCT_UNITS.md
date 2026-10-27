# Số sản phẩm trong booking — 26/10/2026

Bot giữ `product_units` riêng với `pieces` (kiện), `cartons` (thùng), kg và tiền. Chỉ tiêu mới xuất hiện trong báo cáo code bốn phần của `/chief`, `/run`, báo cáo mặc định và metric context của các vai trò khi người dùng hỏi. Bổ sung sản lượng không làm nguồn trở thành dữ liệu tài chính: revenue, cost, GP vẫn N/A và các báo cáo intake không gọi AI (0 HTTP).

Đợt bổ sung L/kg giữ quy tắc J ở tài liệu này độc lập: record schema 3 thêm `weight_rules`, staging v2 có chín cột A/B/C/E/F/G/J/L/N; staging v1 tám cột và record cũ tiếp tục đọc, kg N/A. Xem [BOOKING_GHN_WEIGHT.md](BOOKING_GHN_WEIGHT.md) về tổng kg tính một lần/GHN và trường hợp giữ ngoài tổng.

## Quy tắc nghiệp vụ và phạm vi

Owner xác nhận trong Codex ngày 26/10/2026: file `1fifr6Wacb5zMxWiWgM-HH9xl_z2Px9bGJ4mJk1twHiY`, tab `DC PHÚ THỌ`, gid 0; B là mã GHN, J `Qty (Units)` là số sản phẩm bên trong thùng. Cộng J của các dòng DO thuộc cùng GHN, theo khóa **GHN + SO + DO** (B/F/G). Rule version `supra-product-units-owner-2026-10-26-v1`, provenance `USER_CONFIRMED_IN_CODEX`; không tạo Telegram message ID hoặc giả đã đối soát độc lập.

- Lọc **từng dòng DO theo Ngày xuất E**, kỳ và cutoff của kế hoạch hiện có. Một GHN có nhiều DO/ngày xuất là các đợt giao, không tự coi là trùng. Không kéo các DO ngoài kỳ/cutoff vào tổng của GHN.
- Chỉ nhận Qty là số nguyên không âm rõ nghĩa; số 0 thực được giữ là 0. Trống, lỗi `#N/A`, dấu phân tách mơ hồ, số âm hoặc số thập phân không hợp lệ không được đổi thành 0.
- Thiếu/sai GHN/SO/DO hoặc khóa GHN+SO+DO lặp ở nguồn được giữ nguyên và cách ly. Không chọn dòng cuối, xóa trùng hoặc cộng trùng. Một dòng lỗi làm phần GHN bị ảnh hưởng chưa đầy đủ; toàn phạm vi `product_units` là N/A. `eligible_product_units` chỉ là phần đủ khóa, gắn nhãn rõ không đại diện tổng toàn phạm vi.
- SO/DO xuất hiện ở nhiều GHN là cảnh báo để owner đối chiếu, chưa tự loại dòng theo một quy tắc chưa được xác nhận. Ngày/tỉnh/Qty khác nhau trên cùng khóa GHN+SO+DO cũng thuộc nhóm khóa lặp cần làm rõ.
- Không suy số kiện, thùng, kg, CBM, chuyến hoặc doanh thu từ sản phẩm. Không dùng hệ số 15.8586 hoặc ghép lineage ước tính lịch sử. Cước owner đã xác nhận được giữ ở catalogue riêng; đợt này không tính doanh thu mới.

Snapshot cũ không có Qty/mapping vẫn đọc được, hiện sản phẩm N/A. Fixture bốn header và nguồn khác không tự được gán quy tắc J. Connector/full CSV chỉ bật quy tắc khi khớp đúng file/tab/gid và 37 header; staging business-only có kiểm tra riêng bên dưới.

## Dữ liệu lưu và truy vết

Snapshot schema 2 lưu các trường booking cũ, `source_so`, `source_do`, `source_product_units`, `source_province` và metadata rule. `business_evidence` chỉ giữ A/B/C/E/F/G/J/N cùng `sheet_row`, dưới DPAPI. Giữ nguyên giá trị thiếu, số dòng và các dòng cần kiểm tra. Nhánh CSV dùng record CSV logic nên ô nhiều dòng không làm lệch sheet_row.

Mỗi chỉ tiêu có source reference, source_count, sample flag và SHA-256 phiên bản snapshot. Rule version nằm trong source provenance. Mã GHN/SO/DO và raw rows không gửi vào model; chỉ các chỉ tiêu tổng hợp, quality flags, mapping đã xác nhận và mẫu tham chiếu dòng. Model metadata dùng chung để giảm lặp; value/formula/unit/label, bằng chứng và exact answer cho Auditor vẫn giữ. Trần 7.500 token/phút, 1.500 output Free Pilot, 60 calls và 120.000 token/ngày không đổi.

## Nạp staging DPAPI do owner đã cho phép

`ingest_minimized_snapshot(path, descriptor, state, owner_id, cancel=None)` nhận **chỉ** staging schema `supra-product-source-intake-v1`:

1. Descriptor SHA-256 phải khớp plaintext sau giải mã; raw CSV hash, 37 cột nguồn, tám header business và policy phải khớp descriptor. Chỉ giải mã file staging được caller chỉ định, không lấy key app khác.
2. Spreadsheet ID/tab/gid/range phải khớp facts đã pin. Range hiện có phải là `'DC PHÚ THỌ'!A1:AK...`; physical sheet_row duy nhất, tăng dần, nằm trong giới hạn. Không sửa facts hoặc chọn nguồn mới.
3. `retrieved_at` có timezone, không ở tương lai, không thay snapshot mới bằng bản cũ. Không sửa timestamp để vượt freshness. Staging cũ có thể dùng đối chiếu có nhãn; báo cáo mới vẫn chịu guard 15 phút hiện có.
4. Lưu snapshot versioned DPAPI, đổi pointer sau cùng. Hủy trước commit pointer hoặc lỗi validation giữ pointer cũ. Nếu hủy sau khi ghi blob, chỉ để lại blob mã hóa chưa được chọn.
5. Hàm trả `INGESTED_NOT_SCHEDULED`, digest, safe_rows, rule version và raw hash. Không sửa settings, facts, paired, approved plan, lịch, usage/quota; không restart hoặc gửi báo cáo.

CLI cho người điều phối nạp khi đã hoàn tất QA nguồn mới:

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Content -LiteralPath 'C:\absolute\source-descriptor.json' -Raw |
    python -m ops_team.telegram_snapshot --minimized 'C:\absolute\source-minimized.dpapi'
```

Python phải thuộc venv đang dùng. CLI dùng owner hiện có trong cấu hình; descriptor đi stdin, không đi command line. **Không thêm `--send-scheduled` trong bước nạp/QA.** CLI cũng giữ chế độ payload/CSV cũ. Không cần raw CSV chứa PII để nạp staging này. Refresh đúng nguồn trước khi kích hoạt production; bước reload/activation do người điều phối thực hiện riêng sau QA.

## Nghiệm thu

`tests/test_booking_product_units.py` kiểm tra grain nhiều DO/ngày, cutoff, zero/missing/invalid, khóa trùng/mâu thuẫn, cảnh báo SO/DO nhiều GHN, legacy N/A, CSV multiline, source pin/hash/policy, cancel/stale, DPAPI restart, giữ mã plan/lịch 13h và báo cáo không gọi provider. Regression có số Qty dạng numeric cùng ô None: JSON vẫn hợp lệ, ô thiếu được giữ thiếu khi load lại. Kiểm thử real provider adapter dùng HTTP/đồng hồ giả, ba lượt Operations có history và một luồng Operations→Auditor, kiểm exact review task và cap không đổi.

Mẫu [SYNTHETIC_PRODUCT_UNITS_REPORT.txt](../outputs/chief-auditor/SYNTHETIC_PRODUCT_UNITS_REPORT.txt) dùng dữ liệu giả để minh họa 37 sản phẩm với kg/kiện/thùng và tiền N/A; không phải kết quả doanh nghiệp.
