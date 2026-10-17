# FlowTrace: the API and the built frontend in one image (one URL). The graph is external (FalkorDB Cloud or the
# compose service); in DEMO_MODE the API loads the demo history into it when it is empty.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# LightGBM needs OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY config.yaml .
COPY data/samples/ data/samples/
COPY data/demo/ data/demo/
# The demo bundle stands in for the full data: the scoring history of the samples' accounts and the model.
COPY data/demo/processed/ data/processed/
COPY data/demo/model/ data/models/
COPY data/processed/fx_rates.json data/processed/overview.json data/processed/signal_rates.json data/processed/
COPY data/models/*.json data/models/
COPY backend/flowtrace/ backend/flowtrace/
COPY --from=web /web/dist frontend/dist
WORKDIR /app/backend
EXPOSE 8080
# One process: uploads, cases and investigation jobs live in its memory (and data/runs). Forwarded headers are not
# trusted (no --proxy-headers): on Fly the visitor's address comes from Fly-Client-IP, which the proxy sets itself.
CMD ["uvicorn", "flowtrace.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
