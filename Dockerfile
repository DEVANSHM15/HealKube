# HealKube Autonomous Controller Dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY experiments_results.csv /app/experiments_results.csv
COPY healkube/ /app/healkube/

# Run as non-root user for security best practices
RUN useradd -u 10001 -m healkube && chown -R healkube:healkube /app
USER healkube

EXPOSE 8080

ENTRYPOINT ["python", "-m", "healkube.controller.main"]
