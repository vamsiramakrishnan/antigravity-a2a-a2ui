# Cloud Run image for the shared A2A/A2UI gateway.
#
# The gateway is stateless and holds NO broad storage credential. It runs as its
# own service account whose only privileges are: read workspace metadata
# (Firestore) and call the credential broker / materialize sessions. Storage
# access happens under per-request, workspace-scoped credentials — see
# docs/architecture.md.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install '.[gcp]'

# Run as non-root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080
CMD ["python", "-m", "a2a_workspace"]
