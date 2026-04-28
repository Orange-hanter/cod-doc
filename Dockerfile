FROM python:3.12-slim

LABEL maintainer="COD-DOC" \
      description="Context Orchestrator for Documentation — autonomous agent"

# ── 1. System packages (cached until this list changes) ──────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 2. pip upgrade (separate layer — almost never invalidated) ────────────────
RUN pip install --no-cache-dir --upgrade pip

# ── 3. Python dependencies (cached until pyproject.toml changes) ─────────────
#    Extract deps via stdlib tomllib (Python 3.11+) — no stub needed.
COPY pyproject.toml ./
RUN python -c "import tomllib; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; open('/tmp/reqs.txt','w').write('\n'.join(deps))" \
    && pip install --no-cache-dir -r /tmp/reqs.txt \
    && rm /tmp/reqs.txt

# ── 4. Application source (invalidated on every code change) ─────────────────
#    pip install --no-deps registers entry-points without re-downloading deps.
COPY cod_doc/ ./cod_doc/
RUN pip install --no-cache-dir --no-deps .

# ── 5. Runtime directories & env defaults ────────────────────────────────────
RUN mkdir -p /data/cod-doc /projects

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

CMD ["cod-doc", "serve"]
