FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are pre-installed in the base image.
# Install both Chromium (default) and Chrome (required for Allstate — Akamai's
# ak_bmsc cookie is fingerprint-bound to real Chrome; Chromium gets a bare page).
# Set BROWSER_CHANNEL=chrome at runtime to switch.
RUN playwright install chromium --with-deps && \
    playwright install chrome --with-deps

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
