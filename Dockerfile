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
COPY requirements-docker.txt requirements-dev.txt ./

RUN pip install --no-cache-dir -r requirements-docker.txt -r requirements-dev.txt

# Copy the entire project
COPY . .

# Run tests by default
CMD ["pytest", "-q", "tests/"]