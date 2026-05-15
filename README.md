# AgentFlow Automation Platform

Production-grade multi-tenant workflow automation platform built with Flask, PostgreSQL, Redis, Celery, and Razorpay billing in INR.

## What This Build Includes

- Reports and analytics dashboard with date-range filtering and CSV/PDF exports
- Usage and quota monitoring with plan-aware limits
- Organization audit log with search, filters, pagination, and CSV export
- Full platform admin panel:
  - admin dashboard
  - user management (ban, unban, force verify, impersonate, reset link)
  - organization management (plan change, suspend/unsuspend, soft delete cascade)
  - feature flags management (toggle, rollout, org targeting)
  - billing analytics and MRR insights
  - live system observability page
- Public support and legal pages:
  - Help Center
  - Support ticket flow
  - Platform status page
  - Privacy / Terms / Cookies / GDPR pages
- Error pages for 403, 404, 429, 500
- Production startup hardening:
  - dotenv early load
  - required env validation hints
  - idempotent startup seeding
  - redis startup timestamp for uptime metrics

## Core Stack

- Python 3.11+
- Flask
- PostgreSQL
- Redis
- Celery
- SQLAlchemy
- Flask-WTF
- Razorpay
- ReportLab / WeasyPrint
- Bootstrap 5 + Chart.js

## Quick Start (Windows PowerShell)

1. Create and activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies

```powershell
pip install -r requirements.txt
```

3. Prepare environment variables

```powershell
Copy-Item .env.example .env
```

4. Run migrations

```powershell
flask db upgrade
```

5. Start web app

```powershell
python wsgi.py
```

6. Start background worker (new terminal)

```powershell
celery -A celery_worker.celery worker --loglevel=info
```

7. Start scheduler (optional, new terminal)

```powershell
celery -A celery_worker.celery beat --loglevel=info
```

## Required Environment Variables

These must be present for production startup validation:

- `SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `GOOGLE_API_KEY`
- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`

Additional recommended configuration:

- `RAZORPAY_WEBHOOK_SECRET`
- `MAIL_SERVER`
- `MAIL_PORT`
- `MAIL_USE_TLS`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_DEFAULT_SENDER`
- `FRONTEND_URL`
- `OUTPUT_RETENTION_DEFAULT_DAYS`

## Production Run Commands

Web server:

```bash
gunicorn -w 4 -k gthread -b 0.0.0.0:8000 wsgi:app
```

Celery worker:

```bash
celery -A celery_worker.celery worker --loglevel=info --concurrency=4
```

Celery beat:

```bash
celery -A celery_worker.celery beat --loglevel=info
```

## Main URLs

- Reports: `/reports`
- Usage: `/usage`
- Audit log: `/audit`
- Admin panel: `/admin`
- Help center: `/help`
- Support: `/support`
- Status: `/status`
- Legal pages: `/privacy`, `/terms`, `/cookies`, `/gdpr`

## API Endpoints Added

- `/api/reports/chart-data`
- `/api/admin/system-metrics`
- `/api/support/tickets/<ticket_id>`

## Testing

```powershell
pytest
```

## Notes

- The app factory and startup flow are designed to be idempotent.
- Admin system metrics are cached briefly (`10s`) for fast refreshes.
- Report payloads are cached (`10 min`) by org/date range.
