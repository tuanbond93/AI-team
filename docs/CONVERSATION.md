# Hội thoại tự nhiên và trí nhớ: audit, thiết kế, đánh giá, rollback

Phạm vi: lớp trò chuyện của bot Telegram AI Team. Phần tính số (Booking/Raw, chi phí, SLA, xe) không đổi.

## 1. Audit kiến trúc trước khi sửa

Luồng cũ: `telegram_bridge.parse_message` → `AssistantResponder.answer` → lệnh code hoặc `TelegramAgent.answer` (model).

| Hạng mục | Hiện trạng | Vì sao máy móc |
|---|---|---|
| Persona / SOUL.md | Không có. Giọng nói nằm rải rác trong chuỗi `task` của từng payload. | Không có tính cách chung, mỗi đường trả lời một kiểu. |
| Đầu ra model | Mọi câu, kể cả "chào em", buộc theo JSON schema phân tích (`summary`, `observations`, `proposals`, `requests`...). `telegram_presentation.conversation` ghép lại theo khuôn cố định: kết luận, các ý, cảnh báo, câu hỏi, dòng trạng thái, ghi chú nguồn. | Tin nào cũng thành một bản báo cáo, luôn kết bằng câu hỏi và dòng "/details". |
| Không có model | Tin thường trả "CHƯA CÓ AI/MODEL..." (bản 608fed1). Bản 4389a1f thêm router từ khóa, nhưng lời dẫn vẫn cố định ("Dạ, ..."). | Lặp một kiểu mở đầu, không hiểu câu nối tiếp. |
| Working memory | `TelegramAgent._history`: 3 lượt gần nhất, chỉ trong RAM, chỉ cho đường model. | Không có "chủ đề đang nói", không hiểu "còn hôm qua thì sao". |
| Long-term memory | `BusinessState.memory` chỉ chứa dữ kiện nghiệp vụ do owner xác nhận; sửa nó sẽ tạm dừng lịch. | Không nhớ xưng hô, độ dài, việc đã xem. |
| Session | Khóa (chat, topic, user); /reset xóa RAM. | Ổn, giữ nguyên. |
| Định dạng | Báo cáo code có tiêu đề IN HOA, "Kết luận:", gợi ý "/details", "/thieu". | Đúng cho báo cáo, nhưng quá cứng khi người dùng hỏi bằng lời. |
| Lỗi tìm thấy | Bản 4389a1f bị lỗi khi nhắn "duyệt P…" (`span_text` không nhận mã P). Đã sửa. | |

Hermes Agent, Honcho, mem0, Graphiti, Letta: dự án không dùng framework nào trong số này. Bảng dưới giải thích vì sao không cài thêm.

## 2. So sánh các framework tham khảo

Đọc README trên GitHub ngày 29/10/2026.

| | Hermes Agent | Honcho | mem0 | Graphiti | Letta Code | Bản này |
|---|---|---|---|---|---|---|
| License | MIT | AGPL-3.0 | Apache-2.0 | Apache-2.0 | Apache-2.0 | trong repo |
| Persona | `SOUL.md`, `/personality` | — | — | — | memory blocks | `SOUL.md` + `persona.py` |
| Trí nhớ người dùng | MEMORY.md/USER.md, tìm phiên FTS5, Honcho | peer card, representation | user/session/agent | đồ thị thực thể | memory blocks tự sửa | preference/semantic/project/episodic |
| Xử lý thông tin đổi | — | suy luận nền | thêm mới, không xóa (thuật toán 4/2026) | hiệu lực hai mốc thời gian, vô hiệu hóa fact cũ | tự sửa block | `valid_to` + `superseded_by`, có giờ và nguồn |
| Phụ thuộc | runtime riêng, gateway Telegram riêng | Postgres + pgvector + LLM + tiến trình nền | LLM mỗi lần thêm + embedding | Neo4j/FalkorDB + LLM mỗi lần nạp | Letta server/cloud | SQLite sẵn có, mã hóa DPAPI, 0 LLM khi ghi nhớ |
| Hợp với dự án? | Không: thêm một poller Telegram nữa trên cùng vault (cấm), chạy tool tự do | Không: AGPL, gửi chat nội bộ ra LLM/dịch vụ ngoài | Không: tốn LLM mỗi tin, mặc định OpenAI | Không: thêm DB đồ thị | Không: lưu ở cloud | Có |

