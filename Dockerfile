# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Note: defer spaCy model download to container start to speed up image builds

# Copy application code
COPY . .

# Ensure static directory exists and has proper permissions
RUN mkdir -p /app/static/js && \
    chmod -R 755 /app/static

# Create resources directory with proper permissions
RUN mkdir -p /app/resources && \
    chmod 755 /app/resources

# Create a non-root user for security
# Create non-root user but do not switch; compose may run container as host UID
RUN useradd --create-home --shell /bin/bash app || true
RUN chown -R app:app /app || true

# Copy entrypoint script
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Expose port
EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# Command to run the application
CMD ["python", "app.py"]
