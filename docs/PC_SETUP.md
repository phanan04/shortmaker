# Tiếp tục dự án trên PC

PC dự kiến: i3-12100F, RX 5600 XT, RAM 16 GB. Laptop chứa mã nguồn ban đầu; không cần sao chép môi trường Python của laptop.

## 1. Lấy mã và mở dự án

Cài Git và Python 3.12 bản 64-bit (có pip), rồi chạy PowerShell:

```powershell
git clone https://github.com/phanan04/shortmaker.git C:\Game\shortmaker
cd C:\Game\shortmaker
.\scripts\setup.ps1 -Python python
.\scripts\start.ps1
```

Nếu máy có nhiều Python, có thể dùng `py -3.12 -m venv .venv` rồi ` .\.venv\Scripts\python.exe -m pip install -e '.[test]'` và sao chép `config.example.toml` thành `config.toml`.

Nếu PowerShell chặn script do ExecutionPolicy, mở script để xem rồi gọi cho đúng phiên chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Không thay đổi ExecutionPolicy toàn máy. Mở http://127.0.0.1:8765/docs và kiểm tra `GET /v1/capabilities`.

FFmpeg được cài qua thư viện `imageio-ffmpeg`. Không cần cài CapCut. Cần font Arial hoặc DejaVu Sans để xuất phụ đề có dấu.

## 2. Để Codex hiểu và tiếp tục

Mở **thư mục repo** `C:\Game\shortmaker` trong Codex. Coding agent sẽ có `AGENTS.md`; trạng thái được ghi trong `docs/PROJECT_STATUS.md`. Lịch sử chat ở laptop không tự đi theo Git, nên các quyết định quan trọng đã được ghi vào tài liệu này.

Có thể gửi câu lệnh:

> Đọc AGENTS.md và docs/PROJECT_STATUS.md. Tiếp tục backend shortmaker trên PC i3-12100F, RX 5600 XT, RAM 16 GB. Giữ giải pháp miễn phí và chỉnh thủ công. Kiểm tra môi trường, cài các thành phần AI cục bộ cần thiết, rồi thử pipeline Dương Khai từ đầu đến preview bằng nguồn thật. Không dùng API trả phí, không coi test giả lập là đã chạy AI thật.

## 3. Bật tự động viết bằng Ollama

Cài Ollama từ https://ollama.com, khởi động Ollama và tải:

```powershell
ollama pull qwen3.5:4b
```

Endpoint mặc định `http://127.0.0.1:11434`. Trước tiên kiểm tra chạy được trên CPU; sau đó mới thử tăng tốc Vulkan cho RX 5600 XT nếu driver/backend tương thích. Không cài CUDA cho card AMD này. Chưa có benchmark của dự án trên PC.

## 4. Bật giọng và phụ đề cục bộ

Dừng backend trước khi cài/thay đổi thư viện:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[speech,captions]'
```

Trong `config.toml`, đặt `allow_model_downloads = true` để cho phép tải mô hình lần đầu. Mặc định là `false` để không tự tải file lớn. Khởi động lại backend. Có thể đặt lại `false` khi đã tải đủ và kiểm tra cache.

Giọng dùng VieNeu bản mã nguồn mở chạy CPU/ONNX/fp32; phụ đề dùng faster-whisper CPU/int8. Model cache ở `data/models`. Ollama quản lý model cache riêng. Backend chạy tuần tự và không cần giữ tất cả mô hình trong RAM cùng lúc.

Nếu thư viện AI xung đột hoặc mô hình không chạy, xem `data/jobs/{id}/speech.log` hoặc `captions.log`. Có thể nhập file giọng và phụ đề thủ công để tiếp tục dựng. Không có fallback trả phí.

## 5. Kiểm thử trước khi phát triển tiếp

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=data/test-run-pc
.\.venv\Scripts\python.exe -m pip check
```

`data/test-run-pc` chỉ dành cho test, pytest sẽ dọn nội dung thư mục này. Test điều phối AI giả lập không kiểm chứng chất lượng giọng/kịch bản thực. Sau khi cài model cần chạy thêm một công việc thực tế và đối chiếu nguồn/ảnh/phụ đề.

## Những thứ Git không mang theo

- Video mẫu MP4 và contact sheet trên laptop.
- `.venv`, mô hình AI, SQLite, job, tài nguyên upload và video đã render.
- `config.toml`, `.env`, token và khóa riêng.

Nếu cần chuyển cả công việc đang có: dừng backend ở cả hai máy rồi sao chép toàn bộ `data` bằng USB hoặc kênh riêng. Cài lại `.venv` trên PC; không sao chép nó từ laptop. Không đẩy thư mục `data` lên GitHub.

Khi làm song song, commit/push trên máy đang làm; ở máy còn lại dùng `git pull --ff-only` khi working tree sạch. Không dùng force push để đồng bộ hai máy.
