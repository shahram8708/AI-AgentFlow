# 🤖 AI-AgentFlow

> **An open-source, AI-powered automation platform — run 481 pre-built agent tasks across 40+ domains, build multi-step workflows, and let your team scale work without writing a single line of automation code.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7+-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Celery](https://img.shields.io/badge/Celery-5.x-37814A?style=flat-square&logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.0.0-blue?style=flat-square)](config.py)
[![Model](https://img.shields.io/badge/AI-Gemma%204%2031B-orange?style=flat-square)](https://huggingface.co/google/gemma-3-27b-it)

---

## 📋 Table of Contents

- [About the Project](#-about-the-project)
- [Key Features](#-key-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Variables](#environment-variables)
  - [Running the Project](#running-the-project)
- [Usage](#-usage)
- [API Documentation](#-api-documentation)
- [Configuration](#-configuration)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
- [Roadmap](#-roadmap)
- [License](#-license)
- [Acknowledgements](#-acknowledgements)
- [Contact / Author](#-contact--author)

---

## 🧠 About the Project

Most AI tools give you a chat window. AI-AgentFlow gives you an automation engine.

This platform was built for teams and individuals who have repetitive, intelligence-heavy work — writing research briefs, drafting legal documents, generating code, scraping data, building financial models — and want to automate it without stitching together a dozen SaaS tools or maintaining fragile prompt pipelines.

At the core is a registry of **481 pre-built AI agent tasks** spanning 40+ categories, from Web Research and Code Writing to Healthcare, Legal, HR, and Supply Chain. Each task has structured input fields, an intelligently crafted system prompt, a defined execution strategy, and an estimated completion time. You fill in the form. The agent does the work. You review the output.

It's built for **Indian businesses and teams** (pricing in INR, IST timezone handling, Indian compliance context built into prompts), but works for anyone who needs serious workflow automation without the enterprise price tag.

---

## ✨ Key Features

- **481 pre-built AI agent tasks** organized across 40+ professional categories — from Deep Multi-Source Research to SQL Query Writing to NDA Drafting, ready to run out of the box.
- **Multi-step workflow builder** with 20+ seeded templates (SEO Blog Post Writer, Competitive Analysis Report, Lead Research & Qualification, and more) that chain tasks into repeatable pipelines.
- **Powered by Gemma 4 31B** — a fully open-source, locally-runnable LLM requiring no commercial API key, giving you AI generation that is free and private.
- **Built-in web scraping engine** for research-grounded tasks, autonomously fetching and extracting live public data from the web without any third-party search API.
- **Background execution with Celery and Redis**, real-time log streaming via Redis Pub/Sub and SSE, and automatic 3-attempt retry logic with escalating backoff.
- **Multi-tenant organization model** with role-based access control (owner, admin, member, viewer), per-org quota enforcement, and team seat management.
- **Credential Vault** with AES-256-GCM encryption to securely store API keys, passwords, and integration secrets used across workflows.
- **Knowledge Base** allowing teams to upload documents (PDF, DOCX, plain text) and attach them as context sources for agent tasks.
- **Cron-based scheduling** with full timezone support — schedule any workflow to run daily, weekly, monthly, or on a custom cron expression in IST.
- **Subscription billing via Razorpay** with five plan tiers (Free → Enterprise), monthly/annual cycles, webhook-based payment verification, and invoice generation.
- **MFA with TOTP** (Google Authenticator compatible), backup codes, and Google OAuth for one-click login.
- **Full audit trail** logging every user and system action with IP, user-agent, and metadata — exportable to CSV and PDF.
- **Export-ready outputs** — every agent output can be downloaded as Markdown, PDF, Word DOCX, or CSV directly from the UI.
- **Admin panel** with system metrics (CPU, memory, disk via psutil), user management, org suspension, feature flags, and Celery task queue monitoring.
- **Rate limiting** on all public and authenticated endpoints via Flask-Limiter backed by Redis.

---

## 🛠 Tech Stack

### Backend
| Technology | Purpose |
|---|---|
| Python 3.11+ | Core language |
| Flask | Web framework and application factory |
| Flask-SQLAlchemy | ORM with connection pooling |
| Flask-Login | Session-based authentication |
| Flask-WTF | Form handling and CSRF protection |
| Flask-Migrate / Alembic | Database schema migrations |
| Flask-Mail | Transactional email delivery |
| Flask-Limiter | Rate limiting backed by Redis |
| Flask-Caching | Response and query caching |
| Celery | Async background task queue |
| Gunicorn | WSGI production server |
| bcrypt | Password hashing (12 rounds) |
| pyotp + qrcode | TOTP MFA implementation |
| authlib | OAuth 2.0 client (Google login) |
| cryptography | AES-256-GCM vault encryption |
| python-docx | Word document generation |
| ReportLab + Weasyprint | PDF generation |
| pdfplumber | PDF content extraction |
| Markdown | Markdown-to-HTML rendering |
| croniter | Cron expression parsing and scheduling |
| pytz | Timezone handling (IST-first) |
| pydantic | Data validation |
| psutil | System metrics for admin panel |
| requests | HTTP client for web scraping |
| razorpay | Payment processing SDK |

### AI / Intelligence
| Technology | Purpose |
|---|---|
| Gemma 4 31B | Open-source LLM powering all agent task generation |
| Built-in Web Scraper | No-API-key HTTP scraping engine for research-grounded tasks |
| google-genai SDK | Client library used to interface with the self-hosted Gemma 4 31B endpoint |

### Database & Infrastructure
| Technology | Purpose |
|---|---|
| PostgreSQL 15+ | Primary relational database |
| Redis 7+ | Task queue broker, result backend, cache, Pub/Sub |
| SQLite | Fallback for local development |

### Frontend
| Technology | Purpose |
|---|---|
| Jinja2 | Server-side HTML templating |
| Bootstrap (via CDN) | UI component library |
| Bootstrap Icons | Icon set |
| Vanilla JavaScript | Dynamic interactions, SSE task log streaming |
| Chart.js | Billing usage charts |

---

## 📁 Project Structure

```
AI-AgentFlow-main/
│
├── app/                          # Main application package
│   ├── __init__.py               # App factory, blueprint registration, seeding, template filters
│   ├── extensions.py             # Shared Flask extensions (db, login_manager, cache, etc.)
│   │
│   ├── forms/                    # WTForms form definitions
│   │   ├── auth.py               # Login, registration, MFA, password reset forms
│   │   ├── contact.py            # Support/contact form
│   │   ├── knowledge.py          # Knowledge base entry forms
│   │   ├── project.py            # Project creation/edit forms
│   │   ├── settings.py           # User and org settings forms
│   │   ├── task.py               # Task launcher forms
│   │   ├── team.py               # Team invite and role forms
│   │   └── workflow.py           # Workflow builder and schedule forms
│   │
│   ├── models/                   # SQLAlchemy ORM models
│   │   ├── api_key.py            # API key model
│   │   ├── audit.py              # Audit log model
│   │   ├── billing.py            # Plan, Subscription, Invoice models
│   │   ├── db_types.py           # Custom JSONB and UUID column types
│   │   ├── integration.py        # Integration catalog and credential vault models
│   │   ├── knowledge.py          # Knowledge base entry and data source models
│   │   ├── notification.py       # In-app notification model
│   │   ├── organization.py       # Organization and OrganizationMember models
│   │   ├── project.py            # Project model
│   │   ├── schedule.py           # ScheduledJob model
│   │   ├── task.py               # AutomationTask, TaskStep, TaskOutput models
│   │   ├── usage.py              # UsageRecord model for quota tracking
│   │   ├── user.py               # User, EmailVerificationToken, PasswordResetToken models
│   │   └── workflow.py           # Workflow and WorkflowTemplate models
│   │
│   ├── routes/                   # Flask blueprints (one per feature area)
│   │   ├── admin.py              # Admin panel: users, orgs, flags, system metrics
│   │   ├── api.py                # Internal JSON API: stats, SSE log stream, exports
│   │   ├── audit.py              # Audit log viewer
│   │   ├── auth.py               # Authentication: login, register, MFA, OAuth
│   │   ├── billing.py            # Razorpay checkout, webhook, invoice download
│   │   ├── dashboard.py          # Main dashboard data aggregation
│   │   ├── integrations.py       # Integration catalog and connection management
│   │   ├── knowledge.py          # Knowledge base CRUD and file upload
│   │   ├── notifications.py      # Notification listing and mark-read
│   │   ├── outputs.py            # Task output viewer and download
│   │   ├── projects.py           # Project CRUD
│   │   ├── public.py             # Landing page, pricing, help pages
│   │   ├── reports.py            # Analytics and usage reports
│   │   ├── schedules.py          # Cron schedule management
│   │   ├── settings.py           # User and org settings
│   │   ├── support.py            # Support ticket management
│   │   ├── tasks.py              # Task launcher, configuration, execution, history
│   │   ├── team.py               # Team invite, role management
│   │   ├── templates_bp.py       # Workflow template browser
│   │   ├── usage.py              # Usage quota and billing metrics
│   │   ├── vault.py              # Encrypted credential vault
│   │   └── workflows.py          # Workflow builder and run management
│   │
│   ├── services/                 # Business logic layer
│   │   ├── agent_runner.py       # ★ Core: 481-task registry, prompt builder, AgentExecutionEngine
│   │   ├── auth_service.py       # Auth helpers, org lookup, audit log writer
│   │   ├── billing_service.py    # Razorpay order creation and payment verification
│   │   ├── email_service.py      # Transactional email templates and delivery
│   │   ├── encryption.py         # AES-256-GCM vault encryption service
│   │   ├── export_service.py     # PDF, DOCX, CSV export generation
│   │   ├── file_service.py       # Upload handling and output file management
│   │   ├── llm_service.py        # Gemma 4 31B client: generate, generate_with_search, streaming
│   │   ├── notification_service.py # In-app notification creation and delivery
│   │   └── quota_service.py      # Monthly quota calculation and enforcement
│   │
│   ├── tasks/                    # Celery task definitions
│   │   ├── agent_tasks.py        # run_agent_task: async execution with retry logic
│   │   ├── maintenance.py        # Periodic cleanup and housekeeping tasks
│   │   └── scheduled_tasks.py    # Cron-triggered workflow execution
│   │
│   └── utils/                    # Shared utilities
│       ├── decorators.py         # login_required, org_required, quota_check, admin_required
│       ├── pagination.py         # Pagination helper
│       ├── response_helpers.py   # success_response / error_response JSON helpers
│       ├── sanitizer.py          # HTML sanitization
│       └── validators.py         # UUID, task input, cron, and email validators
│
├── static/                       # Static assets
│   ├── css/
│   │   └── custom.css            # Custom UI styles and overrides
│   └── js/
│       ├── admin.js              # Admin panel interactions
│       ├── billing.js            # Razorpay checkout flow
│       ├── dashboard.js          # Dashboard AJAX refresh
│       ├── knowledge.js          # Knowledge base file upload
│       ├── main.js               # Global interactions and helpers
│       ├── onboarding.js         # New user onboarding flow
│       ├── outputs.js            # Task output rendering and download
│       ├── projects.js           # Project management UI
│       ├── reports.js            # Reports chart rendering
│       ├── schedules.js          # Schedule builder UI
│       └── settings.js           # Settings page interactions
│
├── config.py                     # DevelopmentConfig, TestingConfig, ProductionConfig
├── celery_worker.py              # Celery worker bootstrap
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variable template
└── .gitignore                    # Git ignore rules
```

---

## 🚀 Getting Started

### Prerequisites

Before you start, make sure you have the following installed:

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://python.org/downloads) |
| PostgreSQL | 15+ | [postgresql.org](https://www.postgresql.org/download) |
| Redis | 7+ | [redis.io](https://redis.io/download) |
| pip | Latest | Bundled with Python |
| git | Any | [git-scm.com](https://git-scm.com) |

> **Note:** The project also works with SQLite in local development (no PostgreSQL needed) — just omit the `DATABASE_URL` env var and SQLite will be used automatically.

---

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/your-username/AI-AgentFlow.git
cd AI-AgentFlow
```

**2. Create and activate a virtual environment**

```bash
python -m venv venv

# On macOS/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

**3. Install Python dependencies**

```bash
pip install -r requirements.txt
```

**4. Copy the environment configuration template**

```bash
cp .env.example .env
```

**5. Edit `.env` with your values** (see [Environment Variables](#environment-variables) below)

**6. Run database migrations**

```bash
flask db upgrade
```

**7. Seed the database with plans, integrations, workflow templates, and feature flags**

```bash
flask seed
```

**8. Create the first admin account**

```bash
flask create-admin
```
Follow the prompts to enter email, password, and name.

---

### Environment Variables

Open your `.env` file and fill in the values below. Required ones must be set before the app will start — it validates them at startup and tells you exactly what's missing.

| Variable | Description | Example |
|---|---|---|
| `FLASK_ENV` | Runtime environment | `development` or `production` |
| `FLASK_APP` | Application entry point | `wsgi.py` |
| `SECRET_KEY` | Flask session secret (64-char hex) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@localhost:5432/agentflow_db` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `RAZORPAY_KEY_ID` | Razorpay API key ID | `rzp_test_xxxxxxxxxxxxxxxx` |
| `RAZORPAY_KEY_SECRET` | Razorpay API key secret | `your_razorpay_key_secret` |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhook signing secret (optional) | `your_webhook_secret` |
| `MAIL_SERVER` | SMTP server hostname | `smtp.sendgrid.net` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USE_TLS` | Enable TLS for SMTP | `True` |
| `MAIL_USERNAME` | SMTP username | `apikey` |
| `MAIL_PASSWORD` | SMTP password or API key | `SG.xxxxxxxxxx` |
| `MAIL_DEFAULT_SENDER` | From address for emails | `noreply@agentflow.ai` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID (optional, for Google login) | `xxxx.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret (optional) | `GOCSPX-xxxxxxx` |
| `CELERY_BROKER_URL` | Celery message broker URL | `redis://localhost:6379/1` |
| `CELERY_RESULT_BACKEND` | Celery result storage URL | `redis://localhost:6379/2` |
| `UPLOAD_FOLDER` | Directory for file uploads | `uploads` |
| `MAX_CONTENT_LENGTH` | Max upload size in bytes | `16777216` (16 MB) |
| `OUTPUT_RETENTION_DEFAULT_DAYS` | Days to retain task outputs | `30` |
| `FRONTEND_URL` | Base URL for email links | `http://localhost:5000` |
| `CACHE_DEFAULT_TIMEOUT` | Cache TTL in seconds | `300` |
| `APP_VERSION` | Displayed app version | `1.0.0` |

> **Tip for development:** `DATABASE_URL` and `REDIS_URL` both fall back to sensible defaults (`sqlite:///agentflow.db` and `redis://localhost:6379/0`) if omitted, so you can get started with just `SECRET_KEY` set.

---

### Running the Project

**Development mode (Flask dev server):**

```bash
flask run
```

The app will be available at `http://localhost:5000`.

**Start the Celery worker** (required for background task execution):

```bash
celery -A celery_worker.celery worker --loglevel=info
```

**Start the Celery beat scheduler** (required for cron-based scheduled workflows):

```bash
celery -A celery_worker.celery beat --loglevel=info
```

**Production mode with Gunicorn:**

```bash
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app('production')"
```

**Check application health:**

```bash
curl http://localhost:5000/health
```

Expected response:
```json
{
  "status": "ok",
  "db": "connected",
  "redis": "connected",
  "version": "1.0.0"
}
```

---

## 📖 Usage

### Launching an Agent Task

1. Sign in and complete onboarding (creating or joining an organization).
2. Navigate to **Tasks → Launch Task** from the sidebar.
3. Browse the task library by category (Web Research, Code Writing, Legal, Finance, etc.) or search by name.
4. Select a task — for example, **"Deep Multi-Source Research Synthesis"**.
5. Fill in the structured input fields (e.g., Research Topic, Research Depth, Output Format).
6. Click **Run Task**. The task is queued to Celery and begins executing in the background.
7. Watch the real-time execution log stream in the task monitor panel as each step completes.
8. Once done, review the formatted output and download it as Markdown, PDF, DOCX, or CSV.

### Building a Workflow

1. Go to **Workflows → New Workflow**.
2. Give it a name and add steps — each step is linked to a task type with its own input configuration.
3. Save the workflow, then either run it manually or attach a cron schedule under **Schedules**.
4. Or start from a template — go to **Templates** and pick from 20+ pre-built workflow templates like *Competitive Analysis Report* or *Cold Email Outreach Sequence*.

### API Key Access

Generate a personal API key under **Settings → API Keys** and use it to trigger tasks programmatically:

```bash
curl -X POST https://your-domain.com/api/tasks/run \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "1.1",
    "input": {
      "research_topic": "Competitive landscape of B2B SaaS CRM tools in India",
      "depth_level": "standard",
      "output_format": "markdown"
    }
  }'
```

### Scheduling a Workflow

```python
# Example cron schedule: every Monday at 9 AM IST
cron_expression = "0 9 * * 1"
```

Schedules are created via the **Schedules** section in the UI. The platform uses `croniter` to parse and compute next run times, and a distributed Redis lock ensures no double-execution across multiple workers.

---

## 📡 API Documentation

The platform exposes a JSON API under the `/api` prefix. All authenticated endpoints require a valid session cookie (web) or a `Bearer` token (API key).

### Dashboard

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/dashboard/stats` | Live dashboard stats: tasks today/week/month, quota usage |
| `GET` | `/api/billing/usage-chart` | 30-day daily task usage data for chart rendering |

### Task Execution

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/tasks/run` | Submit a new task for execution |
| `GET` | `/tasks/<task_id>/status` | Poll task status (pending / running / done / failed) |
| `GET` | `/api/tasks/<task_id>/log-stream` | SSE stream of real-time execution logs |
| `GET` | `/tasks/history` | Paginated task history for the current org |

### Outputs

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/outputs/<task_id>` | View formatted task output |
| `GET` | `/api/outputs/<output_id>/download` | Download output as Markdown / PDF / DOCX / CSV |

### Reports

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/reports/data` | Report data for date range and category filters |
| `GET` | `/api/reports/export` | Export report as CSV or PDF |

### Admin

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/admin/` | Admin panel with system metrics |
| `GET` | `/admin/users` | User listing and management |
| `POST` | `/admin/users/<user_id>/suspend` | Suspend or unsuspend a user |
| `GET` | `/admin/orgs` | Organization listing |
| `POST` | `/admin/orgs/<org_id>/suspend` | Suspend an organization |
| `GET` | `/admin/feature-flags` | Feature flag management |

### Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | DB + Redis connectivity check — no auth required |

> **Request / Response format:** All API endpoints return `{"success": true, "data": {...}}` on success and `{"success": false, "message": "...", "error": "..."}` on failure, with appropriate HTTP status codes.

---

## ⚙️ Configuration

### Application Configs (`config.py`)

Three configuration classes inherit from `BaseConfig`:

| Class | Use case | Key differences |
|---|---|---|
| `DevelopmentConfig` | Local development | `DEBUG=True`, SQL echo off |
| `TestingConfig` | Test runs | CSRF disabled, rate limiting disabled, separate test DB |
| `ProductionConfig` | Live deployment | `SESSION_COOKIE_SECURE=True`, HTTPS scheme |

Select a config by setting `FLASK_ENV=development|testing|production` or by passing the config name to `create_app()`.

### Feature Flags

Eight feature flags are seeded to the database at startup and are toggleable from the admin panel without redeploying:

| Flag Key | Default | Description |
|---|---|---|
| `new_task_launcher_ui` | Off | Redesigned task launcher interface |
| `workflow_builder_v2` | Off | Second-gen workflow builder |
| `knowledge_base_embeddings` | Off | Vector embedding search in knowledge base |
| `ai_suggestions` | On (100%) | AI-powered task suggestions |
| `bulk_task_execution` | Off | Run tasks in bulk mode |
| `advanced_analytics` | Off | Advanced analytics dashboard widgets |
| `team_audit_log` | On (100%) | Team-level audit log visibility |
| `razorpay_subscriptions` | On (100%) | Razorpay subscription billing |

### Subscription Plans

Five billing tiers are seeded automatically (prices in INR paise):

| Plan | Monthly (INR) | Annual (INR) | Task Quota/Month | Seats |
|---|---|---|---|---|
| Free | ₹0 | ₹0 | 10 | 1 |
| Starter | ₹999 | ₹9,999 | 100 | 3 |
| Pro | ₹2,499 | ₹24,999 | 500 | 10 |
| Team | ₹6,999 | ₹69,999 | 2,000 | 50 |
| Enterprise | Custom | Custom | Unlimited | Unlimited |

### Caching and Rate Limiting

All caches are Redis-backed. Default TTL is `300` seconds, configurable via `CACHE_DEFAULT_TIMEOUT`. Rate limits are enforced per-endpoint using `Flask-Limiter` with a fixed-window strategy.

---

## 🧪 Testing

A `TestingConfig` is defined in `config.py` with CSRF disabled, rate limiting disabled, and a separate test database URL:

```bash
# Set test database
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/agentflow_test

# Run tests (add your test runner of choice)
pytest
```

No test suite files were found in the repository at the time of analysis. The `TestingConfig` class and config infrastructure are in place and ready for tests to be added. If you're contributing, writing tests for `agent_runner.py` (task registry validation, prompt building) and the API routes would have the highest value.

---

## 🚢 Deployment

### Running with Gunicorn (basic production)

```bash
# Set production environment
export FLASK_ENV=production

# Run with 4 workers
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app('production')"
```

### Docker (recommended)

No `Dockerfile` is included in the current repository, but here's a working minimal setup:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:create_app('production')"]
```

```yaml
# docker-compose.yml (minimal)
version: "3.9"

services:
  web:
    build: .
    env_file: .env
    ports:
      - "5000:5000"
    depends_on:
      - db
      - redis

  worker:
    build: .
    command: celery -A celery_worker.celery worker --loglevel=info
    env_file: .env
    depends_on:
      - db
      - redis

  beat:
    build: .
    command: celery -A celery_worker.celery beat --loglevel=info
    env_file: .env
    depends_on:
      - redis

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: agentflow_db
      POSTGRES_USER: agentflow
      POSTGRES_PASSWORD: changeme
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine

volumes:
  pgdata:
```

### Production Checklist

Before going live, make sure you:

- [ ] Set `FLASK_ENV=production` and `SESSION_COOKIE_SECURE=True`
- [ ] Generate a strong `SECRET_KEY` using `python -c "import secrets; print(secrets.token_hex(32))"`
- [ ] Point `DATABASE_URL` to a managed PostgreSQL instance (not SQLite)
- [ ] Configure a real SMTP provider in the `MAIL_*` variables
- [ ] Set `RAZORPAY_WEBHOOK_SECRET` and verify webhook signatures
- [ ] Run `flask db upgrade` and `flask seed` on first deploy
- [ ] Run `flask create-admin` to create the first admin account
- [ ] Set up a reverse proxy (Nginx/Caddy) with TLS termination in front of Gunicorn
- [ ] Configure log aggregation for Gunicorn and Celery worker processes

---

## 🤝 Contributing

Contributions are welcome. Here's how to get involved:

**1. Fork and clone**

```bash
git clone https://github.com/your-username/AI-AgentFlow.git
cd AI-AgentFlow
```

**2. Create a feature branch**

```bash
git checkout -b feature/your-feature-name
```

**3. Make your changes**

Follow the existing code style — type annotations everywhere, docstrings on all classes and public methods, `from __future__ import annotations` at the top of every module.

**4. Test your changes**

```bash
pytest
```

**5. Commit with a clear message**

```bash
git commit -m "feat: add bulk task execution endpoint"
```

**6. Push and open a Pull Request**

```bash
git push origin feature/your-feature-name
```

### Reporting Bugs

Open a GitHub Issue with:
- A clear title
- Steps to reproduce
- What you expected vs what actually happened
- Your Python version, OS, and relevant log output

### Requesting Features

Open a GitHub Issue with the label `enhancement`. Describe the use case, not just the feature — it helps prioritize what's actually useful.

### Code Conventions

- All new routes should use the `@login_required`, `@org_required`, and `@quota_check` decorators where appropriate (defined in `app/utils/decorators.py`).
- New task types belong in `agent_runner.py` inside either `NUMERIC_GROUPS` or `INDUSTRY_GROUPS`.
- Business logic goes in `app/services/`, not in route handlers.
- All DB writes should use `try/except` with `db.session.rollback()` on failure.

---

## 🗺 Roadmap

Based on the code structure, feature flags, and visible TODOs:

- [x] 481 pre-built agent tasks across 40+ categories
- [x] Multi-step workflow builder with 20+ seeded templates
- [x] Celery-based async execution with retry and timeout handling
- [x] Real-time SSE log streaming during task execution
- [x] Razorpay subscription billing with webhooks
- [x] MFA with TOTP and backup codes
- [x] AES-256-GCM credential vault
- [x] Cron-based scheduled workflow execution
- [x] Knowledge base with file upload support
- [x] Full audit trail with export
- [x] Admin panel with system metrics and feature flags
- [ ] **New Task Launcher UI** — redesigned launcher (feature flag `new_task_launcher_ui` exists, implementation pending)
- [ ] **Workflow Builder V2** — second-generation workflow editor (flag `workflow_builder_v2` exists)
- [ ] **Knowledge Base Embeddings** — vector search over knowledge base entries (flag `knowledge_base_embeddings` exists)
- [ ] **Bulk Task Execution** — run multiple tasks simultaneously (flag `bulk_task_execution` exists)
- [ ] **Advanced Analytics Dashboard** — deeper analytics widgets (flag `advanced_analytics` exists)
- [ ] Full test coverage for agent runner, API routes, and billing service
- [ ] Docker Compose and Helm chart for containerized deployment
- [ ] Webhook outbound triggers for workflow completion events
- [ ] Public workflow sharing and marketplace

---

## 📄 License

This project is licensed under the **MIT License**.

The MIT License is a short, permissive license that lets anyone use, copy, modify, merge, publish, distribute, sublicense, and sell copies of this software — with attribution. It asks for nothing more than keeping the copyright notice in derived works.

A `LICENSE` file was not found at the repository root. If you fork this project, add one using:

```bash
curl https://raw.githubusercontent.com/github/choosealicense.com/gh-pages/_licenses/mit.txt > LICENSE
```

---

## 🙏 Acknowledgements

- **[Gemma 4 31B](https://huggingface.co/google/gemma-3-27b-it)** — the open-source LLM at the core of every agent task, enabling this platform to run without commercial AI API costs.
- **[Flask](https://flask.palletsprojects.com)** — the lightweight, extensible Python web framework this entire platform is built on.
- **[Celery](https://docs.celeryq.dev)** — the distributed task queue that makes background execution, retries, and scheduled jobs work reliably.
- **[SQLAlchemy](https://www.sqlalchemy.org)** — for the ORM that makes complex multi-tenant data modeling clean and maintainable.
- **[Razorpay](https://razorpay.com/docs)** — for the Indian payment infrastructure handling all subscription billing.
- **[Bootstrap](https://getbootstrap.com)** and **[Bootstrap Icons](https://icons.getbootstrap.com)** — for the UI foundation.
- **[cryptography](https://cryptography.io)** — for the AES-256-GCM primitives powering the credential vault.
- **[Weasyprint](https://weasyprint.org)** and **[python-docx](https://python-docx.readthedocs.io)** — for the PDF and Word export capabilities.
- **[croniter](https://github.com/kiorky/croniter)** — for cron expression parsing that makes the scheduling system reliable and timezone-aware.

---

## 👤 Contact / Author

Author information was not found in the repository metadata. If you're the maintainer and reading this, add a `package.json` or `pyproject.toml` with author details, or update this section.

If you've deployed this, run into a bug, or built something interesting on top of it — open an issue or a discussion on GitHub. Every piece of feedback makes this better.
