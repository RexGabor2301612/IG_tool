# Unified Instagram + Facebook Scraper & Analytics

A single Flask dashboard for extracting visible Instagram and Facebook metrics into Excel, with live logs, manual login/verification, and a strict GO gate before collection.

## What This App Does

- Opens a real browser session with Playwright.
- Pauses for manual login or verification (no CAPTCHA bypass).
- Requires a GO signal before scraping begins.
- Streams status + logs to the dashboard in real time.
- Exports the collected metrics to Excel.

## Requirements

- Python 3.10+
- Playwright browsers installed (run once):

```bash
python -m playwright install
```

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000

## API Overview

Instagram routes use `/api/*` and Facebook routes use `/facebook/api/*`.

- `POST /api/validate`
- `POST /api/start`
- `GET /api/status`
- `POST /api/go`
- `POST /api/cancel`
- `POST /api/clear-logs`
- `POST /api/focus-browser`
- `GET /api/download`
- `WS /ws/dashboard`

Facebook mirrors the same endpoints under `/facebook`.

## Project Structure

```
app.py
core/
  etl/
  logging/
  platforms/
  session/
  state/
static/
  css/
  js/
templates/
  dashboard.html
```

## Notes

- The scraper only uses visible data. Private or hidden content is not accessed.
- If verification appears, complete it manually and then click GO.
