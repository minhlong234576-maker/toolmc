# PikaMC Forensics Investigator
FROM python:3.11-slim

WORKDIR /app

# Cài dependencies trước để tận dụng Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy mã nguồn
COPY app/ ./app/
COPY data/ ./data/

# Cổng mặc định
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
