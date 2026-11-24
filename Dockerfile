# Sniff back end (the API). The model servers are separate containers, see docker-compose.yml.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /usr/local/bin/uv
# OpenCV (pulled in by the OCR library) needs these two system libraries
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/sniff ./sniff
RUN uv sync --frozen --no-dev
ENV PATH="/app/backend/.venv/bin:$PATH" SNIFF_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "sniff.api:app", "--host", "0.0.0.0", "--port", "8000"]
