FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# System deps for cryptography / lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
RUN pip install --no-cache-dir .

COPY app /app/app

# Run as non-root
RUN useradd -m smms && chown -R smms /app
USER smms

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
