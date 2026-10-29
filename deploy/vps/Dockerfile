FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/hf \
    TMPDIR=/cache/tmp \
    PATH=/home/quenya/.local/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /bin/bash quenya \
    && mkdir -p /app /cache/hf /cache/tmp \
    && chown -R quenya:quenya /app /cache

WORKDIR /app
COPY backend/ /app/backend/

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "/app/backend[embed]" \
        "curl-cffi==0.7.4"

USER quenya
WORKDIR /app/backend

RUN curl https://cursor.com/install -fsS | bash \
    && cursor-agent --version

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
