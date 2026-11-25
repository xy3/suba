FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch keeps the image ~1.5 GB instead of ~6 GB.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir openai-whisper

COPY pyproject.toml README.md LICENSE main.py /src/
RUN pip install --no-cache-dir /src && rm -rf /src

# Whisper models are cached here; mount a volume to keep them between runs.
ENV XDG_CACHE_HOME=/cache
WORKDIR /media
ENTRYPOINT ["suba"]
