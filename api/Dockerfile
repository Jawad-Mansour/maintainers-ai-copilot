FROM python:3.12-slim

WORKDIR /app

# gcc + libpq-dev: needed for psycopg2 (alembic sync driver)
# curl: used by docker-compose healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