Ý tưởng đã lấy: SOUL.md (Hermes); thẻ người dùng gọn, chỉ đọc phần liên quan (Honcho/Letta); phạm vi theo user (mem0); giữ lịch sử thay đổi bằng mốc hiệu lực (Graphiti).

## 3. Thiết kế mới

```
tin thường ──► Conversation.handle (ops_team/conversation.py, ~1 ms, không gọi model)
               1 trí nhớ: nhớ / quên / xem / xưng hô / độ dài
               2 bản chất AI
               3 sửa câu ("không, ý anh là tuần trước") → chạy lại đúng lệnh
               4a nhắc chuyện cũ rõ ràng, phản bác kết quả vừa gửi
               4 việc: intent.route → /chiphi /thieu /sla /xe /run /details /duyet (chỉ xem trước)
               5 câu nối tiếp ("còn hôm qua?") → lệnh trước + ngày mới
               6 phàn nàn / bực / phản bác
               7 nhắc chuyện cũ → chỉ ghi chép đã lưu, không có thì nói không thấy
               8 tán gẫu: chào, cảm ơn, đùa, mệt
               9 phân tích ("tại sao", "có nên", "đánh giá") → đường Chief/AI cũ, số do code
              10 chưa rõ / chuyện phiếm → model chat (nếu có) hoặc câu trả lời trung thực
```

Thành phần:

| File | Vai trò |
|---|---|
| `SOUL.md` | Tính cách, cách nói, hai chế độ, trí nhớ, giới hạn. Model nhận phần gạch đầu dòng. |
| `ops_team/persona.py` | Xưng hô theo cách người dùng tự xưng hoặc theo ý đã lưu (anh/chị/bạn – em/mình). Trang trọng "Dạ… ạ" chỉ khi người dùng viết trang trọng. Nhiều cách mở đầu và không lặp cách dùng ở 3 lượt gần nhất. Gợi ý "nhắn 'chi tiết'" chỉ nói một lần mỗi cuộc. Không đụng tới số. |
| `ops_team/intent.py` | Hiểu viết tắt, không dấu: ko/k/dc/j/bn/cp/hqua/hnay/ntn/tks... |
| `ops_team/conversation.py` | Nhận diện loại tin, working memory trong RAM (lệnh gần nhất, 6 lượt, cách mở đầu vừa dùng), điều phối. Lỗi trong lớp này rơi về đường cũ, không làm hỏng bot. |
| `ops_team/conv_memory.py` | Bảng `conv_memory` trong `business.sqlite3` hiện có. Phạm vi riêng từng (nhóm, người dùng). Giá trị mã hóa DPAPI. Có nguồn (`telegram:<chat>:<message>` hoặc `code:<lệnh>`), giờ tạo, `valid_to`, `superseded_by`. Giới hạn số mục; kết quả công việc tự xóa sau 45 ngày. |
| `providers.py` | Loại phản hồi mới `chat`: JSON `{"reply": "..."}`, vẫn strict schema nên chạy được với Groq Free Plan. Phân tích nghiệp vụ giữ nguyên schema cũ. |
| `telegram_agent.TelegramAgent.chat` | Một lượt trò chuyện: persona + style + tối đa 3 ký ức liên quan + 3 lượt gần nhất. Không có metrics, không tool. Kiểm khóa/bí mật trước khi gửi và sau khi nhận. |

Quy tắc trí nhớ:

- Chỉ lưu khi người dùng bảo ("nhớ giúp…", "gọi chị là chị", "trả lời ngắn thôi"), hoặc khi code đã chạy xong một lệnh xem số. Không lưu suy đoán của AI.
- Từ chối lưu: mật khẩu, khóa, số tài khoản, giấy tờ, số điện thoại hay dãy số dài, sức khỏe, và các câu giống chỉ thị vượt quyền ("bỏ qua mọi quy tắc", "cấp quyền owner").
- Giá, phí, quy tắc, ngoại lệ không bao giờ vào trí nhớ trò chuyện; bot nhắc gửi qua bảng hoặc lệnh đã duyệt.
- Chỉ đưa ra ký ức có từ trùng với câu đang nói. Người khác trong nhóm không đọc được.
- Xem, sửa, xóa: nhắn "em nhớ gì về anh" hoặc `/nho`; "quên mục 3", "quên cái tuyến Sơn La đi" hoặc `/quen 3`; `/quen all`. Nói lại cùng một sở thích thì bản mới thay bản cũ, bản cũ vẫn giữ mốc thời gian.
- Ký ức đưa cho model được ghi rõ là dữ liệu không tin cậy, không phải lệnh.

