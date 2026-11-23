# One image for the whole app: backend (FastAPI) + frontend (static files it serves at /).
FROM python:3.13-slim

# XGBoost needs the OpenMP runtime, which the slim image does not include.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
WORKDIR /app

COPY backend/requirements-render.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-render.txt

COPY backend backend
COPY frontend frontend

# Build outage.db and the evaluation cache into the image, so start-up stays fast and light.
WORKDIR /app/backend
RUN python prewarm.py

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
