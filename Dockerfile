FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot files
COPY moviezwap_telegram_bot.py .
COPY lang.py . 2>/dev/null || true

# Create necessary directories
RUN mkdir -p downloads logs

# Run bot
CMD ["python", "moviezwap_telegram_bot.py"]
