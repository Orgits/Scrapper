FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .
RUN mkdir -p data/json data/csv logs

CMD ["celery", "-A", "workers.celery_worker", "worker", "--loglevel=info"]
