# Production Dockerfile for Barangay Management System (BMS)
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered streaming logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    BMS_HOST=0.0.0.0 \
    BMS_AUTO_OPEN_BROWSER=false \
    BMS_DATABASE_PATH=/data/bms.sqlite3

WORKDIR /app

# Create non-root runtime user and data directory
RUN useradd -u 1000 -m bmsuser && \
    mkdir -p /data /app/uploads && \
    chown -R bmsuser:bmsuser /data /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=bmsuser:bmsuser server.py config.py index.html sqlite-api.js logo.png compressed_b2f0e3ddc22210e534db1aade7c52180.png ./
COPY --chown=bmsuser:bmsuser assets/ ./assets/
COPY --chown=bmsuser:bmsuser css/ ./css/
COPY --chown=bmsuser:bmsuser js/ ./js/
COPY --chown=bmsuser:bmsuser data/ ./data/
COPY --chown=bmsuser:bmsuser uploads/ ./uploads/

# Mount point for persistent database disk
VOLUME ["/data"]

# Switch to non-root user
USER bmsuser

# Expose default application port
EXPOSE 8000

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, os; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health')" || exit 1

# Production start command
CMD ["python", "server.py"]
