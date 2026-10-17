FROM docker.io/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

# Runtime dependencies for voice playback
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy the pinned requirements first to leverage Docker cache
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# requirements.txt adds the unpinned packages (yt-dlp) on top of the lock file,
# so every build takes their latest release. CI passes a new YTDLP_CACHE_BUST
# on each run, which skips the cached layer.
COPY requirements.txt .
ARG YTDLP_CACHE_BUST
RUN echo "yt-dlp build ${YTDLP_CACHE_BUST}" \
    && pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the rest of the application
COPY . .

RUN mkdir -p /app/data /app/.private/temp_audio \
    && useradd --create-home --uid 10001 bot \
    && chown -R bot:bot /app

USER bot

CMD ["python", "-u", "main.py"]
