FROM python:3.12-slim

LABEL maintainer="COD-DOC" \
      description="Context Orchestrator for Documentation — autonomous agent"

# Зависимости системы
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка зависимостей Python
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .

# Копирование кода
COPY cod_doc/ ./cod_doc/
COPY templates/ ./templates/
COPY hooks/ ./hooks/

# Директория конфига
ENV COD_DOC_HOME=/data/cod-doc
RUN mkdir -p /data/cod-doc /projects

# Переменные окружения (переопределяются в docker-compose или при запуске)
ENV COD_DOC_API_KEY=""
ENV COD_DOC_MODEL="anthropic/claude-sonnet-4-6"
ENV COD_DOC_BASE_URL="https://openrouter.ai/api/v1"
ENV COD_DOC_AUTO_COMMIT="false"
ENV COD_DOC_AGENT_INTERVAL="60"
ENV COD_DOC_API_HOST="0.0.0.0"
ENV COD_DOC_API_PORT="8765"

EXPOSE 8765

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8765/api/health || exit 1

# Точка входа: REST API сервер
CMD ["cod-doc", "serve"]
