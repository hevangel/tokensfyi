# Pinned runtime image; update intentionally through a reviewed dependency change.
FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.9.28 \
    && uv sync --frozen --no-dev --no-install-project

COPY config ./config
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY manage.py ./manage.py

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