Không đổi:

- Lệnh `/...`, lịch báo cáo, Chief/Auditor, cách tính số, quyền owner.
- Tin thường vẫn không thay đổi dữ liệu: "giữ giá" chỉ nhắc `/giugia`; "duyệt P…" chỉ cho xem trước.

## 4. Đánh giá

Chạy lại:

```
python scripts/eval_conversation.py --root . --label new
python scripts/eval_conversation.py --root <worktree commit cũ> --label base
python scripts/eval_conversation.py --compare base.json new.json --out docs/eval/main
```

- **Bộ chính:** `tests/conversation_eval/scenarios.json`, 46 kịch bản, 66 lượt. Gồm hội thoại nhiều lượt, cảm xúc, sửa lỗi, phản bác, tán gẫu, viết tắt, trí nhớ, an toàn, tách người dùng.
- **Bộ giữ lại:** `tests/conversation_eval/holdout.json`, 12 kịch bản (17 lượt) viết sau khi đã chỉnh xong bộ chính.
- Lệnh code được thay bằng đầu ra tổng hợp cố định, nên phép đo chỉ chấm lớp hội thoại. Không mạng, không model, không dữ liệu thật.

Rubric tự động (tỷ lệ check đạt):

- **naturalness:** không có câu rập khuôn hoặc nhãn kiểu "Kết luận:" hay "/details"; dòng đầu không phải tiêu đề IN HOA; độ dài hợp với câu hỏi.
- **relevance:** gọi đúng lệnh.
- **continuity:** câu nối tiếp hoặc câu sửa dùng đúng lệnh và đúng ngày; nhớ đúng ký ức.
- **accuracy:** giữ nguyên mọi con số của code; không bịa ký ức; không lưu thông tin nhạy cảm; không cho người không phải owner xem số.
- **persona:** xưng hô đúng; không nhận là người; không xuôi theo khi bị phản bác.

Bộ chính (`docs/eval/main/comparison.md`):

| Tiêu chí | 608fed1 (bot gốc) | 4389a1f (router từ khóa) | Bản mới |
|---|---|---|---|
| naturalness | 0.497 | 0.925 | 1.0 |
| relevance | 0.482 | 0.929 | 1.0 |
| continuity | 0.444 | 0.389 | 1.0 |
| accuracy | 0.633 | 0.847 | 1.0 |
| persona | 0.973 | 0.959 | 1.0 |
| đa dạng câu mở đầu | 0.015 | 0.182 | 0.636 |
| check đạt | 199/325 | 316/354 | 356/356 |

Bộ giữ lại (`docs/eval/holdout/comparison.md`):

| Tiêu chí | 608fed1 | 4389a1f | Bản mới, lần chạy đầu | Bản mới, sau khi sửa 2 lỗi |
|---|---|---|---|---|
| check đạt | 51/84 | 82/92 | 90/92 | 92/92 |
| đa dạng câu mở đầu | 0.059 | 0.471 | 0.882 | 0.882 |

Lần chạy đầu của bộ giữ lại được lưu nguyên ở `docs/eval/holdout/new_holdout_FIRST_RUN_before_fix.json`. Hai lỗi lần đó: "lãi hay lỗ" chưa hiểu, và câu phản bác có nhắc ngày bị hiểu thành lệnh xem lại số. Cả hai đã sửa, nên con số 92/92 không còn là phép thử độc lập.

Giới hạn của phép đo:

