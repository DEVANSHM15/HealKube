# HealKube Demo Application Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing .pyc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY healkube/ /app/healkube/

EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "healkube.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
