# Tu Tiên Shorts — backend cục bộ

Backend Python/FastAPI tạo short từ tên nhân vật hoặc tài nguyên nhập thủ công. Không tích hợp API AI trả phí. Phần dựng dùng FFmpeg, không cần GPU NVIDIA. Mặc định chỉ mở ở `127.0.0.1`.

## Chạy trên Windows

Cần Python **3.11 trở lên** và font Arial hoặc DejaVu Sans. Thư viện `imageio-ffmpeg` đi kèm FFmpeg có encoder `libx264`; cũng có thể chỉ định một FFmpeg đầy đủ trong cấu hình/PATH. Bản FFmpeg đi kèm CapCut có thể thiếu encoder cần thiết nên không nên dùng để xuất. Không phụ thuộc vào việc điều khiển giao diện CapCut.

```powershell
git clone https://github.com/phanan04/shortmaker.git C:\Game\shortmaker
cd C:\Game\shortmaker
.\scripts\setup.ps1
.\scripts\start.ps1
```

API docs: http://127.0.0.1:8765/docs. Kiểm tra môi trường: `GET /v1/capabilities`.

Chuyển sang PC: đọc [docs/PC_SETUP.md](docs/PC_SETUP.md). Trạng thái bàn giao và phần còn cần kiểm chứng nằm trong [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md). `AGENTS.md` hướng dẫn coding agent tiếp tục dự án ở bất kỳ đường dẫn checkout nào.

Môi trường `.venv` trong workspace hiện tại đã có thư viện backend và test. Có thể chạy trực tiếp:

```powershell
.\.venv\Scripts\python.exe -m shorts
```

`requirements.lock.txt` ghi các phiên bản backend/test đã kiểm chứng. Để tái lập môi trường: `python -m pip install -r requirements.lock.txt`, rồi `python -m pip install -e . --no-deps`. Các thư viện AI tùy chọn được cài riêng.

Sao chép `config.example.toml` thành `config.toml` để thay đổi cấu hình. Không chạy nhiều Uvicorn worker với cùng `data_dir`; khóa tiến trình sẽ từ chối tiến trình thứ hai. SQLite + một worker tuần tự phù hợp máy RAM 16 GB.

## Các bước pipeline

1. `research`: tìm nguồn bằng DDGS, đọc trang, xác định đúng nhân vật/tác phẩm bằng Ollama. Có thể nhập URL hoặc văn bản nguồn. Tái sử dụng hồ sơ nhân vật của công việc trước.
2. `plan`: viết một góc kể, chia đoạn, trích dẫn nguồn và đoạn bằng chứng. Kiểm tra ID nguồn và bằng chứng thực sự có trong nguồn. Tránh góc kể trùng trong lịch sử. Đây không phải bộ kiểm chứng sự thật hoàn hảo: người duyệt vẫn phải đối chiếu lời kể.
3. `assets`: ưu tiên ảnh trong thư viện đã xác nhận nhân vật/tác phẩm. Nếu chưa có, tìm và tải tối đa ba ảnh làm ứng viên, chọn một ảnh cho preview và đánh dấu chưa xác minh. Thư viện lưu nguồn và quyền sử dụng; chưa có bộ tạo ảnh AI cục bộ vì ưu tiên cấu hình nhẹ.
4. `speech`: tạo giọng từng đoạn với VieNeu-TTS CPU, áp dụng từ điển phát âm; đo và ghép âm thanh thực tế. Dịch vụ HTTP TTS nếu cấu hình chỉ được phép ở loopback. Mỗi lần inference SDK chạy subprocess để giải phóng RAM khi xong.
5. `captions`: faster-whisper CPU/int8 với timestamp từ âm thanh. Phụ đề nhận dạng cần kiểm tra tên riêng. Có chế độ nhẹ `precise_captions=false`: chia thời gian theo các đoạn TTS, cụm chữ bên trong được ước lượng và có cảnh báo. Với âm thanh nhập tay không có mốc đoạn thì phải dùng Whisper hoặc nhập phụ đề, không tự đoán toàn bộ thời gian.
6. `timeline`: ghép giọng, hình, phụ đề, nhạc và lớp phủ. Một ảnh xuyên suốt, nhiều ảnh chia đều, hoặc ảnh theo đoạn nếu có mốc giọng tương ứng. Thời lượng bám theo file giọng, tối đa 180 giây; không ép tốc độ đọc để đủ 60 giây.
7. `preview`: dựng bản rộng tối đa 540 px. Font Unicode được vẽ thành lớp PNG nên FFmpeg không cần libass. Có zoom chậm, thay đổi sắc độ/độ sáng nhẹ, hạt sáng và tùy chọn video overlay alpha. Hiệu ứng hào quang/vệt năng lượng phức tạp cần cung cấp overlay thích hợp.
8. `quality`: kiểm tra có hình và tiếng, độ dài, giải mã toàn bộ preview; liệt kê nguồn/ảnh/quyền/chênh lệch thời lượng cần duyệt.
9. `export`: sau duyệt, dựng đúng độ phân giải preset, kiểm tra giải mã/độ dài/kích thước; xuất MP4, SRT và JSON nguồn/ghi nhận duyệt. Không tự đăng mạng xã hội.

