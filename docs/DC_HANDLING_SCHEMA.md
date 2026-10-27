# Staging bốc xếp DC v3 — schema bàn giao

Import `HANDLING_SCHEMA`, `HANDLING_BUSINESS_HEADERS`, `HANDLING_POLICY`, `catalog_digest` từ `ops_team.supra.booking_handling`. Chỉ policy/version/semantics nằm trong code. Giá, địa bàn và store ID thật chỉ nằm trong DPAPI business staging/snapshot, không ghi trong descriptor plaintext hoặc Git.

Payload DPAPI có đúng các key:

`schema, source_url, spreadsheet_id, tab, gid, source, retrieved_at, raw_sha256, raw_column_count, headers, rows, confirmed_business_rules, confirmed_weight_rules, confirmed_handling_policy, handling_catalog, handling_catalog_sha256`

- `schema = HANDLING_SCHEMA` (`supra-dc-handling-source-intake-v3`); `source = AUTHORIZED_BROWSER_VIEWER_CSV_EXPORT`; raw_column_count=37.
- `headers = HANDLING_BUSINESS_HEADERS`: **A/B/C/E/F/G/H/J/L/N/O**, đúng thứ tự 11 cột. Mỗi row vẫn `{sheet_row: số dòng Sheets, values: [11 giá trị]}`. Không giữ I/tên cửa hàng hoặc cột PII khác.
- confirmed_business_rules và confirmed_weight_rules giữ đúng format staging v2 (RULE/WEIGHT_RULE bỏ key version).
- `confirmed_handling_policy = HANDLING_POLICY` nguyên dictionary; `handling_catalog_sha256 = catalog_digest(handling_catalog)`.

Catalog có đúng các key:

```text
schema: supra-dc-handling-catalog-v1
version: định danh business version (A-Z/a-z/0-9/_.-)
source_pin: {spreadsheet_id, tab, gid}
period: YYYY-MM
provenance: USER_CONFIRMED_IN_CODEX
confirmed_date: YYYY-MM-DD
rates: [{rate_id, from, to, vnd_per_kg, currency, evidence_ref}]
routing: [{rule_id, scope, province, district, store_id, routing, from, to, evidence_ref}]
evidence_hashes: [{file_id, sha256}]
```

Dates from/to đóng (inclusive) và nằm trong period đã xác nhận. `vnd_per_kg` là chuỗi số nguyên không âm, currency VND. Scope `DISTRICT` có province/district, store_id rỗng; scope `STORE` có H thật trong store_id, province và district dự kiến từ đối chiếu nguồn. Routing chỉ TRANSFER/DIRECT. Dùng rule_id/rate_id trung tính (R1/R2, D1.../S1), không gắn mã cửa hàng vào ID. evidence_ref là nguồn/pointer (không phải prompt); evidence_hashes lưu hash JSON xác nhận/mapping nguồn được dùng. Không suy năm hoặc giá từ ghi chú phí giao.

Hai giá tháng 10 dùng các khoảng xác nhận 01–18/10 và 19–31/10. Việc chặn khoảng cuối tại 31/10 là scope tháng đã được owner xác nhận; không chứng nhận ngày hết hiệu lực hợp đồng sau tháng 10. Routing STORE lấy H trong RAM từ `store_exception_source_match.dpapi`, không in/log ID hoặc ghi vào plaintext descriptor; ghi dấu nguồn/hash để đối chiếu.

Descriptor plaintext có đúng các key:

`schema, source_url, tab, gid, retrieved_at, raw_sha256, raw_column_count, headers, confirmed_business_rules, confirmed_weight_rules, confirmed_handling_policy, handling_catalog_sha256, handling_catalog_version, row_count, minimized_plaintext_sha256`

Giá trị chung phải khớp payload. `handling_catalog_version` khớp catalog.version; `minimized_plaintext_sha256` là SHA-256 bytes JSON plaintext thực được đưa vào save_protected. Không nhét catalog hoặc store ID vào descriptor. App kiểm pin/range facts hiện có, source/hash/schema/policy/version, từng dòng và thời gian trước pointer commit. Không đổi facts/source/lịch 13h.

API migration dùng cùng `ingest_minimized_snapshot(path, descriptor, state, owner_id, cancel=None)`; parent chỉ tạo export mới sau QA. Full37 CSV/connector dùng catalog đã được duyệt trong snapshot hiện có đúng source; không tự lấy JSON ngoài hoặc áp sang nguồn khác. Staging v1/v2 tiếp tục được hỗ trợ, handling N/A khi không có config. Record mới giữ catalog/policy và digest dưới DPAPI; không gọi model hoặc Telegram khi import.

Hàm RAM-only `catalog_from_owner_confirmations(costs, models, store_match, source_pin, version=..., period=..., evidence_hashes=...)` có thể chuyển các JSON xác nhận đã cho phép và mapping đã giải mã thành catalog. Hàm không mở file, không in ID; caller chỉ save catalog trong payload DPAPI. Nếu giữ catalog đã duyệt để export fresh, giữ nguyên bytes/identity catalog và chỉ cập nhật source metadata theo thời điểm thật. Chi tiết tính/kiểm/model: [DC_HANDLING.md](DC_HANDLING.md).

Phép tính giữ Decimal chính xác, **không làm tròn DO, GHN hoặc tổng component**; subtotal theo từng mức giá cũng giữ exact Decimal. Báo cáo hiển thị tối thiểu ba chữ số thập phân VND, nhiều hơn nếu số nguồn cần. Chưa có quy tắc làm tròn nghiệm thu/thanh toán; không tự biến phần lẻ VND thành số tiền actual.
