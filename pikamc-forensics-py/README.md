# PikaMC Forensics Investigator 🕵️

Công cụ **phân tích gian lận Minecraft Server** ở chế độ **READ-ONLY TUYỆT ĐỐI**.

## Kiến trúc
- **Backend:** Python 3.11+ / FastAPI / SQLAlchemy 2.0 / SQLite (WAL mode)
- **AI:** Gemini / OpenAI / Ollama (bắt buộc append READ_ONLY_SYSTEM_PROMPT)
- **Frontend:** Single HTML file (Tailwind CDN + Chart.js, 3 tabs)

## Cài đặt

```bash
pip install -r requirements.txt
copy .env.example .env     # Windows
# linux: cp .env.example .env
```

Sửa `.env`: điền `PIKAMC_API_KEY`, `GEMINI_API_KEY` hoặc `OPENAI_API_KEY` (tùy chọn).

## Chạy

```bash
uvicorn app.main:app --reload
```

- Frontend: http://localhost:8000
- Health: http://localhost:8000/health
- Docs (Swagger): http://localhost:8000/docs

## Docker

```bash
docker build -t pikamc-forensics .
docker run -p 8000:8000 --env-file .env pikamc-forensics
```

## API chính

| Method | Endpoint | Mô tả |
|---|---|---|
| GET | `/health` | Trạng thái hệ thống |
| POST | `/api/investigate` | Điều tra 1 player |
| POST | `/api/scan/world` | Quét toàn bộ world |
| POST | `/api/ai/analyze` | Phân tích bằng AI |
| GET | `/api/stats/overview` | Thống kê tổng hợp |

## Ràng buộc READ-ONLY
1. Chỉ dùng HTTP GET đến PikaMC API
2. DELETE endpoint chỉ xóa bản ghi SQLite local
3. AI bị khóa ở chế độ phân tích - từ chối mọi yêu cầu ghi/sửa/ban

## Test

```bash
pytest tests/ -v
```
