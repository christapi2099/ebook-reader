FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04 AS builder

RUN apt-get update && apt-get install -y \
    python3.12 python3.12-dev python3-pip \
    build-essential cmake gcc g++ git wget curl \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt && \
    python -m spacy download en_core_web_sm

# Production stage: CUDA 12.4 runtime for GPU acceleration
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    python3.12 python3.12-venv python3-pip \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3

WORKDIR /app

RUN useradd -m -u 1000 -s /bin/bash appuser && \
    mkdir -p /app/uploads /data && \
    chown -R appuser:appuser /app /data

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/ebook_reader.db \
    UVICORN_WS=websockets

COPY --chown=appuser:appuser . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fs http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
