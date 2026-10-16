# One image for the backend, the sandbox and the scripts; docker-compose.yml picks the command.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# LightGBM needs the OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY backend ./backend
COPY sandbox ./sandbox
COPY data ./data
COPY scripts ./scripts
COPY redteam ./redteam

# Generate the synthetic data and train Fraud Sentinel and the credit model into the image (deterministic,
# about 20 seconds). Without the credit model, credit profiles and loan matching would be unavailable.
RUN python -m data.generate_shops > /dev/null && python scripts/train_models.py && python scripts/train_credit.py
