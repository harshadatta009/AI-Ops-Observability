# AI Incident Service

FastAPI service for AI-assisted incident analysis on top of Prometheus, Loki, Grafana alerts, Groq, and SMTP.

It receives Grafana webhook alerts, correlates metrics and logs, generates an RCA-style report with Groq, sends a professional HTML email, and exposes PDF report endpoints for 24-hour operational reports.

## Features

- Grafana webhook receiver: `POST /alert`
- Prometheus metric collection
- Loki error log collection
- Groq LLM incident analysis
- HTML incident email with plain-text fallback
- PDF report generation:
  - `GET /reports/consolidated-rca/pdf`
  - `GET /reports/daily-health/pdf`
  - `GET /reports/db-alerts/pdf`
- Local `.env` configuration via `python-dotenv`

## Architecture

The service is split by responsibility:

- `app.py` - FastAPI routing and dependency wiring
- `ai_incident_service/config.py` - environment-based settings
- `ai_incident_service/grafana.py` - Grafana alert parsing
- `ai_incident_service/observability.py` - Prometheus and Loki clients/collectors
- `ai_incident_service/ai_reports.py` - Groq report generation
- `ai_incident_service/emailer.py` and `email_templates.py` - SMTP delivery and HTML email rendering
- `ai_incident_service/pdf_report.py` - branded PDF generation
- `assets/varsapradaya-icon.webp` - PDF branding asset

## Setup

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Update `.env` with real values:

```env
PROMETHEUS_URL=http://10.100.0.142:9090
LOKI_URL=http://10.100.0.142:3100

GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.1-8b-instant

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your_smtp_user
SMTP_PASSWORD=your_smtp_password
SMTP_FROM=alerts@example.com
SMTP_TO=team@example.com
SMTP_USE_TLS=true

LOOKBACK_MINUTES=60
MAX_LOG_LINES=50
MAX_LOG_CHARS=3000
```

Do not commit `.env`. It is ignored by Git.

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the service:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

## Grafana Contact Point

Create a Grafana contact point:

```text
Type: Webhook
URL: http://ai-incident-service:8000/alert
Method: POST
```

If Grafana is outside the Docker network, use:

```text
http://<host-ip>:8000/alert
```

## PDF Reports

Open these URLs in a browser or call them with `curl`:

```text
http://127.0.0.1:8000/reports/consolidated-rca/pdf
http://127.0.0.1:8000/reports/daily-health/pdf
http://127.0.0.1:8000/reports/db-alerts/pdf
```

Each endpoint collects last-24-hour metrics/logs, asks Groq to generate a concise report, and returns a downloadable PDF.

## Docker Build

```bash
docker build -t ai-incident-service:latest .
```

## Docker Swarm Deploy

Export required environment variables before deploying:

```bash
export GROQ_API_KEY="your_groq_api_key"
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USER="your_smtp_user"
export SMTP_PASSWORD="your_smtp_password"
export SMTP_FROM="alerts@example.com"
export SMTP_TO="team@example.com"
```

Deploy:

```bash
docker stack deploy -c docker-swarm.yaml ai-observability
```

## Security Notes

- Secrets are loaded from `.env` or environment variables.
- Real API keys and SMTP passwords must not be committed.
- Rotate any credentials that were previously committed.
- Restrict access to `/alert` and `/reports/*` at the network or reverse-proxy layer before exposing this service outside a trusted network.
