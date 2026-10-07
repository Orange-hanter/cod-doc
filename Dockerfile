FROM python:3.14-slim

LABEL maintainer="COD-DOC" \
      description="Context Orchestrator for Documentation — autonomous agent"

# ── 1. System packages (cached until this list changes) ──────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 2. uv (pinned binary from the official image; Dependabot bumps the tag) ──
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# ── 3. Python dependencies (cached until pyproject.toml / uv.lock change) ────
#    Ровно версии из uv.lock — те же, на которых зелёный CI. Раньше здесь
#    ставились нижние границы из pyproject через pip, и опубликованный образ
#    получал то, что свежее всего в день сборки, а не то, что проверено.
#    `--frozen` падает на рассинхроне лока с pyproject, а не пересчитывает;
#    экспорт идёт с хэшами, так что подмена пакета в индексе роняет сборку.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --quiet -o /tmp/reqs.txt \
    && uv pip install --system --no-cache -r /tmp/reqs.txt \
    && rm /tmp/reqs.txt

# ── 4. Application source (invalidated on every code change) ─────────────────
#    --no-deps registers entry-points without re-downloading deps.
COPY cod_doc/ ./cod_doc/
COPY alembic.ini ./
COPY entrypoint.sh ./
# Версия пакета выводится из git (setuptools-scm), но `.git/` исключён из
# контекста сборки (.dockerignore) — внутри образа истории нет и быть не
# должно. Поэтому ревизию передаём аргументом: CD подставляет тег, локальная
# сборка может не подставлять ничего и получит `0.0.0+nogit` из
# `fallback_version` — честный признак «эту установку не опознать».
ARG COD_DOC_VERSION=""
RUN chmod +x ./entrypoint.sh \
    && if [ -n "$COD_DOC_VERSION" ]; then \
         export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_COD_DOC="$COD_DOC_VERSION"; \
       fi \
    && uv pip install --system --no-cache --no-deps .

# ── 5. Runtime directories & env defaults ────────────────────────────────────
RUN mkdir -p /data/cod-doc /projects

# COD_DOC_API_HOST=0.0.0.0 — осознанный opt-out (SYM-003): в контейнере
# loopback-bind сделал бы сервис недоступным снаружи; публикацию порта
# ограничивает оператор (`-p 127.0.0.1:8765:8765`). Вне контейнера
# дефолт — 127.0.0.1.
ENV COD_DOC_HOME=/data/cod-doc \
    COD_DOC_API_KEY="" \
    COD_DOC_MODEL="anthropic/claude-sonnet-4-6" \
    COD_DOC_BASE_URL="https://openrouter.ai/api/v1" \
    COD_DOC_AUTO_COMMIT="false" \
    COD_DOC_AGENT_INTERVAL="60" \
    COD_DOC_API_HOST="0.0.0.0" \
    COD_DOC_API_PORT="8765"

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -f http://localhost:${COD_DOC_API_PORT}/api/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
