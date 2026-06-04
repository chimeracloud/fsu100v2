# FSU100V2 — Horse Racing Lay Engine (pure decision engine)
# Phase 1 (shell). Standard Cloud Run image, Python 3.12 slim.
#
# NO betfairlightweight. NO Betfair credentials. NO Secret Manager reads.
# This engine consumes from FSU1B and emits instructions to LBCF.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY main.py ./
COPY core/ ./core/
COPY services/ ./services/
COPY models/ ./models/
COPY plugins/ ./plugins/
COPY resources/ ./resources/

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