## Cài AI miễn phí (tùy chọn)

Các mô hình **chưa được tải sẵn**. Backend dựng thủ công không cần Ollama, VieNeu hoặc Whisper. Không tự tải hàng GB khi khởi động.

**Viết nội dung:** cài Ollama từ https://ollama.com, sau đó:

```powershell
ollama pull qwen3.5:4b
```

Địa chỉ mặc định `http://127.0.0.1:11434`. Mô hình được yêu cầu giải phóng bộ nhớ sau khi trả kết quả (`keep_alive=0`). RX 5600 XT cần thử backend Vulkan; nếu không tương thích thì dùng CPU. Không giả định ROCm/CUDA hoạt động trên card này.

**Giọng và phụ đề:**

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[speech,captions]'
```

VieNeu dùng bản SDK mã nguồn mở v3 Turbo, backend ONNX/fp32 trên CPU; không dùng dịch vụ VieNeu v4 trả phí. Các phiên bản mô hình có thể thay đổi API; xem liên kết tài liệu bên dưới. faster-whisper dùng model `small` mặc định.

Cho phép tải mô hình lần đầu bằng `allow_model_downloads=true` trong `config.toml`, khởi động lại, chạy một công việc. Mô hình lưu tại `data/models`. Có thể đổi lại thành `false` sau đó. SDK giọng/Whisper chạy trong subprocess, không giữ đồng thời với mô hình viết. Ollama có kho mô hình riêng do Ollama quản lý.

Không có dịch vụ cloud miễn phí được dùng làm fallback ngầm. Thiếu mô hình, dịch vụ không chạy hoặc thiếu tư liệu → `needs_input`, thông báo cách xử lý. Tìm kiếm web vẫn cần Internet, có thể bị giới hạn. Sửa tay không cần dịch vụ tìm kiếm.

## API

| Method | Đường dẫn | Chức năng |
|---|---|---|
| GET | `/health` | Trạng thái backend/worker |
| GET | `/v1/capabilities` | FFmpeg, Ollama, thư viện AI, cấu hình tải mô hình |
| POST | `/v1/jobs` | Tạo một công việc |
| POST | `/v1/jobs/batch` | Tạo tối đa 50 công việc; chạy tuần tự |
| GET | `/v1/jobs?limit=50&offset=0` | Danh sách gọn |
| GET | `/v1/jobs/{id}` | Toàn bộ trạng thái, kết quả và lỗi từng bước |
| PATCH | `/v1/jobs/{id}` | Sửa các trường trong JobCreate, tự đánh dấu kết quả liên quan hết hạn |
| POST | `/v1/jobs/{id}/run` | Chạy/tiếp tục/thử lại từ checkpoint |
| POST | `/v1/jobs/{id}/pause` | Yêu cầu dừng, giữ kết quả đã hoàn thành |
| POST | `/v1/jobs/{id}/cancel` | Hủy, vẫn giữ tư liệu/kết quả; có thể chạy lại |
| PUT | `/v1/jobs/{id}/stages/{stage}` | Nhập/sửa kết quả và khóa tay |
| POST | `/v1/jobs/{id}/stages/{stage}/unlock` | Bỏ kết quả bước đó, cho phép tự sinh lại |
| POST | `/v1/jobs/{id}/approve` | Xác nhận bản hiện tại và xếp hàng xuất |
| GET | `/v1/jobs/{id}/events?after=0` | Log sự kiện tăng dần, dùng cursor để polling |
| GET | `/v1/jobs/{id}/files/{filename}` | `preview.mp4`, `final.mp4`, `subtitles.srt`, `metadata.json` |
| POST | `/v1/assets?kind=image` | Upload multipart `file`; kind: image/audio/overlay |
| GET | `/v1/assets?character=...` | Thư viện tài nguyên |
| GET/PATCH | `/v1/assets/{id}` | Xem/sửa tên, nhân vật, tác phẩm, URL nguồn, quyền, xác nhận nhân vật |
| GET | `/v1/assets/{id}/file` | Đọc file tài nguyên |

Chi tiết schema trong `/docs` và `/openapi.json`. Dùng `Authorization: Bearer ...` nếu cấu hình `api_token`. Không gửi khóa API AI. API dành cho ứng dụng tin cậy chạy trên máy, không phải dịch vụ public nhiều người dùng. Truy cập LAN yêu cầu token tối thiểu 24 ký tự và tự cấu hình firewall/TLS phù hợp; bản đầu chưa có tài khoản/phân quyền nhiều người dùng.

### Tự động từ tên

```powershell
$body = @{
  character = 'Dương Khai'
  work = 'Võ Luyện Đỉnh Phong'
  mode = 'auto'
  target_seconds = 60
  precise_captions = $true
} | ConvertTo-Json
$job = Invoke-RestMethod http://127.0.0.1:8765/v1/jobs -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Invoke-RestMethod "http://127.0.0.1:8765/v1/jobs/$($job.id)"
```

Chỉ `character` là bắt buộc. `work` giúp tránh nhầm nhân vật. Nguồn thêm tay có dạng:

```json
{"id":"s1","title":"Nguồn đã kiểm tra","url":"https://example.com/page","text":"Đoạn nguồn nếu muốn nhập trực tiếp, để trống thì tool đọc URL.","period":"Giai đoạn đầu truyện"}
```

### Chỉnh tay

Đặt `auto_start=false` để chuẩn bị tài nguyên trước. `mode=manual` không gọi tìm kiếm, LLM, giọng hay Whisper; phải cung cấp các kết quả cần thiết. `hybrid` cho phép override bước bất kỳ, tự xử lý phần còn thiếu. `auto` cũng giữ khả năng chỉnh tay.

Upload ảnh/âm thanh bằng `/v1/assets`, lấy `id`. PUT các bước với body `{ "data": {...}, "locked": true }`:

**plan** (có thể cung cấp trước research; backend ghi rõ đây là nội dung người dùng, không giả làm nguồn web):

```json
{
  "data": {
    "character":"Dương Khai", "work":"Võ Luyện Đỉnh Phong",
    "title":"Tiêu đề", "angle":"Xuất thân",
    "sections":[{"text":"Kịch bản của bạn...","source_ids":[],"evidence":"","image_query":""}],
    "pronunciation":{"Tên hiển thị":"Cách đọc"}, "warnings":[]
  },
  "locked":true
}
```

**assets**: `{"selected":[{"asset_id":"ID_ẢNH"}],"music_id":null,"overlay_id":null}`. Nhiều ảnh không đặt `section` sẽ chia đều thời lượng. Nếu đặt `section`, phải có đúng một ảnh cho mỗi đoạn và speech.parts tương ứng.

**speech**: `{"asset_id":"ID_GIỌNG","duration":60,"parts":[]}`. Backend đo lại duration từ file, không tin giá trị gửi lên. Nếu biết mốc đoạn, `parts` gồm `{"section":0,"start":0,"end":10}`.

**captions**: `{"method":"manual","cues":[{"start":0,"end":2.5,"text":"Phụ đề tiếng Việt"}]}`. Thời gian tính bằng giây, không chồng lấn; tối đa hai dòng, 44 ký tự/dòng. Không phải file SRT trong body.

Sau đó `POST /run`. Khi `needs_review`, tải preview, đối chiếu nguồn/ảnh rồi gửi:

```json
{"content_verified":true,"identity_verified":true,"rights_confirmed":true,"note":"Đã kiểm tra"}
```

Tới `/approve`. Những xác nhận này là của người dùng, không phải kết luận do AI tự cấp.

## Checkpoint và sửa đổi

- Đổi preset → dựng lại timeline/preview/final, giữ giọng và phụ đề.
- Đổi ảnh → giữ giọng/phụ đề, dựng lại phần hình.
- Đổi kịch bản → giọng/phụ đề/phần dựng phía sau hết hạn.
- Kết quả khóa tay phía sau **không bị xóa**: chuyển `stale`, cần nhập lại bản đã xác nhận hoặc unlock trước khi tiếp tục.
- Mọi sửa đổi liên quan hủy ghi nhận duyệt cũ; không thể tải final cũ dưới revision mới.
- Không cho sửa khi `queued`/`running`: pause và đợi `paused` trước.
- Khởi động lại: công việc đang chạy chuyển `paused`, công việc đang đợi vẫn nằm trong hàng đợi.
- Retry lỗi dịch vụ tối đa hai lần mỗi lần chạy; thiếu tư liệu/mô hình không lặp vô hạn. Pause/cancel được kiểm tra giữa các thao tác; FFmpeg/inference bị dừng, lời gọi mạng đang chờ có thể cần hết timeout (mặc định tối đa 180s cho AI).
- `require_review=false` chỉ bỏ bước duyệt nếu không có cảnh báo cần xử lý; ảnh/quyền chưa xác nhận vẫn yêu cầu duyệt.

## Dữ liệu và kiểm thử

SQLite ở `data/pipeline.sqlite3`; file upload ở `data/assets`; kết quả JSON, log và MP4 ở `data/jobs/{id}`. Sao lưu toàn bộ thư mục `data` khi backend đã dừng. Chưa có cơ chế xóa tự động, tránh mất bản sửa tay; thư mục có thể tăng theo số video.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=data/test-run-local
```

Đường dẫn basetemp phải là thư mục chỉ dành cho test vì pytest dọn nội dung thư mục đó. Bộ test kiểm tra API, xác thực, hàng đợi, checkpoint, bảo vệ sửa tay, nguồn, hủy/khôi phục và dựng MP4 thật với FFmpeg. Phần test điều phối AI dùng bộ giả lập ở ranh giới provider để không tải mô hình. Không coi đó là benchmark hoặc xác nhận chất lượng mô hình thực trên PC.

Tài liệu upstream: [Ollama](https://docs.ollama.com/api/chat), [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [DDGS](https://github.com/deedy5/ddgs), [FFmpeg](https://ffmpeg.org/documentation.html).
