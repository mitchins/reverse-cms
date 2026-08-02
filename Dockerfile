# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ghostscript \
        ocrmypdf \
        poppler-utils \
        qpdf \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$APP_GID" reversecrm \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home reversecrm

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8.3 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-cache \
    && rm -rf \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

RUN mkdir -p /data /inbox /work \
    && chown -R reversecrm:reversecrm /data /inbox /work /app

USER reversecrm
VOLUME ["/data", "/inbox"]
ENTRYPOINT ["reversecrm"]
CMD ["web"]
