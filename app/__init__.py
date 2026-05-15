"""Application factory module."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import click
import markdown as markdown_lib
import pytz
import redis
from dotenv import load_dotenv
from flask import Flask, current_app, flash, jsonify, render_template, request, session
from flask.cli import with_appcontext
from flask_login import current_user
from google import genai
from markupsafe import Markup
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import cache, csrf, db, limiter, login_manager, mail, migrate
from app.models import (
    FeatureFlag,
    Integration,
    Notification,
    Organization,
    OrganizationMember,
    Plan,
    UsageRecord,
    User,
    WorkflowTemplate,
)
from app.routes import (
    admin_bp,
    api_bp,
    audit_bp,
    auth_bp,
    billing_bp,
    dashboard_bp,
    integrations_bp,
    knowledge_bp,
    notifications_bp,
    outputs_bp,
    projects_bp,
    public_bp,
    reports_bp,
    schedules_bp,
    settings_bp,
    support_bp,
    tasks_bp,
    team_bp,
    templates_bp,
    usage_bp,
    vault_bp,
    workflows_bp,
)
from app.routes.auth import init_oauth
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.tasks import celery as celery_ext
from app.tasks import make_celery
from config import DevelopmentConfig, ProductionConfig, TestingConfig

REQUIRED_ENV_VARS = (
    "SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "GOOGLE_API_KEY",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
)

REQUIRED_ENV_HINTS = {
    "SECRET_KEY": "Set a long random secret in .env. Generate one using: python -c \"import secrets; print(secrets.token_hex(32))\"",
    "DATABASE_URL": "Provide your PostgreSQL connection string, for example postgresql://user:pass@localhost:5432/agentflow_db",
    "REDIS_URL": "Provide your Redis connection string, for example redis://localhost:6379/0",
    "GOOGLE_API_KEY": "Create an API key in Google AI Studio at https://aistudio.google.com",
    "RAZORPAY_KEY_ID": "Get your API key ID from Razorpay Dashboard > Settings > API Keys",
    "RAZORPAY_KEY_SECRET": "Get your API key secret from Razorpay Dashboard > Settings > API Keys",
}


def _validate_required_environment_variables() -> None:
    """Validate required environment variables for startup."""

    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        hints = "\n".join(
            f"  {var}: {REQUIRED_ENV_HINTS.get(var, 'Set this value in your .env or deployment environment.')}"
            for var in missing
        )
        raise RuntimeError(
            "Missing required environment variable(s): "
            f"{', '.join(missing)}.\n"
            "Fix the variables below in your .env file or deployment configuration:\n"
            f"{hints}"
        )


def _register_blueprints(app: Flask) -> None:
    """Register all application blueprints with required URL prefixes."""

    app.register_blueprint(public_bp, url_prefix="")
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp, url_prefix="")
    app.register_blueprint(tasks_bp, url_prefix="")
    app.register_blueprint(workflows_bp, url_prefix="")
    app.register_blueprint(templates_bp, url_prefix="")
    app.register_blueprint(integrations_bp, url_prefix="")
    app.register_blueprint(outputs_bp, url_prefix="")
    app.register_blueprint(knowledge_bp, url_prefix="")
    app.register_blueprint(schedules_bp, url_prefix="")
    app.register_blueprint(projects_bp, url_prefix="")
    app.register_blueprint(vault_bp, url_prefix="")
    app.register_blueprint(team_bp, url_prefix="")
    app.register_blueprint(settings_bp, url_prefix="")
    app.register_blueprint(usage_bp, url_prefix="")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(reports_bp, url_prefix="")
    app.register_blueprint(notifications_bp, url_prefix="")
    app.register_blueprint(audit_bp, url_prefix="")
    app.register_blueprint(support_bp, url_prefix="")
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)


def _register_error_handlers(app: Flask) -> None:
    """Register application level HTML error handlers."""

    @app.errorhandler(400)
    def bad_request(error: Exception):
        """Render HTTP 400 page."""

        return render_template("errors/400.html", error=error), 400

    @app.errorhandler(401)
    def unauthorized(error: Exception):
        """Render HTTP 401 page."""

        return render_template("errors/401.html", error=error), 401

    @app.errorhandler(403)
    def forbidden(error: Exception):
        """Render HTTP 403 page."""

        return render_template("errors/403.html", error=error), 403

    @app.errorhandler(404)
    def not_found(error: Exception):
        """Render HTTP 404 page."""

        return render_template("errors/404.html", error=error), 404

    @app.errorhandler(429)
    def rate_limited(error: Exception):
        """Render HTTP 429 page."""

        return render_template("errors/429.html", error=error), 429

    @app.errorhandler(500)
    def server_error(error: Exception):
        """Render HTTP 500 page."""

        return render_template("errors/500.html", error=error), 500


def _upsert_record(model: Any, lookup: dict[str, Any], values: dict[str, Any]) -> Any:
    """Create or update a SQLAlchemy model row by lookup attributes."""

    record = model.query.filter_by(**lookup).first()
    if record is None:
        record = model(**lookup)
        db.session.add(record)
    for key, value in values.items():
        setattr(record, key, value)
    return record


def _seed_plans() -> None:
    """Insert or update billing plans."""

    plans = [
        {
            "name": "Free",
            "slug": "free",
            "price_monthly_inr": 0,
            "price_annual_inr": 0,
            "task_quota_monthly": 10,
            "seat_limit": 1,
            "output_retention_days": 30,
            "features_json": ["basic_tasks", "email_support"],
        },
        {
            "name": "Starter",
            "slug": "starter",
            "price_monthly_inr": 99900,
            "price_annual_inr": 999000,
            "task_quota_monthly": 100,
            "seat_limit": 3,
            "output_retention_days": 90,
            "features_json": [
                "basic_tasks",
                "workflow_templates",
                "team_collaboration",
            ],
        },
        {
            "name": "Pro",
            "slug": "pro",
            "price_monthly_inr": 249900,
            "price_annual_inr": 2499000,
            "task_quota_monthly": 500,
            "seat_limit": 10,
            "output_retention_days": 365,
            "features_json": [
                "all_tasks",
                "workflows",
                "templates",
                "api_access",
                "priority_support",
            ],
        },
        {
            "name": "Team",
            "slug": "team",
            "price_monthly_inr": 699900,
            "price_annual_inr": 6999000,
            "task_quota_monthly": 2000,
            "seat_limit": 50,
            "output_retention_days": -1,
            "features_json": [
                "all_tasks",
                "workflows",
                "templates",
                "api_access",
                "priority_support",
                "sso",
                "advanced_reporting",
            ],
        },
        {
            "name": "Enterprise",
            "slug": "enterprise",
            "price_monthly_inr": -1,
            "price_annual_inr": -1,
            "task_quota_monthly": -1,
            "seat_limit": -1,
            "output_retention_days": -1,
            "features_json": [
                "all_features",
                "dedicated_support",
                "custom_sla",
                "custom_integrations",
            ],
        },
    ]

    for plan in plans:
        lookup = {"slug": plan["slug"]}
        values = {**plan, "is_active": True}
        _upsert_record(Plan, lookup, values)


def _seed_integrations() -> None:
    """Insert or update integration catalog."""

    integrations = [
        ("google_workspace", "Google Workspace", "Productivity", "oauth"),
        ("gmail", "Gmail", "Productivity", "oauth"),
        ("google_drive", "Google Drive", "Storage", "oauth"),
        ("google_calendar", "Google Calendar", "Productivity", "oauth"),
        ("google_sheets", "Google Sheets", "Productivity", "oauth"),
        ("notion", "Notion", "Productivity", "api_key"),
        ("airtable", "Airtable", "Productivity", "api_key"),
        ("slack", "Slack", "Communication", "oauth"),
        ("github", "GitHub", "Development", "oauth"),
        ("gitlab", "GitLab", "Development", "oauth"),
        ("jira", "Jira", "Productivity", "oauth"),
        ("trello", "Trello", "Productivity", "oauth"),
        ("asana", "Asana", "Productivity", "oauth"),
        ("clickup", "ClickUp", "Productivity", "api_key"),
        ("hubspot", "HubSpot", "CRM", "oauth"),
        ("salesforce", "Salesforce", "CRM", "oauth"),
        ("pipedrive", "Pipedrive", "CRM", "api_key"),
        ("mailchimp", "Mailchimp", "Marketing", "api_key"),
        ("sendgrid", "SendGrid", "Communication", "api_key"),
        ("twilio", "Twilio", "Communication", "api_key"),
        ("zapier", "Zapier", "Productivity", "webhook"),
        ("make", "Make", "Productivity", "webhook"),
        ("shopify", "Shopify", "Finance", "api_key"),
        ("woocommerce", "WooCommerce", "Finance", "api_key"),
        ("linkedin", "LinkedIn", "Marketing", "oauth"),
        ("twitter_x", "Twitter/X", "Marketing", "oauth"),
        ("facebook", "Facebook", "Marketing", "oauth"),
        ("instagram", "Instagram", "Marketing", "oauth"),
        ("youtube", "YouTube", "Marketing", "oauth"),
        ("dropbox", "Dropbox", "Storage", "oauth"),
        ("onedrive", "OneDrive", "Storage", "oauth"),
        ("aws_s3", "AWS S3", "Storage", "api_key"),
        ("razorpay", "Razorpay", "Finance", "api_key"),
        ("openai", "OpenAI", "AI", "api_key"),
        ("anthropic", "Anthropic", "AI", "api_key"),
    ]

    for service_name, display_name, category, auth_type in integrations:
        _upsert_record(
            Integration,
            {"service_name": service_name},
            {
                "display_name": display_name,
                "category": category,
                "auth_type": auth_type,
                "is_active": True,
                "description": f"{display_name} integration connector.",
            },
        )


def _seed_workflow_templates() -> None:
    """Insert or update default workflow templates."""

    templates = [
        {
            "name": "Deep Company Research Report",
            "category": "Research",
            "description": "Gather and synthesize deep company intelligence.",
            "steps_json": [
                {
                    "step_name": "Collect public company profile",
                    "task_type": "web_research",
                    "description": "Gather official company information and leadership.",
                },
                {
                    "step_name": "Analyze financial and growth signals",
                    "task_type": "financial_research",
                    "description": "Summarize revenue proxies and funding history.",
                },
                {
                    "step_name": "Map competitors",
                    "task_type": "competitive_analysis",
                    "description": "Identify direct and adjacent competitors.",
                },
                {
                    "step_name": "Generate final report",
                    "task_type": "report_generation",
                    "description": "Produce executive ready report output.",
                },
            ],
            "required_integrations": ["google_drive"],
            "is_featured": True,
            "estimated_time_seconds": 480,
            "difficulty": "intermediate",
            "tags_json": ["research", "company", "intelligence"],
        },
        {
            "name": "Cold Email Outreach Sequence",
            "category": "Sales",
            "description": "Generate and schedule a personalized outreach sequence.",
            "steps_json": [
                {
                    "step_name": "Import lead list",
                    "task_type": "data_import",
                    "description": "Load leads from CRM or CSV.",
                },
                {
                    "step_name": "Enrich lead data",
                    "task_type": "lead_enrichment",
                    "description": "Find role and company context for personalization.",
                },
                {
                    "step_name": "Draft sequence",
                    "task_type": "email_drafting",
                    "description": "Write email sequence with multi touch cadence.",
                },
                {
                    "step_name": "Compliance review",
                    "task_type": "policy_check",
                    "description": "Review copy for compliance and quality.",
                },
                {
                    "step_name": "Schedule sends",
                    "task_type": "campaign_schedule",
                    "description": "Queue sends via connected email provider.",
                },
            ],
            "required_integrations": ["gmail", "hubspot"],
            "is_featured": False,
            "estimated_time_seconds": 420,
            "difficulty": "intermediate",
            "tags_json": ["sales", "email", "outreach"],
        },
        {
            "name": "SEO Blog Post Writer",
            "category": "Marketing",
            "description": "Create SEO optimized long form blog drafts.",
            "steps_json": [
                {
                    "step_name": "Keyword research",
                    "task_type": "seo_keyword_research",
                    "description": "Identify target and secondary keywords.",
                },
                {
                    "step_name": "Outline generation",
                    "task_type": "content_outline",
                    "description": "Produce structured heading hierarchy.",
                },
                {
                    "step_name": "Draft blog",
                    "task_type": "content_generation",
                    "description": "Generate readable and optimized article.",
                },
                {
                    "step_name": "Meta and QA",
                    "task_type": "seo_optimization",
                    "description": "Generate title, meta, and internal links.",
                },
            ],
            "required_integrations": ["google_docs"],
            "is_featured": True,
            "estimated_time_seconds": 360,
            "difficulty": "beginner",
            "tags_json": ["seo", "blog", "content"],
        },
        {
            "name": "Job Description Writer",
            "category": "HR",
            "description": "Create role specific and inclusive job descriptions.",
            "steps_json": [
                {
                    "step_name": "Collect role requirements",
                    "task_type": "input_collection",
                    "description": "Gather hiring manager requirements.",
                },
                {
                    "step_name": "Draft JD",
                    "task_type": "document_generation",
                    "description": "Create role overview and responsibilities.",
                },
                {
                    "step_name": "Bias and clarity review",
                    "task_type": "language_review",
                    "description": "Improve inclusivity and readability.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 180,
            "difficulty": "beginner",
            "tags_json": ["hr", "hiring", "jd"],
        },
        {
            "name": "Competitive Analysis Report",
            "category": "Research",
            "description": "Compare product positioning across competitors.",
            "steps_json": [
                {
                    "step_name": "Define competitors",
                    "task_type": "competitor_selection",
                    "description": "Select direct and secondary competitors.",
                },
                {
                    "step_name": "Capture pricing and features",
                    "task_type": "market_scrape",
                    "description": "Gather public pricing and product tiers.",
                },
                {
                    "step_name": "Review messaging",
                    "task_type": "content_analysis",
                    "description": "Analyze positioning and value propositions.",
                },
                {
                    "step_name": "SWOT summary",
                    "task_type": "strategic_analysis",
                    "description": "Build strengths, weaknesses, opportunities, threats.",
                },
                {
                    "step_name": "Publish report",
                    "task_type": "report_generation",
                    "description": "Create executive summary report.",
                },
            ],
            "required_integrations": ["google_sheets"],
            "is_featured": True,
            "estimated_time_seconds": 540,
            "difficulty": "advanced",
            "tags_json": ["strategy", "competition", "analysis"],
        },
        {
            "name": "Resume & Cover Letter Generator",
            "category": "Career",
            "description": "Generate tailored resume and cover letter drafts.",
            "steps_json": [
                {
                    "step_name": "Parse experience details",
                    "task_type": "document_parse",
                    "description": "Extract skills and achievements.",
                },
                {
                    "step_name": "Compose resume bullets",
                    "task_type": "resume_generation",
                    "description": "Build quantified impact bullets.",
                },
                {
                    "step_name": "Draft cover letter",
                    "task_type": "cover_letter_generation",
                    "description": "Generate role specific letter.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 240,
            "difficulty": "beginner",
            "tags_json": ["career", "resume", "cover_letter"],
        },
        {
            "name": "NDA Contract Drafter",
            "category": "Legal",
            "description": "Produce NDA drafts with standard legal structure.",
            "steps_json": [
                {
                    "step_name": "Collect party details",
                    "task_type": "legal_intake",
                    "description": "Capture parties and jurisdiction details.",
                },
                {
                    "step_name": "Generate NDA draft",
                    "task_type": "legal_document_generation",
                    "description": "Draft NDA with confidentiality clauses.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 150,
            "difficulty": "intermediate",
            "tags_json": ["legal", "nda", "contracts"],
        },
        {
            "name": "Social Media Content Calendar",
            "category": "Marketing",
            "description": "Create monthly social post plans by channel.",
            "steps_json": [
                {
                    "step_name": "Gather campaign goals",
                    "task_type": "campaign_planning",
                    "description": "Define campaign objective and audience.",
                },
                {
                    "step_name": "Generate post ideas",
                    "task_type": "social_ideation",
                    "description": "Generate post concepts per channel.",
                },
                {
                    "step_name": "Create posting calendar",
                    "task_type": "calendar_generation",
                    "description": "Build a day by day posting schedule.",
                },
                {
                    "step_name": "Export to sheet",
                    "task_type": "export",
                    "description": "Publish plan to spreadsheet format.",
                },
            ],
            "required_integrations": ["google_sheets"],
            "is_featured": False,
            "estimated_time_seconds": 300,
            "difficulty": "beginner",
            "tags_json": ["social", "calendar", "marketing"],
        },
        {
            "name": "Financial Statement Analysis",
            "category": "Finance",
            "description": "Summarize key trends from uploaded statements.",
            "steps_json": [
                {
                    "step_name": "Extract financial data",
                    "task_type": "document_extraction",
                    "description": "Parse P and L and balance sheet data.",
                },
                {
                    "step_name": "Compute ratios",
                    "task_type": "ratio_analysis",
                    "description": "Calculate liquidity and profitability ratios.",
                },
                {
                    "step_name": "Generate summary",
                    "task_type": "report_generation",
                    "description": "Provide concise financial narrative.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 270,
            "difficulty": "intermediate",
            "tags_json": ["finance", "statements", "analysis"],
        },
        {
            "name": "Customer Support FAQ Builder",
            "category": "Support",
            "description": "Generate support FAQ from historical ticket data.",
            "steps_json": [
                {
                    "step_name": "Collect ticket themes",
                    "task_type": "ticket_clustering",
                    "description": "Cluster issues by recurring topics.",
                },
                {
                    "step_name": "Draft FAQ answers",
                    "task_type": "faq_generation",
                    "description": "Create clear and accurate answers.",
                },
                {
                    "step_name": "Publish help center draft",
                    "task_type": "knowledge_publish",
                    "description": "Generate publish ready FAQ document.",
                },
            ],
            "required_integrations": ["zendesk"],
            "is_featured": False,
            "estimated_time_seconds": 240,
            "difficulty": "beginner",
            "tags_json": ["support", "faq", "help_center"],
        },
        {
            "name": "Python Code Debugger & Refactorer",
            "category": "Development",
            "description": "Analyze code issues and return improved implementation.",
            "steps_json": [
                {
                    "step_name": "Static issue scan",
                    "task_type": "code_analysis",
                    "description": "Identify bugs and anti patterns.",
                },
                {
                    "step_name": "Refactor proposal",
                    "task_type": "code_refactor",
                    "description": "Generate cleaned and optimized code.",
                },
                {
                    "step_name": "Test case suggestions",
                    "task_type": "test_generation",
                    "description": "Draft high impact unit tests.",
                },
            ],
            "required_integrations": ["github"],
            "is_featured": False,
            "estimated_time_seconds": 300,
            "difficulty": "advanced",
            "tags_json": ["python", "debug", "refactor"],
        },
        {
            "name": "Lead Research & Qualification",
            "category": "Sales",
            "description": "Research leads and score qualification signals.",
            "steps_json": [
                {
                    "step_name": "Collect lead profiles",
                    "task_type": "lead_research",
                    "description": "Enrich leads with company context.",
                },
                {
                    "step_name": "Fit scoring",
                    "task_type": "lead_scoring",
                    "description": "Assign fit score from ICP criteria.",
                },
                {
                    "step_name": "Intent signal extraction",
                    "task_type": "intent_analysis",
                    "description": "Identify buying intent signals.",
                },
                {
                    "step_name": "Prioritize accounts",
                    "task_type": "prioritization",
                    "description": "Rank leads by score and urgency.",
                },
                {
                    "step_name": "Export to CRM",
                    "task_type": "crm_sync",
                    "description": "Write qualified leads back to CRM.",
                },
            ],
            "required_integrations": ["hubspot", "salesforce"],
            "is_featured": True,
            "estimated_time_seconds": 420,
            "difficulty": "advanced",
            "tags_json": ["leads", "sales", "qualification"],
        },
        {
            "name": "Weekly News Briefing Generator",
            "category": "Research",
            "description": "Compile weekly industry highlights and summaries.",
            "steps_json": [
                {
                    "step_name": "Source headline feeds",
                    "task_type": "news_collection",
                    "description": "Gather top stories from selected sources.",
                },
                {
                    "step_name": "Summarize key updates",
                    "task_type": "news_summarization",
                    "description": "Create concise bullet summaries.",
                },
                {
                    "step_name": "Draft weekly briefing",
                    "task_type": "report_generation",
                    "description": "Assemble digest for stakeholders.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 180,
            "difficulty": "beginner",
            "tags_json": ["news", "briefing", "research"],
        },
        {
            "name": "Vendor Comparison Report",
            "category": "Procurement",
            "description": "Compare vendor options with weighted criteria.",
            "steps_json": [
                {
                    "step_name": "Define evaluation criteria",
                    "task_type": "criteria_definition",
                    "description": "Capture must have and nice to have criteria.",
                },
                {
                    "step_name": "Collect vendor data",
                    "task_type": "vendor_research",
                    "description": "Gather pricing, SLA, and feature data.",
                },
                {
                    "step_name": "Score vendors",
                    "task_type": "scorecard_generation",
                    "description": "Apply weighted scoring model.",
                },
                {
                    "step_name": "Generate recommendation",
                    "task_type": "recommendation",
                    "description": "Recommend best fit vendor and rationale.",
                },
            ],
            "required_integrations": ["google_sheets"],
            "is_featured": False,
            "estimated_time_seconds": 360,
            "difficulty": "intermediate",
            "tags_json": ["procurement", "vendor", "comparison"],
        },
        {
            "name": "Employee Onboarding Package",
            "category": "HR",
            "description": "Generate complete onboarding docs and checklists.",
            "steps_json": [
                {
                    "step_name": "Collect employee info",
                    "task_type": "hr_intake",
                    "description": "Capture role, team, and joining details.",
                },
                {
                    "step_name": "Generate welcome note",
                    "task_type": "document_generation",
                    "description": "Create personalized welcome content.",
                },
                {
                    "step_name": "Create onboarding checklist",
                    "task_type": "checklist_generation",
                    "description": "Build role based onboarding checklist.",
                },
                {
                    "step_name": "Prepare policy packet",
                    "task_type": "policy_compilation",
                    "description": "Bundle relevant HR policy documents.",
                },
                {
                    "step_name": "Send package",
                    "task_type": "notification_send",
                    "description": "Share package with employee and manager.",
                },
            ],
            "required_integrations": ["gmail"],
            "is_featured": False,
            "estimated_time_seconds": 300,
            "difficulty": "intermediate",
            "tags_json": ["hr", "onboarding", "employee"],
        },
        {
            "name": "Product Listing Scraper",
            "category": "E-Commerce",
            "description": "Extract product catalog data and normalize fields.",
            "steps_json": [
                {
                    "step_name": "Collect product pages",
                    "task_type": "web_scrape",
                    "description": "Fetch listing pages and detail pages.",
                },
                {
                    "step_name": "Normalize fields",
                    "task_type": "data_normalization",
                    "description": "Normalize title, price, stock, and category.",
                },
                {
                    "step_name": "Export catalog",
                    "task_type": "export",
                    "description": "Export clean listing dataset.",
                },
            ],
            "required_integrations": ["shopify"],
            "is_featured": False,
            "estimated_time_seconds": 240,
            "difficulty": "intermediate",
            "tags_json": ["ecommerce", "scraping", "catalog"],
        },
        {
            "name": "Medical Literature Summary",
            "category": "Healthcare",
            "description": "Summarize research papers for rapid review.",
            "steps_json": [
                {
                    "step_name": "Collect paper abstracts",
                    "task_type": "literature_collection",
                    "description": "Gather relevant papers from trusted databases.",
                },
                {
                    "step_name": "Extract key findings",
                    "task_type": "literature_extraction",
                    "description": "Identify methods, outcomes, and limitations.",
                },
                {
                    "step_name": "Generate summary brief",
                    "task_type": "summary_generation",
                    "description": "Create concise medical briefing.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 300,
            "difficulty": "advanced",
            "tags_json": ["healthcare", "research", "literature"],
        },
        {
            "name": "Travel Itinerary Builder",
            "category": "Travel",
            "description": "Build day wise travel itineraries with logistics.",
            "steps_json": [
                {
                    "step_name": "Collect travel preferences",
                    "task_type": "travel_intake",
                    "description": "Capture dates, budget, and interests.",
                },
                {
                    "step_name": "Find attractions",
                    "task_type": "destination_research",
                    "description": "Identify top attractions and activities.",
                },
                {
                    "step_name": "Plan route and timing",
                    "task_type": "itinerary_planning",
                    "description": "Arrange activities by location and time.",
                },
                {
                    "step_name": "Generate final itinerary",
                    "task_type": "document_generation",
                    "description": "Deliver complete itinerary document.",
                },
            ],
            "required_integrations": ["google_calendar"],
            "is_featured": False,
            "estimated_time_seconds": 240,
            "difficulty": "beginner",
            "tags_json": ["travel", "itinerary", "planning"],
        },
        {
            "name": "Grant Application Drafter",
            "category": "Nonprofit",
            "description": "Prepare grant narrative and impact statements.",
            "steps_json": [
                {
                    "step_name": "Collect program details",
                    "task_type": "grant_intake",
                    "description": "Gather mission, outcomes, and budget inputs.",
                },
                {
                    "step_name": "Draft proposal narrative",
                    "task_type": "proposal_generation",
                    "description": "Write problem statement and approach.",
                },
                {
                    "step_name": "Draft measurable outcomes",
                    "task_type": "outcome_mapping",
                    "description": "Define KPIs and reporting metrics.",
                },
                {
                    "step_name": "Compile submission packet",
                    "task_type": "application_packaging",
                    "description": "Generate final application package.",
                },
            ],
            "required_integrations": [],
            "is_featured": False,
            "estimated_time_seconds": 360,
            "difficulty": "advanced",
            "tags_json": ["grant", "nonprofit", "proposal"],
        },
        {
            "name": "IT Incident Runbook Generator",
            "category": "IT",
            "description": "Create incident response runbooks for ops teams.",
            "steps_json": [
                {
                    "step_name": "Collect incident patterns",
                    "task_type": "incident_analysis",
                    "description": "Identify common incidents and causes.",
                },
                {
                    "step_name": "Generate remediation steps",
                    "task_type": "runbook_generation",
                    "description": "Draft step by step remediation guidance.",
                },
                {
                    "step_name": "Publish runbook",
                    "task_type": "documentation_publish",
                    "description": "Export runbook for IT operations.",
                },
            ],
            "required_integrations": ["jira"],
            "is_featured": False,
            "estimated_time_seconds": 210,
            "difficulty": "intermediate",
            "tags_json": ["it", "incident", "runbook"],
        },
    ]

    for template in templates:
        lookup = {"name": template["name"], "category": template["category"]}
        values = {
            **template,
            "is_active": True,
            "usage_count": 0,
            "icon": "bi-lightning-charge-fill",
            "preview_output": f"Preview output for {template['name']}",
        }
        _upsert_record(WorkflowTemplate, lookup, values)


def _seed_feature_flags() -> None:
    """Insert or update platform feature flags."""

    flags = [
        (
            "new_task_launcher_ui",
            "New Task Launcher UI",
            "Enable redesigned task launcher user interface.",
            False,
            0,
        ),
        (
            "workflow_builder_v2",
            "Workflow Builder V2",
            "Enable the second generation workflow builder.",
            False,
            0,
        ),
        (
            "knowledge_base_embeddings",
            "Knowledge Base Embeddings",
            "Enable vector embedding search in knowledge base.",
            False,
            0,
        ),
        (
            "ai_suggestions",
            "AI Task Suggestions",
            "Enable AI powered suggestions in task launcher.",
            True,
            100,
        ),
        (
            "bulk_task_execution",
            "Bulk Task Execution",
            "Enable running tasks in bulk mode.",
            False,
            0,
        ),
        (
            "advanced_analytics",
            "Advanced Analytics Dashboard",
            "Enable advanced analytics dashboard widgets.",
            False,
            0,
        ),
        (
            "team_audit_log",
            "Team Audit Log",
            "Enable team level audit log visibility.",
            True,
            100,
        ),
        (
            "razorpay_subscriptions",
            "Razorpay Subscription Billing",
            "Enable Razorpay subscription billing flow.",
            True,
            100,
        ),
    ]

    for flag_key, display_name, description, is_enabled, rollout_percentage in flags:
        _upsert_record(
            FeatureFlag,
            {"flag_key": flag_key},
            {
                "display_name": display_name,
                "description": description,
                "is_enabled": is_enabled,
                "rollout_percentage": rollout_percentage,
                "enabled_org_ids": [],
            },
        )


def auto_seed_database() -> None:
    """Seed core platform data exactly once in an idempotent way."""

    from app.models import FeatureFlag as SeedFeatureFlag
    from app.models import Integration as SeedIntegration
    from app.models import Plan as SeedPlan
    from app.models import WorkflowTemplate as SeedWorkflowTemplate

    if SeedPlan.query.count() > 0:
        return

    try:
        _seed_plans()
        _seed_integrations()
        _seed_workflow_templates()
        _seed_feature_flags()
        db.session.commit()

        current_app.logger.info("Seeded %s plans", SeedPlan.query.count())
        current_app.logger.info("Seeded %s integrations", SeedIntegration.query.count())
        current_app.logger.info(
            "Seeded %s workflow templates", SeedWorkflowTemplate.query.count()
        )
        current_app.logger.info("Seeded %s feature flags", SeedFeatureFlag.query.count())
    except IntegrityError:
        db.session.rollback()
        current_app.logger.info(
            "Seed data initialization skipped due to concurrent startup race."
        )
    except Exception:
        db.session.rollback()
        raise


def create_app(config_name: str = "development") -> Flask:
    """Create and configure Flask application instance."""

    load_dotenv()
    _validate_required_environment_variables()

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        static_url_path="/static",
    )

    config_map: dict[str, type] = {
        "development": DevelopmentConfig,
        "testing": TestingConfig,
        "production": ProductionConfig,
    }
    config_class = config_map.get(config_name.lower(), DevelopmentConfig)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    init_oauth(app)

    celery_app = make_celery(app)
    celery_ext.conf.update(celery_app.conf)
    celery_ext.Task = celery_app.Task
    celery_ext.set_default()
    app.extensions["redis_client"] = redis.Redis.from_url(app.config["REDIS_URL"])
    google_api_key = app.config.get("GOOGLE_API_KEY")
    if google_api_key:
        app.extensions["gemini_client"] = genai.Client(api_key=google_api_key)

    _register_blueprints(app)

    webhook_endpoint_candidates = [
        "billing.webhook",
        "billing.billing_webhook",
        "billing.webhook_handler",
    ]
    for endpoint in webhook_endpoint_candidates:
        view_func = app.view_functions.get(endpoint)
        if view_func is not None:
            csrf.exempt(view_func)

    _register_error_handlers(app)

    auth_service = AuthService()
    notification_service = NotificationService()
    ist_tz = pytz.timezone("Asia/Kolkata")

    def _to_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _to_ist(value: datetime | None) -> datetime | None:
        normalized = _to_utc(value)
        if normalized is None:
            return None
        return normalized.astimezone(ist_tz)

    def get_user_org_cached(user_id: Any) -> Organization | None:
        """Return organization for user with five minute cache."""

        if not user_id:
            return None

        cache_key = f"user_org:{user_id}"

        try:
            cached_org_id = cache.get(cache_key)
            if cached_org_id == "none":
                return None

            if cached_org_id:
                try:
                    org_uuid = UUID(str(cached_org_id))
                except (TypeError, ValueError):
                    org_uuid = None

                if org_uuid is not None:
                    organization = db.session.get(Organization, org_uuid)
                    if organization and not organization.is_deleted:
                        return organization

            organization = auth_service.get_user_org(user_id)
            if organization is not None and not organization.is_deleted:
                cache.set(cache_key, str(organization.id), timeout=300)
                return organization

            cache.set(cache_key, "none", timeout=300)
            return None
        except Exception:
            return auth_service.get_user_org(user_id)

    def get_quota_usage(
        user_id: Any,
        org: Organization | None,
        plan: Plan | None = None,
    ) -> tuple[int, int, float]:
        """Return monthly quota usage values as used, limit, and percent."""

        if org is None:
            return 0, 0, 0.0

        now_utc = datetime.now(timezone.utc)
        month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_key = month_start.strftime("%Y%m")
        cache_key = f"quota_usage:{org.id}:{month_key}"

        try:
            cached_usage = cache.get(cache_key)
            if isinstance(cached_usage, dict):
                return (
                    int(cached_usage.get("used", 0)),
                    int(cached_usage.get("limit", 0)),
                    float(cached_usage.get("percent", 0.0)),
                )
        except Exception:
            pass

        try:
            active_plan = plan
            if active_plan is None and org.plan_id:
                active_plan = db.session.get(Plan, org.plan_id)

            quota_limit = int(active_plan.task_quota_monthly) if active_plan else 10
            quota_used = (
                UsageRecord.query.filter(
                    UsageRecord.org_id == org.id,
                    UsageRecord.usage_type == "task_run",
                    UsageRecord.recorded_at >= month_start,
                ).count()
            )

            if quota_limit <= 0:
                quota_percent = 0.0
            else:
                quota_percent = round(min((quota_used / quota_limit) * 100, 100), 1)

            cache.set(
                cache_key,
                {
                    "used": int(quota_used),
                    "limit": int(quota_limit),
                    "percent": float(quota_percent),
                },
                timeout=60,
            )
            return int(quota_used), int(quota_limit), float(quota_percent)
        except Exception:
            return 0, 0, 0.0

    def get_quota_percent(user_id: Any, org: Organization | None) -> float:
        """Return monthly quota usage percentage for template globals."""

        _used, _limit, percent = get_quota_usage(user_id, org)
        return float(percent)

    def get_next_plan(current_plan: Plan | None) -> Plan | None:
        """Return next higher priced active plan for upgrade prompts."""

        if current_plan is None:
            return None
        try:
            base_price = current_plan.price_monthly_inr if current_plan.price_monthly_inr >= 0 else 0
            return (
                Plan.query.filter(
                    Plan.is_active.is_(True),
                    Plan.price_monthly_inr > base_price,
                    Plan.price_monthly_inr > 0,
                )
                .order_by(Plan.price_monthly_inr.asc())
                .first()
            )
        except Exception:
            return None

    def get_recent_notifications(user_id: Any, org_id: Any) -> list[Notification]:
        """Return latest five non deleted notifications."""

        if not user_id or not org_id:
            return []

        try:
            return (
                Notification.query.filter_by(
                    user_id=user_id,
                    org_id=org_id,
                    is_deleted=False,
                )
                .order_by(Notification.created_at.desc())
                .limit(5)
                .all()
            )
        except Exception:
            return []

    @app.template_filter("timeago")
    def timeago_filter(value: datetime | None) -> str:
        """Render relative timestamps using IST time zone."""

        if value is None:
            return ""

        timestamp_ist = _to_ist(value)
        if timestamp_ist is None:
            return ""

        now_ist = datetime.now(ist_tz)
        delta = now_ist - timestamp_ist
        total_seconds = max(int(delta.total_seconds()), 0)

        if total_seconds < 60:
            return "just now"

        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"

        hours = minutes // 60
        if hours < 24:
            suffix = "hour" if hours == 1 else "hours"
            return f"{hours} {suffix} ago"

        days = hours // 24
        if days < 30:
            suffix = "day" if days == 1 else "days"
            return f"{days} {suffix} ago"

        return timestamp_ist.strftime("%b %d, %Y")

    @app.template_filter("duration_display")
    def duration_display_filter(seconds: int | float | None) -> str:
        """Render human-friendly duration strings."""

        if not seconds or seconds < 0:
            return "-"
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"

    @app.template_filter("format_inr")
    def format_inr_filter(value: int | float | None) -> str:
        """Render INR amounts with locale-style separators."""

        if value is None:
            return "0"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "0"

        return f"{numeric:,.0f}"

    @app.template_filter("task_category_display")
    def task_category_display_filter(task_type: str) -> str:
        """Map task_type values to category display labels."""

        from app.services.agent_runner import TASK_REGISTRY

        config = TASK_REGISTRY.get(task_type, {})
        return str(config.get("category_display", task_type))

    @app.template_filter("to_ist")
    def to_ist_filter(dt: datetime | None) -> str:
        """Render datetime in Asia Kolkata timezone."""

        if not dt:
            return ""

        ist = pytz.timezone("Asia/Kolkata")
        normalized = dt
        if normalized.tzinfo is None:
            normalized = pytz.utc.localize(normalized)
        return normalized.astimezone(ist).strftime("%d %b %Y, %I:%M %p IST")

    @app.template_filter("action_to_human")
    def action_to_human_filter(action: str | None) -> str:
        """Convert audit action keys to human readable labels."""

        normalized = (action or "").strip().lower()
        mapping = {
            "user.login": "Signed in",
            "user.logout": "Signed out",
            "user.registered": "Created an account",
            "user.email_verified": "Verified email address",
            "user.onboarding_completed": "Completed onboarding",
            "task.created": "Launched a new task",
            "task.completed": "Completed a task",
            "task.failed": "A task failed",
            "workflow.created": "Created a workflow",
            "workflow.updated": "Updated a workflow",
            "workflow.deleted": "Deleted a workflow",
            "billing.upgraded": "Upgraded billing plan",
            "billing.cancelled": "Cancelled subscription",
            "billing.payment_failed": "Payment failed",
        }

        if normalized in mapping:
            return mapping[normalized]

        if normalized.startswith("task."):
            return "Task activity updated"
        if normalized.startswith("workflow."):
            return "Workflow activity updated"
        if normalized.startswith("billing."):
            return "Billing activity updated"
        if normalized.startswith("user."):
            return "User activity updated"

        title = normalized.replace("_", " ").replace(".", " ").strip()
        return title.title() if title else "Activity updated"

    @app.template_filter("markdown_to_html")
    def markdown_to_html_filter(content: str | None) -> Markup:
        """Render markdown content to safe HTML for output pages."""

        if not content:
            return Markup("")

        html = markdown_lib.markdown(
            content,
            extensions=[
                "extra",
                "sane_lists",
                "tables",
                "fenced_code",
                "toc",
            ],
            output_format="html5",
        )
        return Markup(html)

    @app.context_processor
    def inject_template_globals() -> dict[str, Any]:
        """Inject global values into all templates."""

        base_values = {
            "current_year": datetime.now().year,
            "current_user": current_user,
        }

        if current_user.is_authenticated:
            organization = get_user_org_cached(current_user.id)

            plan = None
            if organization is not None and organization.plan_id:
                try:
                    plan = db.session.get(Plan, organization.plan_id)
                except Exception:
                    plan = None

            unread_count = notification_service.get_unread_count(
                current_user.id,
                organization.id if organization else None,
            )
            quota_used, quota_limit, quota_percent = get_quota_usage(
                current_user.id,
                organization,
                plan,
            )
            next_plan = get_next_plan(plan)
            recent_notifications = get_recent_notifications(
                current_user.id,
                organization.id if organization else None,
            )

            return {
                **base_values,
                "g_org": organization,
                "g_plan": plan,
                "g_unread_count": unread_count,
                "g_quota_percent": get_quota_percent(current_user.id, organization),
                "g_quota_used": quota_used,
                "g_quota_limit": quota_limit,
                "g_next_plan": next_plan,
                "g_recent_notifications": recent_notifications,
                "is_impersonating": bool(session.get("admin_impersonating_user_id")),
                "admin_impersonating_user_id": session.get("admin_impersonating_user_id"),
            }

        return {
            **base_values,
            "g_org": None,
            "g_plan": None,
            "g_unread_count": 0,
            "g_quota_percent": 0,
            "g_quota_used": 0,
            "g_quota_limit": 0,
            "g_next_plan": None,
            "g_recent_notifications": [],
            "is_impersonating": bool(session.get("admin_impersonating_user_id")),
            "admin_impersonating_user_id": session.get("admin_impersonating_user_id"),
        }

    @app.before_request
    def enforce_org_suspension() -> Any:
        """Block suspended organizations while allowing account recovery routes."""

        if not current_user.is_authenticated or current_user.is_admin:
            return None

        path = request.path or ""
        allowed_prefixes = (
            "/static/",
            "/auth/",
            "/help",
            "/status",
            "/privacy",
            "/terms",
            "/cookies",
            "/gdpr",
            "/support",
            "/admin/return-impersonation",
        )
        if path == "/" or any(path.startswith(prefix) for prefix in allowed_prefixes):
            return None

        organization = get_user_org_cached(current_user.id)
        if organization is None:
            return None

        suspended = bool((organization.settings_json or {}).get("suspended"))
        if not suspended:
            return None

        message = "Your organization has been suspended. Please contact support."
        if path.startswith("/api"):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": message,
                        "error": "organization_suspended",
                    }
                ),
                403,
            )

        flash(message, "danger")
        return render_template("errors/403.html", reason="suspended", error_message=message), 403

    @app.get("/health")
    def health() -> tuple[Any, int]:
        """Return application health status after DB and Redis checks."""

        db_status = "connected"
        redis_status = "connected"
        status_code = 200

        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"
            status_code = 503

        try:
            app.extensions["redis_client"].ping()
        except Exception:
            redis_status = "error"
            status_code = 503

        return (
            jsonify(
                {
                    "status": "ok",
                    "db": db_status,
                    "redis": redis_status,
                    "version": app.config.get("APP_VERSION", "1.0.0"),
                }
            ),
            status_code,
        )

    @app.cli.command("seed")
    @with_appcontext
    def seed_command() -> None:
        """Seed plans, integrations, workflow templates, and feature flags."""

        _seed_plans()
        _seed_integrations()
        _seed_workflow_templates()
        _seed_feature_flags()

        db.session.commit()
        click.echo("Seed data successfully inserted or updated.")

    @app.cli.command("create-admin")
    @with_appcontext
    def create_admin_command() -> None:
        """Create the first platform administrator account and organization."""

        email = click.prompt("Admin email", type=str)
        password = click.prompt(
            "Admin password",
            type=str,
            hide_input=True,
            confirmation_prompt=True,
        )
        first_name = click.prompt("First name", type=str)
        last_name = click.prompt("Last name", type=str)

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            raise click.ClickException("A user with this email already exists.")

        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role="admin",
            is_verified=True,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        enterprise_plan = Plan.query.filter_by(slug="enterprise").first()
        if enterprise_plan is None:
            raise click.ClickException("Enterprise plan not found. Run flask seed first.")

        organization = Organization.query.filter_by(slug="platform-admin").first()
        if organization is None:
            organization = Organization(
                name="Platform Admin",
                slug="platform-admin",
                owner_id=user.id,
                plan_id=enterprise_plan.id,
                seats_used=1,
                settings_json={},
            )
            db.session.add(organization)
            db.session.flush()
        else:
            organization.owner_id = user.id
            organization.plan_id = enterprise_plan.id

        membership = OrganizationMember.query.filter_by(
            org_id=organization.id,
            user_id=user.id,
        ).first()
        if membership is None:
            membership = OrganizationMember(
                org_id=organization.id,
                user_id=user.id,
                role="owner",
                joined_at=datetime.now(timezone.utc),
            )
            db.session.add(membership)

        db.session.commit()
        click.echo(f"Admin user created successfully with ID: {user.id}")

    return app
