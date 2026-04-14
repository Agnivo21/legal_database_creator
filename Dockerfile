# ─────────────────────────────────────────────
# Stage 1 — Base image locked to Python 3.10.11
# ─────────────────────────────────────────────
FROM python:3.10.11-slim

# Keeps Python from writing .pyc files and enables unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ─────────────────────────────────────────────
# System dependencies
# These match your packages.txt exactly:
#   libgl1        → OpenCV headless requirement
#   tesseract-ocr → pytesseract backend
#   poppler-utils → pdf2image backend
# ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    tesseract-ocr \
    poppler-utils \
    # Needed by opencv-python-headless on slim images
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────
# Working directory
# ─────────────────────────────────────────────
WORKDIR /app

# ─────────────────────────────────────────────
# Install Python dependencies
# Copy requirements first so Docker layer-caches
# the pip install step (only re-runs if requirements.txt changes)
# ─────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────
# Copy application source files
# credentials.json is NOT copied here — it is
# injected at runtime via Render Secret Files
# ─────────────────────────────────────────────
COPY app_modified.py .
COPY extract_case_metadata.py .

# ─────────────────────────────────────────────
# Streamlit config — disables the telemetry
# prompt and binds to 0.0.0.0 for Render
# ─────────────────────────────────────────────
RUN mkdir -p /root/.streamlit
COPY .streamlit/config.toml /root/.streamlit/config.toml

# Render sets $PORT dynamically; default 8501 for local dev
EXPOSE 8501

# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────
CMD ["streamlit", "run", "app_modified.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]