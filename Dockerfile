# JARVIS CI/Test Docker image
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    libportaudio2 \
    portaudio19-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements/ /app/requirements/
RUN pip install --no-cache-dir -r /app/requirements/requirements-docker.txt -r /app/requirements/requirements-dev.txt

# Copy the entire project
COPY . .

# Run tests by default
CMD ["pytest", "-q", "tests/"]