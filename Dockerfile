# Production-oriented image for local demonstration of the SlotBook API.
# It is not a claim of production deployment or a live public service.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root runtime user; the image embeds no secrets.
RUN groupadd --system slotbook \
    && useradd --system --gid slotbook --create-home slotbook \
    && chown -R slotbook:slotbook /app
USER slotbook

EXPOSE 8000

# Gunicorn serves the Django WSGI application; configuration comes from
# environment variables (DJANGO_SECRET_KEY, DB_*) provided at runtime.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "config.wsgi:application"]