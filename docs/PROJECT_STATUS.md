# Bàn giao Shortmaker

Cập nhật: **18/10/2026** (Asia/Bangkok).

## Mục tiêu đã thống nhất

Tool tạo short tu tiên từ tên nhân vật, ví dụ Dương Khai. Ưu tiên không tốn phí API; giữ nhập/sửa thủ công song song. Dựng kiểu ảnh nhân vật + chuyển động/hiệu ứng + lời đọc + phụ đề. Triển khai backend trước, frontend sau.

Máy xử lý mục tiêu: **i3-12100F, RX 5600 XT, RAM 16 GB**. Không mặc định có NVIDIA/CUDA. Kiến trúc cục bộ, một worker tuần tự, SQLite; giao diện tương lai gọi HTTP API.

## Đã có trong mã nguồn

- FastAPI, Swagger `/docs`, kiểm tra môi trường `/v1/capabilities`.
- Job đơn/hàng loạt, event cursor, checkpoint, retry giới hạn, pause/cancel/resume, phục hồi sau restart và khóa một tiến trình.
- Các bước `research -> plan -> assets -> speech -> captions -> timeline -> preview -> quality -> export`.
- DDGS tìm kiếm và đọc nguồn; Ollama tạo JSON xác định nhân vật/kịch bản. Kiểm tra trích dẫn/bằng chứng tồn tại trong nguồn.
- Tái sử dụng hồ sơ nhân vật và thư viện ảnh; ảnh tìm tự động được đánh dấu chưa xác minh.
- VieNeu CPU trong subprocess hoặc server TTS cục bộ; faster-whisper CPU trong subprocess; chọn phụ đề nhẹ theo đoạn TTS.
- Override các bước research/plan/assets/speech/captions; khóa tay và trạng thái `stale` để tránh mất bản sửa.
- FFmpeg: nhiều cảnh, zoom nhẹ, hạt sáng, lớp phủ alpha, nhạc giảm theo lời kể, phụ đề PNG Unicode; xuất MP4/SRT/metadata.
- Thu hồi duyệt và chặn tải final cũ khi chỉnh dữ liệu liên quan.
- AI endpoints giới hạn loopback; không có adapter trả phí. URL nguồn giới hạn public IP/redirect/kích thước; bảo vệ browser cross-origin và tùy chọn Bearer token.
- Script setup/start PowerShell; file cấu hình mẫu và dependency lock cho môi trường backend đã kiểm chứng.

## Đã kiểm chứng trên laptop

**28 tests passed**, gồm MP4 dựng thật, Unicode, nhiều cảnh, nhạc, alpha overlay, HTTP API, state machine, retry, locked edits, restart, nguồn và giao thức Ollama giả lập. `pip check` không báo xung đột. Backend đã khởi động thật và trả HTTP 200 cho health/docs/OpenAPI.

Không đưa kết quả giả lập AI thành bằng chứng mô hình đã chạy thật. AI tests thay provider/inference ở ranh giới để không tải mô hình.

## Việc còn cần làm trên PC

1. Cài môi trường mới, chạy test và kiểm tra FFmpeg/font.
2. Cài Ollama + mô hình; kiểm tra CPU/Vulkan trên RX 5600 XT, đo thời gian/RAM thực.
3. Cài VieNeu/Whisper và tải model cache, kiểm tra phiên bản SDK tương thích và giọng tiếng Việt.
4. Chạy thực từ tên Dương Khai với nguồn thật, sửa vấn đề dữ liệu, phát âm, phụ đề hoặc lựa chọn ảnh nếu có.
5. Đánh giá nguồn, nhận diện ảnh và chất lượng câu chuyện. Trích dẫn khớp chuỗi chưa đủ chứng minh toàn bộ lời kể đúng.
6. Làm frontend sau khi backend/AI thực đã ổn. Chưa có frontend hoặc bộ tạo ảnh/video AI cục bộ trong repo.

## Giới hạn cần nhớ

- Mô hình AI chưa cài hoặc benchmark trên PC. Bộ nối SDK/API đã viết, chưa xác nhận chạy AI thật từ đầu đến cuối.
- Tìm web miễn phí có thể rate-limit hoặc không tìm đủ dữ liệu. Ảnh tìm thấy chưa có nghĩa đúng nhân vật hoặc được phép sử dụng.
- Hiệu ứng hiện tại là preset nhẹ và overlay cung cấp thêm; chưa tái tạo chính xác toàn bộ hiệu ứng video mẫu.
- Phụ đề theo đoạn TTS có ước lượng nội bộ; Whisper có thể sai tên riêng. Cho sửa tay.
- Giới hạn pipeline hiện tại 180 giây, mặc định đầu ra 1080x1920/30fps. Video mẫu gốc 960x1920 khoảng 60 giây.
- Chưa có đăng tự động, nhiều tài khoản, dịch vụ public hoặc dọn dữ liệu tự động.

Tài liệu sử dụng: `README.md`; chuyển máy/cài model: `docs/PC_SETUP.md`; quy tắc coding agent: `AGENTS.md`.
