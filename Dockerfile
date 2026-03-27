FROM python:3.11-slim AS builder

WORKDIR /app

# Cài đặt các công cụ build cần thiết
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements trước để tận dụng Docker layer cache
COPY requirements.txt .

# Cài đặt dependencies vào thư mục /install để copy sang stage cuối
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage cuối: runtime image nhỏ gọn
# ============================================================
FROM python:3.11-slim AS runtime

# Thiết lập biến môi trường Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Cài đặt libpq runtime (cần cho psycopg2/asyncpg kết nối PostgreSQL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Tạo user non-root để chạy ứng dụng (bảo mật)
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy installed packages từ builder stage
COPY --from=builder /install /usr/local

# Copy toàn bộ source code ứng dụng
COPY --chown=appuser:appgroup . .

# Chuyển sang user non-root
USER appuser

# Expose port ứng dụng FastAPI
EXPOSE 8000

# Health check để Docker/orchestrator biết app đã sẵn sàng
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Lệnh khởi động ứng dụng với uvicorn
# workers=1 phù hợp cho container environment (scaling bằng số container)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "--http", "httptools"]