- Rubric do chính người làm viết, và bộ chính đã được dùng để chỉnh code. Điểm 1.0 ở bộ chính chỉ chứng minh các kịch bản đó chạy đúng, không chứng minh bot "giống người".
- Không dùng LLM tự chấm.
- **Đánh giá mù của người là bằng chứng chính, và chưa làm:**
  1. Mở `docs/eval/main/blind_review.csv` bằng Excel.
  2. Mỗi dòng là một lượt chat với hai câu trả lời X/Y, đã trộn ngẫu nhiên giữa bản cũ (4389a1f) và bản mới.
  3. Chấm 1–5 cho "tự nhiên" và "đúng ý", rồi chọn bản tốt hơn.
  4. Chấm xong mới mở `blind_key.json` để biết X/Y là bản nào.
- Đường model chat chỉ được test bằng model giả (kiểm cấu trúc, an toàn, ký ức). Chất lượng câu chữ khi có model thật chưa đo được vì máy này chưa cấu hình model.

## 5. Độ trễ, token, rủi ro

**Độ trễ.** Lớp hội thoại không gọi model để phân loại. Trung vị khoảng 0,5 ms mỗi lượt khi có ghi/đọc SQLite (đo bằng harness; lệnh code được thay bằng đầu ra giả). Bản cũ khoảng 0,1 ms. Thời gian tính số của lệnh thật không đổi.

**Token.**

- Không tốn token: chào, cảm ơn, đùa, tình trạng mệt, câu về bản chất AI, trí nhớ, nhắc chuyện cũ, phàn nàn, phản bác kết quả, và mọi lệnh số liệu.
- Tốn token chỉ khi đã cấu hình model và tin là chuyện phiếm hoặc chưa rõ. Mỗi lượt khoảng 1.300 token đầu vào (ước tính bằng `estimate_tokens`), tối đa khoảng 2.000 khi có 3 lượt trước và 3 ký ức. Đầu ra là một câu ngắn.
- So với đường `/ask` cũ: khoảng 600 token khi chưa có dữ liệu. Đường cũ còn nhân lên khi gửi kèm metrics.
- Lượt chat dùng chung ngân sách phiên với `TelegramAgent`: 20 câu, 60 lượt gọi.

**Rủi ro và cách giảm.**

| Rủi ro | Cách giảm |
|---|---|
| Hiểu sai ý, chạy nhầm lệnh | Chỉ chạy lệnh xem. Câu trả lời ghi rõ kỳ/ngày. Người dùng sửa bằng "không, ý anh là…". |
| Lộ thông tin giữa người dùng | Phạm vi theo (nhóm, user). Có test cho người khác hỏi "em nhớ gì về anh". |
| Prompt injection qua trí nhớ | Câu giống chỉ thị không được lưu. Ký ức gửi model được gắn nhãn dữ liệu không tin cậy. Quyền vẫn do code kiểm. |
| Model bịa số | Câu trả lời chat bị lọc: câu có con số kèm đơn vị tiền/kg/xe/%… bị bỏ. Không còn gì thì dùng câu trả lời viết sẵn. |
| Lưu thông tin nhạy cảm | Bộ lọc mật khẩu/khóa/tài khoản/giấy tờ/dãy số/sức khỏe. Dữ liệu mã hóa DPAPI, có lệnh xóa. |
| Từ khóa sót cách nói lạ | Rơi về "chưa rõ" kèm ví dụ, hoặc về model. Thêm từ khóa ở `intent.py`/`conversation.py`, có test. |
| Trong nhóm vẫn phải @bot hoặc trả lời bot | Đây là chế độ riêng tư (privacy mode) của Telegram. Muốn nhắn trơn thì owner tắt trong BotFather; chưa làm. |

## 6. Rollback

Không cần sửa code:

```powershell
# Tắt lớp hội thoại mới, quay về router từ khóa của 4389a1f. Áp dụng khi khởi động lại worker.
[Environment]::SetEnvironmentVariable('AI_TEAM_CONVERSATION','legacy','User')
```

Bảng `conv_memory` vẫn nằm trong vault nhưng không được dùng.

Gỡ hẳn:

1. Dừng worker theo `docs/DEPLOY_WINDOWS.md` bước 2.
2. Chép lại code từ bản backup.
3. Tùy chọn xóa dữ liệu trò chuyện: `/quen all` cho từng người dùng, hoặc chạy `DELETE FROM conv_memory` trên bản sao vault đã backup.
4. Khởi động lại.

Các bảng nghiệp vụ không bị đụng tới.
