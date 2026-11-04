# ==============================================================================
# RENTKOM SMART ENTERPRISE: BUSINESS CORE DOCKERFILE
# ==============================================================================
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for PostgreSQL client and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root unprivileged runtime user
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

COPY core/ /app/core/
COPY tools/ /app/tools/
COPY SMART_ENTERPRISE/database/ /app/SMART_ENTERPRISE/database/

RUN chown -R appuser:appgroup /app

USER appuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/ready || exit 1

CMD ["uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8000"]
