"""Public marketing blueprint routes and content constants."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_mail import Message
from sqlalchemy import case, func

from app.extensions import db, mail
from app.forms.contact import ContactForm, EnterpriseInquiryForm
from app.models import AutomationTask, Integration, Plan, WorkflowTemplate
from app.utils.sanitizer import strip_sql_injection

public_bp = Blueprint("public", __name__)


@public_bp.get("/uploads/<category>/<filename>")
def serve_upload(category: str, filename: str):
    """Serve uploaded files from the uploads directory."""

    upload_folder = current_app.config.get("UPLOAD_FOLDER", "uploads")
    upload_root = os.path.abspath(upload_folder)
    
    category_safe = str(category).strip()
    filename_safe = str(filename).strip()
    
    if not category_safe or not filename_safe:
        abort(404)
    
    if "." not in filename_safe:
        abort(404)
    
    requested_path = os.path.abspath(os.path.join(upload_root, category_safe, filename_safe))
    
    if not requested_path.startswith(upload_root):
        abort(403)
    
    if not os.path.exists(requested_path) or not os.path.isfile(requested_path):
        abort(404)
    
    mime_type = "application/octet-stream"
    filename_lower = filename_safe.lower()
    if filename_lower.endswith(".png"):
        mime_type = "image/png"
    elif filename_lower.endswith((".jpg", ".jpeg")):
        mime_type = "image/jpeg"
    elif filename_lower.endswith(".webp"):
        mime_type = "image/webp"
    elif filename_lower.endswith(".gif"):
        mime_type = "image/gif"
    elif filename_lower.endswith(".pdf"):
        mime_type = "application/pdf"
    elif filename_lower.endswith((".csv", ".txt")):
        mime_type = "text/plain"
    elif filename_lower.endswith((".xls", ".xlsx")):
        mime_type = "application/vnd.ms-excel"
    elif filename_lower.endswith(".zip"):
        mime_type = "application/zip"
    
    try:
        return send_file(
            requested_path,
            as_attachment=False,
            mimetype=mime_type
        )
    except Exception:
        abort(500)


@dataclass
class ListPagination:
    """Simple pagination helper for in-memory collections."""

    items: list[Any]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        if self.per_page <= 0:
            return 0
        return ceil(self.total / self.per_page)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int:
        return max(self.page - 1, 1)

    @property
    def next_num(self) -> int:
        return min(self.page + 1, self.pages if self.pages else 1)

    def iter_pages(
        self,
        left_edge: int = 2,
        left_current: int = 2,
        right_current: int = 4,
        right_edge: int = 2,
    ) -> Iterable[int | None]:
        """Yield page numbers in the same shape as Flask-SQLAlchemy pagination."""

        if self.pages <= 0:
            return

        last = 0
        for num in range(1, self.pages + 1):
            is_left = num <= left_edge
            is_current = (self.page - left_current - 1) < num < (self.page + right_current)
            is_right = num > self.pages - right_edge
            if is_left or is_current or is_right:
                if last + 1 != num:
                    yield None
                yield num
                last = num


def _paginate_list(items: list[Any], page: int, per_page: int) -> ListPagination:
    safe_page = max(page, 1)
    safe_per_page = max(1, min(per_page, 100))
    total = len(items)
    start = (safe_page - 1) * safe_per_page
    end = start + safe_per_page
    return ListPagination(items=items[start:end], page=safe_page, per_page=safe_per_page, total=total)


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slugify_label(value: str) -> str:
    return value.lower().replace("&", "and").replace("/", "-").replace(" ", "-")


def _mail_default_recipient() -> str:
    sender = current_app.config.get("MAIL_DEFAULT_SENDER", "hello@agentflow.ai")
    if isinstance(sender, (list, tuple)):
        if len(sender) >= 2:
            return str(sender[1])
        if sender:
            return str(sender[0])
    return str(sender)


TASK_CATEGORIES: dict[str, dict[str, Any]] = {
    "web_research": {
        "name": "Web Research",
        "icon": "bi bi-globe2",
        "description": "Deep cross source research with verification and synthesis.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "file_creation": {
        "name": "File Creation & Management",
        "icon": "bi bi-file-earmark-richtext",
        "description": "Generate, structure, summarize, and maintain documents at scale.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "email_automation": {
        "name": "Email Automation",
        "icon": "bi bi-envelope-paper",
        "description": "Draft, classify, and optimize emails for speed and clarity.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "calendar_scheduling": {
        "name": "Calendar & Scheduling",
        "icon": "bi bi-calendar3",
        "description": "Automate event planning, reminders, and scheduling workflows.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "form_filling": {
        "name": "Form Filling & Data Entry",
        "icon": "bi bi-ui-checks-grid",
        "description": "Populate forms and records accurately from structured context.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "code_writing": {
        "name": "Code Writing & Debugging",
        "icon": "bi bi-code-square",
        "description": "Generate, refactor, and validate code across stacks.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "data_scraping": {
        "name": "Data Scraping & Extraction",
        "icon": "bi bi-database-fill-gear",
        "description": "Extract structured data from websites, reports, and listings.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "spreadsheet_automation": {
        "name": "Spreadsheet & Document Automation",
        "icon": "bi bi-table",
        "description": "Automate models, audits, and reporting in spreadsheets.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "api_integrations": {
        "name": "API Calls & Integrations",
        "icon": "bi bi-plug",
        "description": "Connect systems through APIs, webhooks, and transformations.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "shopping_ecommerce": {
        "name": "Shopping & E Commerce",
        "icon": "bi bi-cart-check",
        "description": "Find, compare, and monitor products and suppliers.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "social_media": {
        "name": "Social Media Automation",
        "icon": "bi bi-chat-square-heart",
        "description": "Plan content, monitor trends, and draft engagement responses.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "news_aggregation": {
        "name": "News & Content Aggregation",
        "icon": "bi bi-newspaper",
        "description": "Track, aggregate, and summarize industry updates in real time.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "travel_booking": {
        "name": "Travel Booking & Research",
        "icon": "bi bi-airplane",
        "description": "Research flights, hotels, visas, and itinerary options quickly.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "job_applications": {
        "name": "Job Application Automation",
        "icon": "bi bi-briefcase",
        "description": "Tailor applications, prep interviews, and track opportunities.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "customer_support": {
        "name": "Customer Support Automation",
        "icon": "bi bi-headset",
        "description": "Automate ticketing flows, responses, and help center operations.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "financial_data": {
        "name": "Financial Data Research",
        "icon": "bi bi-graph-up-arrow",
        "description": "Build financial insights, research briefs, and variance analysis.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "legal_documents": {
        "name": "Legal Document Drafting",
        "icon": "bi bi-file-earmark-text",
        "description": "Create legal drafts with consistent structure and language.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "medical_research": {
        "name": "Medical Research",
        "icon": "bi bi-heart-pulse",
        "description": "Support healthcare research, coding, and eligibility screening.",
        "task_count": 0,
        "color": "var(--danger)",
    },
    "education_tutoring": {
        "name": "Education & Tutoring",
        "icon": "bi bi-mortarboard",
        "description": "Generate plans, lessons, practice sets, and learning content.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "image_media": {
        "name": "Image & Media Research",
        "icon": "bi bi-image",
        "description": "Research visual assets, media trends, and attribution details.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "database_management": {
        "name": "Database Querying & Management",
        "icon": "bi bi-hdd-network",
        "description": "Design schemas, optimize queries, and automate ETL tasks.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "it_sysadmin": {
        "name": "IT & System Administration",
        "icon": "bi bi-server",
        "description": "Support infrastructure, policy, and operations workflows.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "project_management": {
        "name": "Project Management Automation",
        "icon": "bi bi-kanban",
        "description": "Automate plans, risk logs, status reports, and retrospectives.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "crm_sales": {
        "name": "CRM & Sales Automation",
        "icon": "bi bi-people",
        "description": "Streamline sales sequencing, enrichment, and pipeline reviews.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "hr_recruitment": {
        "name": "HR & Recruitment Automation",
        "icon": "bi bi-person-vcard",
        "description": "Automate hiring workflows and talent operations.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "marketing_seo": {
        "name": "Marketing & SEO",
        "icon": "bi bi-megaphone",
        "description": "Create campaigns, optimize SEO, and repurpose content.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "multi_step_workflows": {
        "name": "Multi Step Complex Workflows",
        "icon": "bi bi-diagram-3",
        "description": "Run end to end automation chains across multiple systems.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "real_estate": {
        "name": "Real Estate Research & Automation",
        "icon": "bi bi-house-door",
        "description": "Automate market research and property operations tasks.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "supply_chain": {
        "name": "Supply Chain & Logistics Research",
        "icon": "bi bi-truck",
        "description": "Optimize supplier qualification and shipping decision support.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "government_compliance": {
        "name": "Government & Compliance Form Automation",
        "icon": "bi bi-building-check",
        "description": "Automate filings, compliance calendars, and regulatory drafts.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "personal_productivity": {
        "name": "Personal Productivity & Life Automation",
        "icon": "bi bi-lightning-charge",
        "description": "Streamline personal planning, routines, and digital organization.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "finance_banking": {
        "name": "Finance & Banking",
        "icon": "bi bi-bank",
        "description": "Automate reconciliation, reporting, risk, and treasury workflows.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "healthcare_medicine": {
        "name": "Healthcare & Medicine",
        "icon": "bi bi-hospital",
        "description": "Modernize clinical, claims, and administrative healthcare operations.",
        "task_count": 0,
        "color": "var(--danger)",
    },
    "legal_enterprise": {
        "name": "Legal Enterprise",
        "icon": "bi bi-shield-check",
        "description": "Automate contract, docket, compliance, and legal operations.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "education_enterprise": {
        "name": "Education Enterprise",
        "icon": "bi bi-journal-bookmark",
        "description": "Automate SIS, LMS, admissions, and student communication.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "ecommerce_retail": {
        "name": "E Commerce & Retail",
        "icon": "bi bi-bag-check",
        "description": "Automate order, inventory, pricing, and customer lifecycle tasks.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "manufacturing": {
        "name": "Manufacturing & Supply Chain",
        "icon": "bi bi-gear-wide-connected",
        "description": "Optimize production, maintenance, and demand planning workflows.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "nonprofit": {
        "name": "Nonprofit",
        "icon": "bi bi-heart",
        "description": "Automate fundraising, volunteer, and impact reporting operations.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "research_academia": {
        "name": "Research & Academia",
        "icon": "bi bi-book",
        "description": "Accelerate literature reviews, protocols, and grant workflows.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "human_resources": {
        "name": "Human Resources",
        "icon": "bi bi-person-workspace",
        "description": "Scale HR operations across hiring, benefits, and workforce planning.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "marketing_advertising": {
        "name": "Marketing and Advertising",
        "icon": "bi bi-broadcast",
        "description": "Automate campaigns, attribution, and audience orchestration.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "it_software_dev": {
        "name": "IT and Software Development",
        "icon": "bi bi-cpu",
        "description": "Run DevOps, security, and engineering automations at scale.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "customer_service": {
        "name": "Customer Service",
        "icon": "bi bi-telephone-forward",
        "description": "Automate multichannel support, SLA tracking, and service alerts.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "government_services": {
        "name": "Government and Public Services",
        "icon": "bi bi-building",
        "description": "Automate casework, permits, grants, and public records workflows.",
        "task_count": 0,
        "color": "var(--warning)",
    },
    "media_entertainment": {
        "name": "Media and Entertainment",
        "icon": "bi bi-film",
        "description": "Automate publishing, moderation, rights, and localization operations.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "travel_hospitality": {
        "name": "Travel and Hospitality",
        "icon": "bi bi-compass",
        "description": "Automate reservations, pricing, channels, and loyalty workflows.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "food_restaurant": {
        "name": "Food and Restaurant",
        "icon": "bi bi-cup-hot",
        "description": "Optimize POS analytics, delivery, and kitchen cost operations.",
        "task_count": 0,
        "color": "var(--danger)",
    },
    "agriculture": {
        "name": "Agriculture",
        "icon": "bi bi-flower1",
        "description": "Automate farm data, forecasting, and equipment intelligence.",
        "task_count": 0,
        "color": "var(--success)",
    },
    "logistics_transportation": {
        "name": "Logistics and Transportation",
        "icon": "bi bi-sign-turn-right",
        "description": "Automate freight, routing, compliance, and fleet operations.",
        "task_count": 0,
        "color": "var(--info)",
    },
    "insurance": {
        "name": "Insurance",
        "icon": "bi bi-shield-lock",
        "description": "Automate underwriting, claims, policy, and reserve workflows.",
        "task_count": 0,
        "color": "var(--primary)",
    },
    "telecommunications": {
        "name": "Telecommunications",
        "icon": "bi bi-broadcast-pin",
        "description": "Automate provisioning, billing, churn, and network operations.",
        "task_count": 0,
        "color": "var(--accent)",
    },
    "energy_utilities": {
        "name": "Energy and Utilities",
        "icon": "bi bi-lightning",
        "description": "Automate metering, outage, demand response, and compliance tasks.",
        "task_count": 0,
        "color": "var(--warning)",
    },
}


def _ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{i}" for i in range(1, count + 1)]


TASK_GROUP_DEFINITIONS: list[dict[str, Any]] = [
    {
        "ids": _ids("1.", 15),
        "category": "web_research",
        "requires_web_search": True,
        "icon": "bi bi-globe2",
        "names": [
            "Deep Multi Source Research Synthesis",
            "Real Time Product Price Comparison",
            "Academic Literature Search and Summarization",
            "Company Background Research",
            "Regulatory and Compliance Research",
            "Technology Stack Discovery",
            "Wikipedia Research and Cross Referencing",
            "Forum and Community Sentiment Extraction",
            "Job Market Research",
            "Real Estate Listing Research",
            "Event and Conference Research",
            "News Aggregation and Briefing Generation",
            "Social Proof and Review Research",
            "Patent Research",
            "Grant and Funding Opportunity Research",
        ],
    },
    {
        "ids": _ids("2.", 10),
        "category": "file_creation",
        "requires_web_search": False,
        "icon": "bi bi-file-earmark-richtext",
        "names": [
            "Multi Section Report Generation",
            "Template Population from Data",
            "Bulk File Renaming and Organization",
            "Document Summarization",
            "Resume and Cover Letter Generation",
            "Contract Redlining and Markup Drafting",
            "Code File Documentation Generation",
            "Presentation Outline and Slide Script Creation",
            "Policy and Procedure Document Drafting",
            "Data Driven Narrative Writing",
        ],
    },
    {
        "ids": _ids("3.", 10),
        "category": "email_automation",
        "requires_web_search": False,
        "icon": "bi bi-envelope-paper",
        "names": [
            "Inbox Triage and Priority Classification",
            "Personalized Cold Email Drafting at Scale",
            "Email Thread Summarization",
            "Meeting Follow Up Email Drafting",
            "Customer Complaint Response Drafting",
            "Email Campaign Copy Writing",
            "Vendor and Supplier Correspondence",
            "Legal Notice and Demand Letter Drafting",
            "Unsubscribe and Inbox Cleaning Assistance",
            "Proposal and Quote Email Writing",
        ],
    },
    {
        "ids": _ids("4.", 5),
        "category": "calendar_scheduling",
        "requires_web_search": False,
        "icon": "bi bi-calendar3",
        "names": [
            "Meeting Scheduling from Availability Context",
            "Calendar Event Creation from Email or Notes",
            "Weekly Agenda Preparation",
            "Recurring Event and Deadline Tracking",
            "Interview Scheduling Coordination",
        ],
    },
    {
        "ids": _ids("5.", 6),
        "category": "form_filling",
        "requires_web_search": False,
        "icon": "bi bi-ui-checks-grid",
        "names": [
            "Web Form Auto Population",
            "Government Form Assistance and Population",
            "CRM Data Entry from Meeting Notes",
            "Insurance Claims Data Preparation",
            "Survey Response Data Consolidation",
            "E commerce Order Form Completion",
        ],
    },
    {
        "ids": _ids("6.", 15),
        "category": "code_writing",
        "requires_web_search": False,
        "icon": "bi bi-code-square",
        "names": [
            "Full Application Code Generation",
            "Bug Identification and Fix Generation",
            "Code Refactoring for Readability and Performance",
            "Unit Test Generation",
            "SQL Query Writing and Optimization",
            "API Integration Code Writing",
            "Shell Script and Automation Script Generation",
            "Data Analysis Script Writing",
            "Code Explanation and Documentation",
            "Infrastructure as Code Generation",
            "Regex Pattern Generation",
            "Code Migration Between Languages or Frameworks",
            "Security Vulnerability Review",
            "Environment Setup and Dependency Configuration",
            "Algorithm Design and Implementation",
        ],
    },
    {
        "ids": _ids("7.", 10),
        "category": "data_scraping",
        "requires_web_search": True,
        "icon": "bi bi-database-fill-gear",
        "names": [
            "Product Listing Extraction",
            "Contact Information Extraction from Directories",
            "News Article Extraction and Structuring",
            "Job Posting Extraction and Aggregation",
            "Financial Data Extraction from Reports",
            "Real Estate Data Extraction",
            "Review and Rating Extraction",
            "Scholarly Citation Extraction",
            "Government Data Extraction",
            "Event and Agenda Data Extraction",
        ],
    },
    {
        "ids": _ids("8.", 8),
        "category": "spreadsheet_automation",
        "requires_web_search": False,
        "icon": "bi bi-table",
        "names": [
            "Financial Model Building",
            "Dashboard Creation from Raw Data",
            "Data Cleaning and Deduplication",
            "Formula Audit and Error Correction",
            "Contract and Document Comparison",
            "Mail Merge Document Generation",
            "Report Compilation from Multiple Sources",
            "Budget vs Actuals Analysis",
        ],
    },
    {
        "ids": _ids("9.", 7),
        "category": "api_integrations",
        "requires_web_search": False,
        "icon": "bi bi-plug",
        "names": [
            "REST API Data Retrieval and Processing",
            "Webhook Configuration Assistance",
            "GraphQL Query Writing",
            "Authentication Flow Implementation",
            "API Response Schema Documentation",
            "Third Party Service Configuration Code",
            "Data Transformation Between API Formats",
        ],
    },
    {
        "ids": _ids("10.", 7),
        "category": "shopping_ecommerce",
        "requires_web_search": True,
        "icon": "bi bi-cart-check",
        "names": [
            "Multi Site Price Tracking and Alerting Logic",
            "Product Availability Checking",
            "Coupon and Promo Code Research",
            "Product Specification Comparison",
            "Wholesale Supplier Discovery",
            "Amazon Product Research",
            "Subscription Management Research",
        ],
    },
    {
        "ids": _ids("11.", 7),
        "category": "social_media",
        "requires_web_search": True,
        "icon": "bi bi-chat-square-heart",
        "names": [
            "Social Media Content Calendar Creation",
            "Post Copy Writing for Multiple Platforms",
            "Competitor Social Media Analysis",
            "Hashtag Research",
            "Influencer Research and List Building",
            "Community Post and Response Drafting",
            "Social Listening Summary",
        ],
    },
    {
        "ids": _ids("12.", 6),
        "category": "news_aggregation",
        "requires_web_search": True,
        "icon": "bi bi-newspaper",
        "names": [
            "Industry Newsletter Compilation",
            "Earnings and Financial News Aggregation",
            "Regulatory Filing and Announcement Monitoring",
            "Research Paper Alert Aggregation",
            "Competitor Blog and Content Monitoring",
            "Social News Trend Identification",
        ],
    },
    {
        "ids": _ids("13.", 7),
        "category": "travel_booking",
        "requires_web_search": True,
        "icon": "bi bi-airplane",
        "names": [
            "Flight Option Research",
            "Hotel Research and Comparison",
            "Visa and Entry Requirement Research",
            "Travel Itinerary Building",
            "Travel Insurance Research",
            "Ground Transportation Research",
            "Currency and Cost of Living Research",
        ],
    },
    {
        "ids": _ids("14.", 7),
        "category": "job_applications",
        "requires_web_search": True,
        "icon": "bi bi-briefcase",
        "names": [
            "Job Description Analysis and Matching",
            "Resume Customization Per Role",
            "Cover Letter Generation",
            "LinkedIn Profile Optimization",
            "Application Tracking Sheet Creation",
            "Interview Preparation Brief",
            "Salary Research and Negotiation Brief",
        ],
    },
    {
        "ids": _ids("15.", 7),
        "category": "customer_support",
        "requires_web_search": False,
        "icon": "bi bi-headset",
        "names": [
            "FAQ Knowledge Base Drafting",
            "Support Ticket Classification and Routing",
            "Canned Response Library Creation",
            "Escalation Summary Drafting",
            "Customer Satisfaction Survey Analysis",
            "Help Center Article Writing",
            "Chatbot Script and Flow Writing",
        ],
    },
    {
        "ids": _ids("16.", 7),
        "category": "financial_data",
        "requires_web_search": True,
        "icon": "bi bi-graph-up-arrow",
        "names": [
            "Equity Research Report Drafting",
            "Financial Statement Analysis",
            "Macro Economic Data Research",
            "Crypto and DeFi Market Research",
            "Tax Law Research",
            "Budget Variance Report Writing",
            "M and A Target Research",
        ],
    },
    {
        "ids": _ids("17.", 8),
        "category": "legal_documents",
        "requires_web_search": False,
        "icon": "bi bi-file-earmark-text",
        "names": [
            "NDA Drafting",
            "Service Agreement Drafting",
            "Terms of Service and Privacy Policy Drafting",
            "Employment Offer Letter Drafting",
            "Cease and Desist Letter Drafting",
            "Partnership Agreement Outline",
            "Demand Letter for Unpaid Invoices",
            "GDPR Data Subject Request Response Drafting",
        ],
    },
    {
        "ids": _ids("18.", 7),
        "category": "medical_research",
        "requires_web_search": True,
        "icon": "bi bi-heart-pulse",
        "names": [
            "Clinical Trial Research",
            "Drug Interaction Research",
            "Medical Literature Summarization",
            "Symptom and Differential Diagnosis Research",
            "Treatment Guideline Research",
            "Health Insurance Coverage Research",
            "Medical Device Regulatory Research",
        ],
    },
    {
        "ids": _ids("19.", 8),
        "category": "education_tutoring",
        "requires_web_search": False,
        "icon": "bi bi-mortarboard",
        "names": [
            "Personalized Study Plan Generation",
            "Practice Problem Generation",
            "Essay Feedback and Grading",
            "Concept Explanation at Multiple Levels",
            "Reading Comprehension Question Generation",
            "Vocabulary and Definition Research",
            "Research Paper Outline and Draft",
            "Lesson Plan Generation",
        ],
    },
    {
        "ids": _ids("20.", 5),
        "category": "image_media",
        "requires_web_search": True,
        "icon": "bi bi-image",
        "names": [
            "Stock Image Search and Curation",
            "Brand Asset Competitive Analysis",
            "Icon and Illustration Library Research",
            "Video Content Research",
            "Image Metadata and Attribution Research",
        ],
    },
    {
        "ids": _ids("21.", 6),
        "category": "database_management",
        "requires_web_search": False,
        "icon": "bi bi-hdd-network",
        "names": [
            "Database Schema Design",
            "SQL Query Generation for Analytics",
            "Data Migration Script Writing",
            "Index Optimization Recommendation",
            "ETL Pipeline Code Writing",
            "NoSQL Data Model Design",
        ],
    },
    {
        "ids": _ids("22.", 7),
        "category": "it_sysadmin",
        "requires_web_search": False,
        "icon": "bi bi-server",
        "names": [
            "Infrastructure Documentation Writing",
            "Network Diagram Description and Design",
            "Log Analysis and Error Diagnosis",
            "Security Policy Drafting",
            "CI and CD Pipeline Configuration",
            "Cloud Cost Optimization Research",
            "Container and Orchestration Configuration",
        ],
    },
    {
        "ids": _ids("23.", 6),
        "category": "project_management",
        "requires_web_search": False,
        "icon": "bi bi-kanban",
        "names": [
            "Project Plan Generation",
            "Risk Register Creation",
            "Status Report Writing",
            "Meeting Agenda Preparation",
            "RACI Matrix Creation",
            "Retrospective Facilitation Framework",
        ],
    },
    {
        "ids": _ids("24.", 7),
        "category": "crm_sales",
        "requires_web_search": True,
        "icon": "bi bi-people",
        "names": [
            "Lead Scoring Framework Development",
            "Sales Sequence Script Writing",
            "Account Research Briefing",
            "Competitive Battle Card Creation",
            "Sales Email Personalization at Scale",
            "CRM Data Enrichment",
            "Pipeline Review Report",
        ],
    },
    {
        "ids": _ids("25.", 7),
        "category": "hr_recruitment",
        "requires_web_search": False,
        "icon": "bi bi-person-vcard",
        "names": [
            "Job Description Writing",
            "Interview Question Bank Creation",
            "Candidate Screening Criteria Development",
            "Onboarding Document Package",
            "Performance Review Template Creation",
            "Employee Survey Design",
            "Compensation Benchmarking Research",
        ],
    },
    {
        "ids": _ids("26.", 10),
        "category": "marketing_seo",
        "requires_web_search": True,
        "icon": "bi bi-megaphone",
        "names": [
            "Keyword Research and Clustering",
            "SEO Optimized Blog Post Writing",
            "Technical SEO Audit Preparation",
            "Competitor Content Gap Analysis",
            "Ad Copy Writing",
            "Landing Page Copy Writing",
            "Email Marketing A and B Test Variant Creation",
            "Marketing Brief Writing",
            "Content Repurposing Across Formats",
            "Influencer Outreach Email Writing",
        ],
    },
    {
        "ids": _ids("27.", 5),
        "category": "multi_step_workflows",
        "requires_web_search": True,
        "icon": "bi bi-diagram-3",
        "names": [
            "End to End Lead Research and Outreach Workflow",
            "Competitive Intelligence Report Production",
            "Research to Writing to Publish Workflow",
            "RFP Response Assembly",
            "Due Diligence Checklist Completion",
        ],
    },
    {
        "ids": _ids("28.", 5),
        "category": "real_estate",
        "requires_web_search": True,
        "icon": "bi bi-house-door",
        "names": [
            "Comparable Sales Analysis",
            "Rental Market Research",
            "Neighborhood Research Report",
            "Zoning and Permit Research",
            "HOA Research",
        ],
    },
    {
        "ids": _ids("29.", 4),
        "category": "supply_chain",
        "requires_web_search": True,
        "icon": "bi bi-truck",
        "names": [
            "Supplier Qualification Research",
            "Shipping Rate Comparison",
            "Import and Export Regulation Research",
            "Logistics Technology Vendor Research",
        ],
    },
    {
        "ids": _ids("30.", 5),
        "category": "government_compliance",
        "requires_web_search": True,
        "icon": "bi bi-building-check",
        "names": [
            "Business License Application Assistance",
            "Tax Form Preparation Support",
            "Compliance Calendar Building",
            "Grant Application Drafting",
            "Regulatory Comment Letter Drafting",
        ],
    },
    {
        "ids": _ids("31.", 10),
        "category": "personal_productivity",
        "requires_web_search": True,
        "icon": "bi bi-lightning-charge",
        "names": [
            "Personal Finance Research and Budgeting",
            "Recipe Scaling and Meal Planning",
            "Book and Movie Research and Recommendation",
            "Event Planning Research",
            "Language Learning Material Creation",
            "Podcast and Video Content Research",
            "Nonprofit Impact Report Drafting",
            "Scientific Experiment Design Assistance",
            "SOP Writing",
            "Franchise Research",
        ],
    },
    {
        "ids": _ids("F", 20),
        "category": "finance_banking",
        "industry": "finance",
        "requires_web_search": False,
        "icon": "bi bi-bank",
        "names": [
            "Automated Account Reconciliation",
            "Automated Invoice Processing and Accounts Payable",
            "Automated Accounts Receivable and Collections",
            "Loan Origination Processing Automation",
            "Regulatory Reporting Automation",
            "Anti Money Laundering Transaction Monitoring",
            "KYC Document Verification",
            "Trade Settlement Automation",
            "Algorithmic Trading Execution",
            "Automated Financial Close Process",
            "Expense Report Processing",
            "Credit Scoring Model Execution",
            "Portfolio Rebalancing Automation",
            "Bank Statement Data Extraction",
            "Treasury Cash Position Reporting",
            "Fraud Detection and Card Alert Automation",
            "Tax Calculation and Filing Automation",
            "Payroll Processing Automation",
            "Insurance Premium Calculation",
            "Audit Trail and Transaction Log Automation",
        ],
    },
    {
        "ids": _ids("H", 17),
        "category": "healthcare_medicine",
        "industry": "healthcare",
        "requires_web_search": False,
        "icon": "bi bi-hospital",
        "names": [
            "Electronic Health Record Data Entry Automation",
            "Prior Authorization Automation",
            "Claims Processing and Adjudication",
            "Appointment Scheduling and Reminder Automation",
            "Lab Result Distribution Automation",
            "Medication Dispensing Automation",
            "Patient Discharge Summary Generation",
            "Population Health Management Outreach",
            "Medical Coding Automation",
            "Drug Interaction and Allergy Checking",
            "Clinical Trial Eligibility Screening",
            "Radiology Report Generation Assistance",
            "Revenue Cycle Denial Management",
            "Patient Intake Form Automation",
            "HIPAA Compliance Monitoring",
            "Pharmacy Benefit Management Processing",
            "Surgical Case Scheduling and Resource Allocation",
        ],
    },
    {
        "ids": _ids("LE", 12),
        "category": "legal_enterprise",
        "industry": "legal",
        "requires_web_search": False,
        "icon": "bi bi-shield-check",
        "names": [
            "Contract Analysis and Risk Flagging",
            "Contract Lifecycle Management",
            "Legal Document Review eDiscovery",
            "Legal Research Automation",
            "Regulatory Change Monitoring",
            "IP Portfolio Management Automation",
            "Due Diligence Data Room Processing",
            "Billing and Time Entry Automation",
            "Pleading and Motion Drafting Assistance",
            "Court Deadline and Docket Management",
            "Compliance Document Generation",
            "E Signature Workflow Automation",
        ],
    },
    {
        "ids": _ids("ED", 12),
        "category": "education_enterprise",
        "industry": "education",
        "requires_web_search": False,
        "icon": "bi bi-journal-bookmark",
        "names": [
            "Student Information System Data Management",
            "Learning Management System Content Deployment",
            "Automated Grading for Objective Assessments",
            "Plagiarism Detection",
            "Student Communication Automation",
            "Admissions Application Processing",
            "Adaptive Learning Content Delivery",
            "Financial Aid Processing",
            "Attendance Tracking and Reporting",
            "Course Recommendation Generation",
            "Credential and Certificate Issuance",
            "Library System Automation",
        ],
    },
    {
        "ids": _ids("EC", 14),
        "category": "ecommerce_retail",
        "industry": "ecommerce",
        "requires_web_search": False,
        "icon": "bi bi-bag-check",
        "names": [
            "Order Management and Fulfillment Automation",
            "Inventory Level Monitoring and Reorder Automation",
            "Dynamic Pricing Automation",
            "Product Catalog Data Enrichment",
            "Customer Segmentation and Targeting",
            "Cart Abandonment Recovery Automation",
            "Returns and Refunds Processing",
            "Fraud Detection and Order Risk Scoring",
            "Personalized Product Recommendation Engine",
            "Supplier EDI Integration and PO Processing",
            "Loyalty Program Management",
            "Review and UGC Collection Automation",
            "Marketplace Listing Syndication",
            "Tax Calculation at Checkout",
        ],
    },
    {
        "ids": _ids("M", 13),
        "category": "manufacturing",
        "industry": "manufacturing",
        "requires_web_search": False,
        "icon": "bi bi-gear-wide-connected",
        "names": [
            "ERP Production Order Automation",
            "MRP Run Automation",
            "Quality Control Inspection Data Collection",
            "Predictive Maintenance Scheduling",
            "Shop Floor Data Collection",
            "Supplier Performance Monitoring",
            "Demand Forecasting Automation",
            "Bill of Materials Management",
            "Warehouse Management System Automation",
            "Automated Guided Vehicle Coordination",
            "Dispatch and Route Optimization",
            "EDI Order Processing Automation",
            "ISO and Regulatory Document Control",
        ],
    },
    {
        "ids": _ids("NP", 6),
        "category": "nonprofit",
        "industry": "nonprofit",
        "requires_web_search": False,
        "icon": "bi bi-heart",
        "names": [
            "Donor Management and Giving History Tracking",
            "Online Fundraising Campaign Automation",
            "Grant Reporting Automation",
            "Volunteer Management Automation",
            "Impact Metrics Collection and Reporting",
            "Membership Renewal Automation",
        ],
    },
    {
        "ids": _ids("RA", 9),
        "category": "research_academia",
        "industry": "research-academia",
        "requires_web_search": False,
        "icon": "bi bi-book",
        "names": [
            "Literature Review Automation",
            "Systematic Review and Meta Analysis Support",
            "Research Data Management",
            "Lab Instrument Data Capture",
            "Statistical Analysis Pipeline Automation",
            "Academic Publishing Submission Tracking",
            "IRB Protocol Management",
            "Citation and Bibliography Management",
            "Research Grant Application Tracking",
        ],
    },
    {
        "ids": _ids("PP", 15),
        "category": "personal_productivity",
        "requires_web_search": False,
        "icon": "bi bi-lightning-charge",
        "names": [
            "Email Inbox Automation and Filtering",
            "Personal Finance Aggregation and Tracking",
            "Bill Payment Automation",
            "Calendar and Task Integration",
            "Smart Home Automation",
            "Health and Fitness Tracking",
            "Subscription Tracking and Management",
            "Travel Itinerary Parsing and Organization",
            "Password and Credential Management",
            "News and Reading Digest Curation",
            "Social Media Archiving",
            "File Organization and Cloud Sync",
            "Shopping Price Drop Alert",
            "Meeting Transcription and Summary",
            "Backup Automation for Personal Files",
        ],
    },
    {
        "ids": _ids("RE", 10),
        "category": "real_estate",
        "industry": "real-estate",
        "requires_web_search": False,
        "icon": "bi bi-house-door",
        "names": [
            "MLS Data Synchronization",
            "Lease Administration Automation",
            "Tenant Rent Collection and Late Fee Processing",
            "Maintenance Request Routing and Tracking",
            "Property Listing Syndication",
            "Tenant Screening Automation",
            "Property Tax Assessment Monitoring",
            "Appraisal Data Management",
            "Commercial Real Estate Lease Abstraction",
            "Investor Reporting Automation",
        ],
    },
    {
        "ids": _ids("HR", 12),
        "category": "human_resources",
        "industry": "hr",
        "requires_web_search": False,
        "icon": "bi bi-person-workspace",
        "names": [
            "Applicant Tracking System Automation",
            "Resume Parsing and Screening",
            "Onboarding Workflow Automation",
            "Benefits Enrollment Processing",
            "Performance Management Cycle Automation",
            "Time and Attendance Tracking",
            "Leave Management Automation",
            "Employee Separation and Offboarding Automation",
            "Compensation Planning and Modeling",
            "Learning and Development Assignment Automation",
            "Employee Survey Distribution and Analysis",
            "Headcount and Workforce Planning Reporting",
        ],
    },
    {
        "ids": _ids("MA", 12),
        "category": "marketing_advertising",
        "industry": "marketing",
        "requires_web_search": False,
        "icon": "bi bi-broadcast",
        "names": [
            "Marketing Automation Workflow Execution",
            "Lead Scoring and Lifecycle Stage Updates",
            "Ad Campaign Bid Management",
            "SEO Rank Tracking and Reporting",
            "Content Publishing Scheduling",
            "UTM Parameter Management and Attribution",
            "A and B Test Automation and Statistical Significance Monitoring",
            "Programmatic Ad Buying",
            "Marketing Performance Dashboard Automation",
            "Influencer Campaign Tracking",
            "Customer Journey Orchestration",
            "Social Listening and Sentiment Monitoring",
        ],
    },
    {
        "ids": _ids("IT", 15),
        "category": "it_software_dev",
        "industry": "it-software",
        "requires_web_search": False,
        "icon": "bi bi-cpu",
        "names": [
            "Infrastructure Provisioning IaC",
            "CI and CD Pipeline Execution",
            "Application Performance Monitoring and Alerting",
            "Incident Management and Runbook Automation",
            "Security Patch Management",
            "User Access Provisioning and Deprovisioning",
            "Backup and Disaster Recovery Automation",
            "Log Aggregation and SIEM Automation",
            "Software Testing Automation",
            "Container Orchestration and Auto Scaling",
            "DNS and SSL Certificate Management",
            "Database Backup and Failover Automation",
            "ITSM Ticket Routing and Resolution",
            "Configuration Management and Drift Detection",
            "API Gateway Management and Rate Limiting",
        ],
    },
    {
        "ids": _ids("CS", 10),
        "category": "customer_service",
        "industry": "customer-service",
        "requires_web_search": False,
        "icon": "bi bi-telephone-forward",
        "names": [
            "Chatbot Conversational AI Deployment",
            "Ticket Auto Classification and Routing",
            "Suggested Response Generation",
            "SLA Monitoring and Escalation",
            "Customer Satisfaction Survey Triggering",
            "Knowledge Base Article Publishing Workflow",
            "IVR Call Routing",
            "Agent Performance Scorecard Automation",
            "Order Status Inquiry Automation",
            "Proactive Service Alert Notifications",
        ],
    },
    {
        "ids": _ids("G", 10),
        "category": "government_services",
        "industry": "government",
        "requires_web_search": False,
        "icon": "bi bi-building",
        "names": [
            "Benefits Eligibility Determination Automation",
            "Permit Application Processing",
            "Tax Assessment and Collection System Automation",
            "311 Request Management",
            "Court Case Management Automation",
            "Election and Voter Registration Management",
            "Grant Management and Compliance Reporting",
            "FOIA Request Processing",
            "Public Records and Document Management",
            "Social Services Case Management",
        ],
    },
    {
        "ids": _ids("ME", 9),
        "category": "media_entertainment",
        "industry": "media-entertainment",
        "requires_web_search": False,
        "icon": "bi bi-film",
        "names": [
            "Content Metadata Management",
            "Video Transcription and Captioning",
            "Content Moderation Automation",
            "Royalty Calculation and Distribution",
            "Ad Insertion and Dynamic Ad Insertion",
            "Content Delivery Network Management",
            "Subtitle and Localization Workflow Automation",
            "Publishing Workflow Automation",
            "Programmatic Content Licensing",
        ],
    },
    {
        "ids": _ids("TH", 8),
        "category": "travel_hospitality",
        "industry": "travel-hospitality",
        "requires_web_search": False,
        "icon": "bi bi-compass",
        "names": [
            "Central Reservation System Management",
            "Revenue Management and Dynamic Pricing",
            "Guest Communication Automation",
            "GDS Content Management",
            "OTA Channel Management",
            "Housekeeping Workflow Management",
            "Food and Beverage Inventory Management",
            "Loyalty Program Management and Communication",
        ],
    },
    {
        "ids": _ids("FR", 7),
        "category": "food_restaurant",
        "industry": "food-restaurant",
        "requires_web_search": False,
        "icon": "bi bi-cup-hot",
        "names": [
            "Point of Sale Data Reporting",
            "Menu Engineering Analysis",
            "Online Ordering and Delivery Integration",
            "Food Cost and Recipe Costing Automation",
            "Staff Scheduling Automation",
            "Vendor Invoice Processing",
            "Health Inspection Compliance Documentation",
        ],
    },
    {
        "ids": _ids("AG", 7),
        "category": "agriculture",
        "industry": "agriculture",
        "requires_web_search": False,
        "icon": "bi bi-flower1",
        "names": [
            "Precision Agriculture Data Collection and Analysis",
            "Crop Yield Prediction Modeling",
            "Irrigation Scheduling Automation",
            "Livestock Monitoring and Health Alert",
            "Farm Management Information System Record Keeping",
            "Commodity Price Monitoring and Alerting",
            "Agricultural Equipment Telematics",
        ],
    },
    {
        "ids": _ids("LT", 8),
        "category": "logistics_transportation",
        "industry": "logistics",
        "requires_web_search": False,
        "icon": "bi bi-sign-turn-right",
        "names": [
            "Freight Quote Automation",
            "Shipment Tracking and Visibility",
            "Customs Documentation Automation",
            "Driver HOS Compliance",
            "Load Board and Carrier Matching",
            "Fleet Maintenance Scheduling",
            "Last Mile Delivery Route Optimization",
            "Cross Docking Coordination",
        ],
    },
    {
        "ids": _ids("IN", 8),
        "category": "insurance",
        "industry": "insurance",
        "requires_web_search": False,
        "icon": "bi bi-shield-lock",
        "names": [
            "First Notice of Loss Processing",
            "Automated Underwriting Decision",
            "Policy Issuance and Documentation",
            "Premium Billing and Collection",
            "Claims Reserve Calculation",
            "Fraud Analytics and Investigation Queuing",
            "Compliance Filing Automation",
            "Reinsurance Bordereau Generation",
        ],
    },
    {
        "ids": _ids("TE", 6),
        "category": "telecommunications",
        "industry": "telecommunications",
        "requires_web_search": False,
        "icon": "bi bi-broadcast-pin",
        "names": [
            "Network Fault Detection and Auto Healing",
            "Customer Account Provisioning",
            "Usage Based Billing Automation",
            "Churn Prediction and Retention Outreach",
            "Network Capacity Planning Automation",
            "SIM Card Management and Activation",
        ],
    },
    {
        "ids": _ids("EU", 7),
        "category": "energy_utilities",
        "industry": "energy-utilities",
        "requires_web_search": False,
        "icon": "bi bi-lightning",
        "names": [
            "Smart Meter Data Collection and Billing",
            "SCADA and Energy Management System Automation",
            "Outage Management Automation",
            "Renewable Energy Forecasting",
            "Demand Response Automation",
            "Environmental Compliance Reporting",
            "Pipeline Monitoring and Leak Detection",
        ],
    },
]


def _build_task_registry() -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    for group in TASK_GROUP_DEFINITIONS:
        ids = group["ids"]
        names = group["names"]
        if len(ids) != len(names):
            raise ValueError(f"Task definition mismatch in category {group['category']}")

        category = group["category"]
        fallback_icon = TASK_CATEGORIES[category]["icon"]
        for task_id, name in zip(ids, names):
            registry.append(
                {
                    "id": task_id,
                    "name": name,
                    "category": category,
                    "description": (
                        f"Automates {name.lower()} with structured execution steps, quality checks, "
                        "and production ready outputs for teams."
                    ),
                    "industry": group.get("industry"),
                    "requires_web_search": bool(group.get("requires_web_search", False)),
                    "icon": group.get("icon", fallback_icon),
                }
            )

    ids_seen: set[str] = set()
    for task in registry:
        task_id = str(task["id"])
        if task_id in ids_seen:
            raise ValueError(f"Duplicate task id detected: {task_id}")
        ids_seen.add(task_id)

    if len(registry) != 481:
        raise ValueError(f"Task registry size mismatch: expected 481, got {len(registry)}")

    return registry


TASK_REGISTRY_SUMMARY: list[dict[str, Any]] = _build_task_registry()
TASK_LOOKUP_BY_ID: dict[str, dict[str, Any]] = {task["id"]: task for task in TASK_REGISTRY_SUMMARY}

_task_category_counter = Counter(task["category"] for task in TASK_REGISTRY_SUMMARY)
for category_key, category_data in TASK_CATEGORIES.items():
    category_data["task_count"] = int(_task_category_counter.get(category_key, 0))


INDUSTRY_DATA: dict[str, dict[str, Any]] = {
    "finance": {
        "name": "Finance & Banking",
        "hero_title": "Automate Financial Operations With Audit Ready Precision",
        "hero_subtitle": "Scale reconciliation, reporting, compliance, and risk workflows with secure AI agents that reduce manual effort, improve controls, and keep every financial decision traceable across teams.",
        "icon": "bi bi-bank",
        "color": "#0d9488",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 55%, #0d9488 100%)",
        "task_ids": ["F1", "F2", "F6", "F10", "F16", "F20", "16.1", "16.2", "30.2"],
        "compliance_badges": ["RBI", "SEBI", "PCI DSS", "SOC 2"],
        "testimonial": {
            "quote": "We cut monthly close preparation from five days to one while improving audit evidence quality across every account workflow.",
            "author": "Ritika Shah",
            "title": "VP Finance",
            "company": "Horizon Capital Services",
        },
        "stats": [
            {"label": "Manual Finance Hours Reduced", "value": "68%"},
            {"label": "Close Cycle Improvement", "value": "4x"},
            {"label": "Audit Readiness", "value": "99.7%"},
        ],
        "use_case_headline": "High Impact Finance Workflows You Can Automate First",
        "cta_text": "Start automating finance workflows",
        "benefits": [
            "Automate reconciliations with exception level trails.",
            "Generate board ready MIS and compliance packs faster.",
            "Track approvals and policy adherence across teams.",
        ],
    },
    "healthcare": {
        "name": "Healthcare & Medicine",
        "hero_title": "Improve Care Operations Without Adding Administrative Burden",
        "hero_subtitle": "Automate intake, coding, claims, and care coordination workflows so clinical teams focus on outcomes while operations stay compliant, traceable, and consistently fast for patients.",
        "icon": "bi bi-heart-pulse",
        "color": "#dc2626",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 52%, #dc2626 100%)",
        "task_ids": ["H1", "H2", "H3", "H10", "H15", "H17", "18.1", "18.5"],
        "compliance_badges": ["HIPAA", "NABH", "GDPR", "ISO 27001"],
        "testimonial": {
            "quote": "Prior authorization and claims handling became predictable, and our staff now spends more time on patient coordination.",
            "author": "Dr. Meenal Arora",
            "title": "Operations Director",
            "company": "CareBridge Hospitals",
        },
        "stats": [
            {"label": "Admin Turnaround Improvement", "value": "61%"},
            {"label": "Claims Processing Speed", "value": "3.2x"},
            {"label": "Patient Communication SLA", "value": "98%"},
        ],
        "use_case_headline": "Healthcare Automations That Free Up Clinical Time",
        "cta_text": "Modernize healthcare workflows",
        "benefits": [
            "Standardize intake and authorization workflows.",
            "Reduce coding delays with structured automation.",
            "Maintain secure and policy aligned handling of records.",
        ],
    },
    "legal": {
        "name": "Legal",
        "hero_title": "Move Legal Work Faster While Protecting Quality",
        "hero_subtitle": "Streamline contract review, research, drafting, and deadline management with AI workflows that preserve legal rigor, improve turnaround times, and keep partner teams aligned.",
        "icon": "bi bi-shield-check",
        "color": "#f59e0b",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 55%, #f59e0b 100%)",
        "task_ids": ["LE1", "LE2", "LE4", "LE10", "17.1", "17.3", "17.5", "30.5"],
        "compliance_badges": ["GDPR", "DPDP", "ISO 27001", "SOC 2"],
        "testimonial": {
            "quote": "Our legal ops team now ships first drafts in hours, and counsel can focus on strategic review.",
            "author": "Aarav Menon",
            "title": "Head of Legal Operations",
            "company": "Nexus LegalTech",
        },
        "stats": [
            {"label": "Contract Drafting Time Saved", "value": "72%"},
            {"label": "Review Consistency", "value": "96%"},
            {"label": "Matter Intake Speed", "value": "3x"},
        ],
        "use_case_headline": "Legal Use Cases With Immediate ROI",
        "cta_text": "Automate legal operations",
        "benefits": [
            "Flag risks early with structured clause analysis.",
            "Standardize templates across every practice area.",
            "Track deadlines and obligations without manual chasing.",
        ],
    },
    "education": {
        "name": "Education",
        "hero_title": "Deliver Better Learning Operations At Institutional Scale",
        "hero_subtitle": "Automate admissions, student communication, grading support, and content workflows so educators and administrators can focus on outcomes while keeping operations reliable and measurable.",
        "icon": "bi bi-mortarboard",
        "color": "#2563eb",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 58%, #2563eb 100%)",
        "task_ids": ["ED1", "ED2", "ED3", "ED7", "ED10", "19.1", "19.4", "19.8"],
        "compliance_badges": ["FERPA", "GDPR", "ISO 27001", "SOC 2"],
        "testimonial": {
            "quote": "From lesson planning to student outreach, our faculty regained hours every week without compromising quality.",
            "author": "Nidhi Kulkarni",
            "title": "Dean of Digital Learning",
            "company": "Pinnacle Learning Group",
        },
        "stats": [
            {"label": "Academic Admin Time Saved", "value": "57%"},
            {"label": "Student Response Speed", "value": "4x"},
            {"label": "Content Delivery Consistency", "value": "98%"},
        ],
        "use_case_headline": "Education Workflows Ready For Automation",
        "cta_text": "Transform education operations",
        "benefits": [
            "Automate repetitive academic operations and updates.",
            "Personalize communication at student level.",
            "Track outcomes with transparent execution dashboards.",
        ],
    },
    "ecommerce": {
        "name": "E Commerce",
        "hero_title": "Run Leaner Commerce Operations Across Every Channel",
        "hero_subtitle": "Automate order handling, pricing, catalog enrichment, and returns so teams can scale revenue and customer experience without adding operational complexity or manual review bottlenecks.",
        "icon": "bi bi-bag-check",
        "color": "#7c3aed",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 50%, #7c3aed 100%)",
        "task_ids": ["EC1", "EC2", "EC3", "EC6", "EC9", "10.1", "10.2", "10.4"],
        "compliance_badges": ["PCI DSS", "GDPR", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "We scaled to three marketplaces and reduced returns turnaround from days to hours with automation.",
            "author": "Karan Bedi",
            "title": "Head of Operations",
            "company": "CartNova Retail",
        },
        "stats": [
            {"label": "Order Ops Throughput", "value": "3.8x"},
            {"label": "Cart Recovery Improvement", "value": "29%"},
            {"label": "Inventory Accuracy", "value": "97%"},
        ],
        "use_case_headline": "E Commerce Automations That Drive Margin",
        "cta_text": "Scale e commerce with AI",
        "benefits": [
            "Improve fulfillment speed with automated orchestration.",
            "Monitor pricing and inventory in near real time.",
            "Deliver consistent customer updates at every stage.",
        ],
    },
    "manufacturing": {
        "name": "Manufacturing",
        "hero_title": "Modernize Production Ops With Data Driven Automation",
        "hero_subtitle": "Automate quality checks, maintenance scheduling, planning, and supplier monitoring so plant teams improve uptime, reduce errors, and keep execution synchronized across functions.",
        "icon": "bi bi-gear-wide-connected",
        "color": "#0f766e",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 54%, #0f766e 100%)",
        "task_ids": ["M1", "M2", "M4", "M7", "M10", "M13", "29.1", "29.4"],
        "compliance_badges": ["ISO 9001", "ISO 27001", "SOC 2", "OSHA"],
        "testimonial": {
            "quote": "Production planning and maintenance are now linked, which improved uptime and reduced urgent disruptions.",
            "author": "Suresh Iyer",
            "title": "Plant Operations Lead",
            "company": "Apex Components",
        },
        "stats": [
            {"label": "Unplanned Downtime Reduction", "value": "34%"},
            {"label": "Quality Reporting Speed", "value": "5x"},
            {"label": "Supplier Visibility", "value": "92%"},
        ],
        "use_case_headline": "Manufacturing Use Cases Built For Reliability",
        "cta_text": "Automate factory workflows",
        "benefits": [
            "Coordinate production planning with inventory signals.",
            "Capture quality data with fewer manual handoffs.",
            "Enable preventive actions through predictive insights.",
        ],
    },
    "real-estate": {
        "name": "Real Estate",
        "hero_title": "Automate Property Operations From Listing To Reporting",
        "hero_subtitle": "Improve leasing, tenant, and portfolio workflows through AI automations that reduce manual follow ups, increase data quality, and accelerate property level decision making.",
        "icon": "bi bi-house-door",
        "color": "#2563eb",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 57%, #2563eb 100%)",
        "task_ids": ["RE1", "RE2", "RE4", "RE5", "RE10", "28.1", "28.3", "7.6"],
        "compliance_badges": ["RERA", "GDPR", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "Leasing and maintenance workflows now run on one timeline, and investor reporting is always current.",
            "author": "Ishita Rao",
            "title": "Portfolio Manager",
            "company": "UrbanGrid Realty",
        },
        "stats": [
            {"label": "Leasing Cycle Speed", "value": "2.6x"},
            {"label": "Maintenance Resolution Time", "value": "43%"},
            {"label": "Reporting Accuracy", "value": "98%"},
        ],
        "use_case_headline": "Property Workflows You Can Automate Today",
        "cta_text": "Transform real estate operations",
        "benefits": [
            "Automate tenant communication and payment follow ups.",
            "Track property performance with live operational metrics.",
            "Streamline multi site listing and documentation updates.",
        ],
    },
    "hr": {
        "name": "Human Resources",
        "hero_title": "Scale People Operations With Less Manual Coordination",
        "hero_subtitle": "Automate hiring, onboarding, benefits, and workforce reporting while maintaining a strong employee experience and consistent compliance across fast growing organizations.",
        "icon": "bi bi-person-workspace",
        "color": "#7c3aed",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 52%, #7c3aed 100%)",
        "task_ids": ["HR1", "HR2", "HR3", "HR5", "HR9", "25.1", "25.2", "14.1"],
        "compliance_badges": ["DPDP", "GDPR", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "Recruiting and onboarding no longer rely on spreadsheet follow ups, and managers get visibility instantly.",
            "author": "Priyanka Dutta",
            "title": "Head of People",
            "company": "ScaleStack Labs",
        },
        "stats": [
            {"label": "Hiring Workflow Time Saved", "value": "63%"},
            {"label": "Onboarding Completion Rate", "value": "96%"},
            {"label": "HR Ticket SLA Compliance", "value": "99%"},
        ],
        "use_case_headline": "High Value HR Automations For Growing Teams",
        "cta_text": "Automate HR workflows",
        "benefits": [
            "Unify recruiting and onboarding execution.",
            "Automate policy communication with traceable delivery.",
            "Build workforce insights without manual report assembly.",
        ],
    },
    "marketing": {
        "name": "Marketing",
        "hero_title": "Ship Campaigns Faster With Structured AI Operations",
        "hero_subtitle": "Automate planning, publishing, reporting, and optimization so teams run more campaigns with better consistency, stronger attribution, and less coordination overhead.",
        "icon": "bi bi-megaphone",
        "color": "#d97706",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 53%, #d97706 100%)",
        "task_ids": ["MA1", "MA3", "MA4", "MA9", "MA12", "26.1", "26.2", "11.2"],
        "compliance_badges": ["GDPR", "SOC 2", "ISO 27001", "CCPA"],
        "testimonial": {
            "quote": "Our content pipeline moved from ad hoc to systemized, and campaign velocity doubled within one quarter.",
            "author": "Neha Bansal",
            "title": "Marketing Director",
            "company": "GrowthSphere Digital",
        },
        "stats": [
            {"label": "Campaign Production Speed", "value": "2.9x"},
            {"label": "Content Output Increase", "value": "4x"},
            {"label": "Attribution Coverage", "value": "95%"},
        ],
        "use_case_headline": "Marketing Workflows Built For Consistency",
        "cta_text": "Accelerate marketing execution",
        "benefits": [
            "Automate briefs, variants, and review cycles.",
            "Track channel performance with near real time insight.",
            "Scale content without losing brand consistency.",
        ],
    },
    "it-software": {
        "name": "IT & Software",
        "hero_title": "Automate DevOps And IT Operations End To End",
        "hero_subtitle": "Reduce incident load and operational toil by automating provisioning, deployment, observability, and remediation workflows across modern software and infrastructure environments.",
        "icon": "bi bi-cpu",
        "color": "#0891b2",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 50%, #0891b2 100%)",
        "task_ids": ["IT1", "IT2", "IT4", "IT8", "IT10", "6.2", "6.6", "22.6"],
        "compliance_badges": ["ISO 27001", "SOC 2", "GDPR", "PCI DSS"],
        "testimonial": {
            "quote": "Routine operations are now policy driven automations, and engineers spend more time shipping product.",
            "author": "Rahul Varma",
            "title": "VP Engineering",
            "company": "CloudFrame Systems",
        },
        "stats": [
            {"label": "Ops Toil Reduction", "value": "71%"},
            {"label": "Incident Response Speed", "value": "2.4x"},
            {"label": "Release Reliability", "value": "99.95%"},
        ],
        "use_case_headline": "IT Automations That Protect Engineering Velocity",
        "cta_text": "Scale IT operations with AI",
        "benefits": [
            "Automate repetitive runbook steps during incidents.",
            "Standardize secure infrastructure changes.",
            "Improve observability and alert response consistency.",
        ],
    },
    "government": {
        "name": "Government & Public Services",
        "hero_title": "Deliver Public Services Faster With Transparent Automation",
        "hero_subtitle": "Automate permits, records, eligibility checks, and case workflows while preserving accountability, policy compliance, and service level transparency for citizens and agency teams.",
        "icon": "bi bi-building",
        "color": "#0ea5e9",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 55%, #0ea5e9 100%)",
        "task_ids": ["G1", "G2", "G3", "G7", "G8", "30.1", "30.3", "30.5"],
        "compliance_badges": ["DPDP", "ISO 27001", "SOC 2", "GIGW"],
        "testimonial": {
            "quote": "Service request handling became faster and easier to audit across departments in a matter of weeks.",
            "author": "Anita Sethi",
            "title": "Digital Governance Lead",
            "company": "CivicOne Program Office",
        },
        "stats": [
            {"label": "Citizen Response Time", "value": "52%"},
            {"label": "Case Throughput", "value": "2.1x"},
            {"label": "Policy Compliance Coverage", "value": "98%"},
        ],
        "use_case_headline": "Public Sector Workflows Ready For Automation",
        "cta_text": "Modernize government operations",
        "benefits": [
            "Reduce manual backlog in permit and case handling.",
            "Track every workflow action for accountability.",
            "Improve citizen communication consistency and speed.",
        ],
    },
    "media-entertainment": {
        "name": "Media & Entertainment",
        "hero_title": "Automate Content Operations Across Production Pipelines",
        "hero_subtitle": "Streamline metadata, moderation, publishing, and localization workflows so media teams can ship content faster while maintaining quality, rights control, and compliance visibility.",
        "icon": "bi bi-film",
        "color": "#a855f7",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 49%, #a855f7 100%)",
        "task_ids": ["ME1", "ME2", "ME3", "ME4", "ME7", "11.1", "12.6", "20.4"],
        "compliance_badges": ["GDPR", "DMCA", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "Localization and publishing workflows are now coordinated automatically, which cut our release delays significantly.",
            "author": "Zubin Contractor",
            "title": "Head of Content Operations",
            "company": "StreamPulse Media",
        },
        "stats": [
            {"label": "Publishing Cycle Reduction", "value": "46%"},
            {"label": "Metadata Accuracy", "value": "98.5%"},
            {"label": "Localization Turnaround", "value": "3x"},
        ],
        "use_case_headline": "Media Workflows Built For High Volume Delivery",
        "cta_text": "Scale media operations",
        "benefits": [
            "Coordinate content metadata and rights workflows.",
            "Automate moderation and compliance checks.",
            "Accelerate release readiness across channels.",
        ],
    },
    "travel-hospitality": {
        "name": "Travel & Hospitality",
        "hero_title": "Deliver Smoother Guest Operations With Intelligent Automation",
        "hero_subtitle": "Automate reservations, communications, revenue workflows, and service operations across travel and hospitality teams to improve guest experience and operational efficiency at scale.",
        "icon": "bi bi-compass",
        "color": "#0284c7",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 52%, #0284c7 100%)",
        "task_ids": ["TH1", "TH2", "TH3", "TH6", "TH8", "13.1", "13.4", "13.5"],
        "compliance_badges": ["PCI DSS", "GDPR", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "Guest communication and reservation updates are now fully coordinated, reducing service escalations during peak season.",
            "author": "Mitali Joshi",
            "title": "Regional Operations Head",
            "company": "VistaStay Hospitality",
        },
        "stats": [
            {"label": "Guest Response Time Improvement", "value": "67%"},
            {"label": "Booking Ops Accuracy", "value": "99%"},
            {"label": "Channel Update Speed", "value": "4x"},
        ],
        "use_case_headline": "Travel And Hospitality Automations That Matter",
        "cta_text": "Optimize travel workflows",
        "benefits": [
            "Automate guest messaging through the full journey.",
            "Keep inventory and pricing synchronized.",
            "Reduce manual load on front office teams.",
        ],
    },
    "food-restaurant": {
        "name": "Food & Restaurant",
        "hero_title": "Automate Restaurant Operations From Kitchen To Counter",
        "hero_subtitle": "Improve consistency across ordering, inventory, costing, scheduling, and compliance workflows so restaurant teams can focus on service quality and profitable growth.",
        "icon": "bi bi-cup-hot",
        "color": "#dc2626",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 50%, #dc2626 100%)",
        "task_ids": ["FR1", "FR2", "FR3", "FR4", "FR7", "10.2", "8.8", "22.7"],
        "compliance_badges": ["FSSAI", "GST", "SOC 2", "ISO 27001"],
        "testimonial": {
            "quote": "Food cost controls and vendor processing improved quickly, and our store managers spend less time on paperwork.",
            "author": "Harsh Patel",
            "title": "Operations Manager",
            "company": "UrbanTiffin Restaurants",
        },
        "stats": [
            {"label": "Back Office Time Saved", "value": "58%"},
            {"label": "Food Cost Variance Reduction", "value": "22%"},
            {"label": "Compliance Readiness", "value": "97%"},
        ],
        "use_case_headline": "Restaurant Tasks You Can Automate In Weeks",
        "cta_text": "Automate restaurant operations",
        "benefits": [
            "Connect POS and inventory workflows end to end.",
            "Automate schedule planning and vendor processing.",
            "Keep compliance records ready for inspection.",
        ],
    },
    "agriculture": {
        "name": "Agriculture",
        "hero_title": "Bring Data Driven Automation To Modern Farming",
        "hero_subtitle": "Automate crop planning, irrigation, equipment monitoring, and commodity alerts so agricultural teams can improve yield outcomes while reducing operational uncertainty and manual tracking.",
        "icon": "bi bi-flower1",
        "color": "#16a34a",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 48%, #16a34a 100%)",
        "task_ids": ["AG1", "AG2", "AG3", "AG4", "AG6", "13.7", "16.3", "EU4"],
        "compliance_badges": ["ISO 27001", "SOC 2", "GAP", "GDPR"],
        "testimonial": {
            "quote": "We now plan irrigation and market pricing actions from one workflow, improving both yield and planning confidence.",
            "author": "Mahesh Chauhan",
            "title": "Farm Program Lead",
            "company": "GreenField Agro Systems",
        },
        "stats": [
            {"label": "Planning Efficiency", "value": "49%"},
            {"label": "Yield Forecast Accuracy", "value": "31%"},
            {"label": "Field Ops Visibility", "value": "95%"},
        ],
        "use_case_headline": "Agriculture Automations For Smarter Operations",
        "cta_text": "Automate agriculture workflows",
        "benefits": [
            "Combine weather, crop, and pricing signals automatically.",
            "Track farm operations with structured digital logs.",
            "Improve resource planning across growing cycles.",
        ],
    },
    "logistics": {
        "name": "Logistics & Transportation",
        "hero_title": "Move Freight Faster With Workflow Driven Logistics Automation",
        "hero_subtitle": "Automate quotes, visibility, customs, routing, and fleet operations so logistics teams improve reliability, cut delays, and keep every shipment milestone transparent.",
        "icon": "bi bi-sign-turn-right",
        "color": "#0ea5e9",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 52%, #0ea5e9 100%)",
        "task_ids": ["LT1", "LT2", "LT3", "LT5", "LT7", "29.2", "29.3", "22.2"],
        "compliance_badges": ["ISO 27001", "SOC 2", "AEO", "GDPR"],
        "testimonial": {
            "quote": "Shipment visibility and documentation automation helped us cut avoidable delay costs across lanes.",
            "author": "Gurpreet Sandhu",
            "title": "Director of Logistics",
            "company": "RouteWave Transport",
        },
        "stats": [
            {"label": "On Time Delivery Improvement", "value": "24%"},
            {"label": "Documentation SLA", "value": "99%"},
            {"label": "Dispatch Efficiency", "value": "2.3x"},
        ],
        "use_case_headline": "Logistics Automations For Better Reliability",
        "cta_text": "Optimize logistics operations",
        "benefits": [
            "Automate quote to shipment workflows.",
            "Improve customs and compliance documentation speed.",
            "Keep fleet and route execution synchronized.",
        ],
    },
    "insurance": {
        "name": "Insurance",
        "hero_title": "Accelerate Policy And Claims Workflows With Control",
        "hero_subtitle": "Automate underwriting, claims handling, compliance reporting, and billing operations so insurance teams increase processing capacity while maintaining strong governance and auditability.",
        "icon": "bi bi-shield-lock",
        "color": "#2563eb",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 56%, #2563eb 100%)",
        "task_ids": ["IN1", "IN2", "IN3", "IN5", "IN6", "5.4", "16.5", "H2"],
        "compliance_badges": ["IRDAI", "SOC 2", "ISO 27001", "GDPR"],
        "testimonial": {
            "quote": "Claims intake and reserve workflows are now significantly faster, and exceptions are surfaced much earlier.",
            "author": "Devanshi Mehta",
            "title": "Head of Claims Operations",
            "company": "SecureLife Insurance",
        },
        "stats": [
            {"label": "Claims Processing Time Saved", "value": "54%"},
            {"label": "Underwriting Throughput", "value": "2.7x"},
            {"label": "Compliance Report Readiness", "value": "98%"},
        ],
        "use_case_headline": "Insurance Workflows Ready To Automate",
        "cta_text": "Automate insurance operations",
        "benefits": [
            "Route claims with policy aware decision support.",
            "Improve underwriting cycle speed with automation.",
            "Generate compliance evidence with minimal manual effort.",
        ],
    },
    "telecommunications": {
        "name": "Telecommunications",
        "hero_title": "Automate Telco Operations Across Network And Customer Systems",
        "hero_subtitle": "Modernize provisioning, billing, retention, and network workflows with AI automations that improve service quality, reduce churn risk, and increase operational consistency.",
        "icon": "bi bi-broadcast-pin",
        "color": "#7c3aed",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 50%, #7c3aed 100%)",
        "task_ids": ["TE1", "TE2", "TE3", "TE4", "TE5", "IT11", "IT15", "22.3"],
        "compliance_badges": ["TRAI", "ISO 27001", "SOC 2", "GDPR"],
        "testimonial": {
            "quote": "Provisioning and fault response are now connected workflows, helping us improve uptime and response quality.",
            "author": "Anuj Nagpal",
            "title": "Network Operations Head",
            "company": "SkyLink Telecom",
        },
        "stats": [
            {"label": "Provisioning Speed", "value": "3.4x"},
            {"label": "Fault Resolution Time", "value": "41%"},
            {"label": "Retention Campaign Lift", "value": "18%"},
        ],
        "use_case_headline": "Telco Use Cases Built For Scale",
        "cta_text": "Automate telecom workflows",
        "benefits": [
            "Reduce provisioning delays with automated checks.",
            "Connect network events to support workflows.",
            "Improve billing accuracy across usage events.",
        ],
    },
    "energy-utilities": {
        "name": "Energy & Utilities",
        "hero_title": "Automate Utility Operations For Better Reliability And Compliance",
        "hero_subtitle": "Streamline metering, outage response, forecasting, and environmental reporting workflows with AI powered automation built for high reliability utility operations.",
        "icon": "bi bi-lightning",
        "color": "#f59e0b",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 48%, #f59e0b 100%)",
        "task_ids": ["EU1", "EU2", "EU3", "EU4", "EU5", "EU6", "EU7", "IT3"],
        "compliance_badges": ["ISO 27001", "SOC 2", "EPA", "GDPR"],
        "testimonial": {
            "quote": "Outage coordination and compliance reporting are now proactive, helping us improve both response and readiness.",
            "author": "Shalini Nair",
            "title": "Grid Operations Manager",
            "company": "VoltGrid Utilities",
        },
        "stats": [
            {"label": "Outage Response Improvement", "value": "36%"},
            {"label": "Forecasting Accuracy", "value": "27%"},
            {"label": "Reporting Time Saved", "value": "62%"},
        ],
        "use_case_headline": "Utility Workflows To Automate Right Away",
        "cta_text": "Modernize utility operations",
        "benefits": [
            "Automate meter and outage event processing.",
            "Improve sustainability reporting consistency.",
            "Coordinate field and control center execution.",
        ],
    },
    "nonprofit": {
        "name": "Nonprofit",
        "hero_title": "Scale Mission Impact With Leaner Back Office Operations",
        "hero_subtitle": "Automate donor communication, grant reporting, volunteer workflows, and impact tracking so mission teams spend less time on administration and more time delivering outcomes.",
        "icon": "bi bi-heart",
        "color": "#16a34a",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 50%, #16a34a 100%)",
        "task_ids": ["NP1", "NP2", "NP3", "NP5", "NP6", "31.7", "30.4", "12.1"],
        "compliance_badges": ["FCRA", "SOC 2", "ISO 27001", "GDPR"],
        "testimonial": {
            "quote": "Grant and impact reporting that took weeks now runs as a repeatable workflow with clear ownership.",
            "author": "Farah Khan",
            "title": "Programs Director",
            "company": "ImpactBridge Foundation",
        },
        "stats": [
            {"label": "Reporting Cycle Reduction", "value": "64%"},
            {"label": "Volunteer Ops Efficiency", "value": "2.5x"},
            {"label": "Donor Engagement Lift", "value": "22%"},
        ],
        "use_case_headline": "Mission Critical Nonprofit Workflows",
        "cta_text": "Automate nonprofit operations",
        "benefits": [
            "Streamline recurring donor and volunteer communications.",
            "Generate grant and impact reports faster.",
            "Track outcomes with reliable operational data.",
        ],
    },
    "research-academia": {
        "name": "Research & Academia",
        "hero_title": "Accelerate Research Lifecycles Without Sacrificing Rigor",
        "hero_subtitle": "Automate literature review, protocol management, data handling, and publication workflows so research teams spend more time on discovery and less on repetitive process work.",
        "icon": "bi bi-book",
        "color": "#1d4ed8",
        "hero_gradient": "linear-gradient(135deg, #0f172a 0%, #1a56db 56%, #1d4ed8 100%)",
        "task_ids": ["RA1", "RA2", "RA4", "RA5", "RA9", "18.1", "19.7", "1.3"],
        "compliance_badges": ["IRB", "GDPR", "ISO 27001", "SOC 2"],
        "testimonial": {
            "quote": "Our review and manuscript preparation pipeline is now significantly faster while keeping citation quality high.",
            "author": "Prof. Aditya Sen",
            "title": "Research Program Director",
            "company": "Institute for Applied Intelligence",
        },
        "stats": [
            {"label": "Literature Review Speed", "value": "3.1x"},
            {"label": "Submission Readiness", "value": "2.4x"},
            {"label": "Data Workflow Consistency", "value": "97%"},
        ],
        "use_case_headline": "Research Workflows Optimized For Throughput",
        "cta_text": "Accelerate research workflows",
        "benefits": [
            "Automate repetitive research process coordination.",
            "Keep citation and protocol quality consistent.",
            "Improve visibility across studies and submissions.",
        ],
    },
}


def _article_html(paragraphs: list[str], headings: list[str]) -> str:
    parts: list[str] = []
    for idx, paragraph in enumerate(paragraphs):
        if idx < len(headings):
            parts.append(f"<h2>{headings[idx]}</h2>")
        parts.append(f"<p>{paragraph}</p>")
    return "".join(parts)


BLOG_POSTS: list[dict[str, Any]] = [
    {
        "slug": "ai-automation-trends-india-2026",
        "title": "AI Automation Trends Indian Teams Should Watch in 2026",
        "excerpt": "Indian businesses are moving from one off AI prompts to production workflows. The winners are building repeatable automation systems with clear governance and measurable output quality.",
        "content": _article_html(
            paragraphs=[
                "Most teams in 2026 are no longer asking whether AI can help. They are asking where AI can be trusted repeatedly and where humans should stay in the loop. The biggest shift is operational discipline. Organizations are mapping repetitive work, defining acceptable outputs, and creating simple review checkpoints. This approach removes the fear of automation because teams can verify quality, keep accountability, and improve each workflow over time.",
                "A second trend is consolidation around workflow based execution instead of isolated tools. Marketing teams are connecting keyword research, draft generation, legal review, and publishing into one chain. Finance teams are linking reconciliation, exception checks, and report generation in a single run. This change matters because value comes from orchestration, not from isolated model responses. Teams save the most time when handoffs disappear.",
                "Security and compliance are now board level discussions for AI programs. High growth companies are asking for role based access, audit logs, and region aware data controls before expanding automation. This has pushed vendors to design products that fit enterprise governance from the start. Adoption rises when compliance teams can see who triggered workflows, what outputs were created, and how sensitive data was handled at each stage.",
                "Another visible pattern is output standardization. Teams used to reject AI content because style and structure varied between runs. In mature deployments, workflows produce templates, citations, and format rules automatically. This consistency creates trust with leadership because reports, emails, and analyses arrive in expected formats. It also reduces reviewer fatigue since the same checklist can be applied every time.",
                "Cost efficiency is also changing. Companies are learning that AI ROI is not only about token costs. The real gain is in cycle time, reduced rework, and better throughput from existing teams. A workflow that saves fifteen minutes once is interesting. A workflow that saves fifteen minutes across three hundred runs every week changes operating margins and team capacity significantly.",
                "The final trend is role redesign, not role replacement. Strong teams define which decisions remain human and which production steps become automated. When done well, employees spend less time gathering data and more time making strategic choices. That is the practical future of AI automation in India: measurable workflows, secure operations, and higher value work for people.",
            ],
            headings=[
                "Why the Conversation Has Changed",
                "From Tools to Workflow Systems",
                "Compliance Is Now a Growth Enabler",
                "Standardized Outputs Build Trust",
                "ROI Comes From Throughput",
                "Human Roles Become More Strategic",
            ],
        ),
        "author_name": "Ananya Desai",
        "author_title": "Editor, Automation Strategy",
        "category": "Trends",
        "published_date": "April 10, 2026",
        "read_time_minutes": 8,
        "tags": ["AI Automation", "India", "Workflow Design", "Productivity"],
        "featured": True,
        "hero_image_alt": "Team planning automation roadmap on a digital dashboard",
    },
    {
        "slug": "productivity-playbook-research-teams",
        "title": "A Practical Productivity Playbook for Research Heavy Teams",
        "excerpt": "Research bottlenecks usually come from handoffs and inconsistent synthesis quality. A workflow first operating model helps teams ship faster without compromising evidence quality.",
        "content": _article_html(
            paragraphs=[
                "Research teams often think their bottleneck is not enough people. In practice, the bottleneck is usually process fragmentation. One person gathers sources, another writes notes, someone else formats the final report, and quality checks happen at the end. This sequence creates delays and inconsistencies. A better approach is to automate repeatable portions of the workflow while preserving expert review where judgment matters.",
                "Start by classifying research work into three buckets. The first bucket is discovery, where agents collect and normalize sources from websites, reports, and databases. The second bucket is synthesis, where content is grouped into themes, gaps, and contradictions. The third bucket is publication, where findings are converted into client ready output formats. This structure makes ownership clear and enables objective performance tracking.",
                "Next, standardize output templates before scaling. If every report has a different structure, review cycles become slow and subjective. Define a baseline structure with sections for source coverage, assumptions, key insights, and recommended actions. AI workflows can fill this structure quickly, but reviewers should still verify conclusions and high impact decisions. Standardization is what turns speed into reliable quality.",
                "It also helps to set confidence thresholds for different task types. Source extraction can run with high automation because it is mostly deterministic. Strategic conclusions should require human sign off. Teams that define thresholds early avoid over automation mistakes and build confidence among stakeholders. This model also improves onboarding because new team members learn where automation helps and where judgment is required.",
                "Measure outcomes using operational metrics, not vanity metrics. Useful indicators include turnaround time, percentage of reusable outputs, reviewer correction rate, and stakeholder satisfaction. These metrics show whether workflows are truly reducing friction. Over time, correction rates should decline as prompts, templates, and validation rules improve.",
                "When implemented this way, research automation does not remove analysts. It removes repetitive coordination overhead. Analysts spend more time on framing the right questions, challenging assumptions, and presenting strategic recommendations. That is where their expertise creates disproportionate value.",
            ],
            headings=[
                "The Real Bottleneck in Research Operations",
                "Design a Three Stage Workflow",
                "Template First, Then Scale",
                "Set Confidence Thresholds",
                "Track Metrics That Matter",
                "Shift Analysts to Higher Value Work",
            ],
        ),
        "author_name": "Rohit Ghosh",
        "author_title": "Principal Workflow Consultant",
        "category": "Productivity",
        "published_date": "March 29, 2026",
        "read_time_minutes": 7,
        "tags": ["Research", "Productivity", "Operations"],
        "featured": False,
        "hero_image_alt": "Research analysts reviewing structured findings on a shared screen",
    },
    {
        "slug": "finance-ops-automation-with-ai",
        "title": "How Finance Operations Teams Can Deploy AI Without Compliance Risk",
        "excerpt": "Finance teams can automate high volume processes while improving control quality. The key is designing review points, exception handling, and audit trails from day one.",
        "content": _article_html(
            paragraphs=[
                "Finance leaders are under pressure to close faster, provide better forecasts, and maintain strict compliance. AI can help, but only when workflows are designed for control as much as speed. The best finance automation programs begin by selecting deterministic processes like reconciliation support, invoice triage, and report assembly. These areas deliver immediate gains and create confidence for broader adoption.",
                "A strong first step is exception based workflow design. Instead of trying to automate every edge case, teams automate standard paths and route anomalies for analyst review. This keeps throughput high while preserving oversight where risk is higher. The exception queue also becomes a learning signal for improving business rules over time.",
                "Auditability should be built into every automated run. Teams need records of source data used, transformations applied, and outputs generated. When audit questions arise, evidence should be accessible without manual reconstruction. This is why workflow logs and versioned templates are critical. They protect both compliance posture and team confidence.",
                "Model governance also matters in finance contexts. Prompt changes, validation rules, and output formats should follow controlled release practices similar to software changes. A lightweight approval process prevents accidental drift and ensures report quality remains stable. Teams with governance discipline typically scale faster because stakeholders trust the system.",
                "Finance automation programs succeed when they combine technical controls with role clarity. Analysts move from manual compilation to review and interpretation. Managers move from chasing updates to reviewing exceptions and trends. This shift improves job quality while reducing operational fatigue during monthly and quarterly cycles.",
                "The outcome is not just faster processing. It is better decision readiness. When finance data is assembled consistently and exceptions are surfaced early, leadership can act with higher confidence. That is the real competitive advantage.",
            ],
            headings=[
                "Control and Speed Must Coexist",
                "Use Exception Based Automation",
                "Make Auditability Non Negotiable",
                "Apply Lightweight Governance",
                "Redefine Team Roles Around Insight",
                "Decision Readiness Is the True ROI",
            ],
        ),
        "author_name": "Meera Venkataraman",
        "author_title": "Finance Automation Lead",
        "category": "Finance",
        "published_date": "March 18, 2026",
        "read_time_minutes": 9,
        "tags": ["Finance", "Compliance", "Audit", "Automation"],
        "featured": False,
        "hero_image_alt": "Finance team reviewing automated reconciliation dashboard",
    },
    {
        "slug": "healthcare-admin-automation-playbook",
        "title": "Healthcare Admin Automation Playbook for Faster Patient Operations",
        "excerpt": "Clinical teams need less paperwork and faster administrative support. AI workflows can reduce delays in claims, intake, and authorization without compromising compliance.",
        "content": _article_html(
            paragraphs=[
                "Healthcare organizations face a constant tension between operational speed and regulatory rigor. Administrative delays affect patient experience, provider productivity, and revenue cycles. AI automation can reduce this burden when workflows are designed around process clarity and secure handling of sensitive data. The first priority should be repetitive, high volume administrative tasks where quality criteria are explicit.",
                "Patient intake is a practical place to start. Intake forms often require normalization, validation, and routing to different teams. AI workflows can extract structured fields, flag missing information, and pre populate downstream systems before staff review. This shortens registration time while reducing duplicate data entry work for front desk teams.",
                "Prior authorization and claims processing are another high impact area. Workflow automation can classify request types, assemble required documentation, and track status updates automatically. Staff then focus on exceptions and escalations. This model improves turnaround while keeping accountability clear, because every action remains visible in workflow logs.",
                "Compliance requirements should be embedded, not added later. Access controls, retention settings, and role based permissions need to be defined from the start. Teams should also implement clear review policies for any output that influences clinical or financial decisions. Automation is most effective when it augments staff judgment rather than bypassing it.",
                "Operational metrics help teams improve safely. Useful measures include authorization cycle time, claims first pass acceptance rate, and percentage of cases requiring manual intervention. Tracking these over time helps leadership prioritize which workflows to expand and which need tighter rules.",
                "The long term value is capacity. As administrative throughput improves, staff can spend more time on patient coordination and care quality initiatives. That is the practical promise of healthcare automation done responsibly.",
            ],
            headings=[
                "Why Administrative Workflows Matter",
                "Start With Intake Standardization",
                "Improve Authorization and Claims Flow",
                "Build Compliance Into Workflow Design",
                "Measure for Safe Optimization",
                "Free Capacity for Better Care",
            ],
        ),
        "author_name": "Dr. Kavya Rao",
        "author_title": "Healthcare Systems Advisor",
        "category": "Healthcare",
        "published_date": "March 06, 2026",
        "read_time_minutes": 8,
        "tags": ["Healthcare", "Operations", "Claims", "HIPAA"],
        "featured": False,
        "hero_image_alt": "Healthcare operations team managing automated patient workflows",
    },
    {
        "slug": "seo-content-pipeline-with-ai-agents",
        "title": "Building an SEO Content Pipeline With AI Agents and Human Review",
        "excerpt": "High performing content teams rely on workflow consistency, not random generation. Combining AI execution with editorial control creates better search performance and faster publishing.",
        "content": _article_html(
            paragraphs=[
                "SEO content operations often break when speed and quality pull in opposite directions. Teams either publish quickly with weak structure or publish slowly with heavy manual effort. A workflow based model can resolve this trade off. The core idea is to automate predictable steps such as topic clustering, outline generation, metadata creation, and distribution prep, while editors focus on narrative quality and strategic positioning.",
                "The pipeline should begin with keyword clustering tied to intent. AI agents can group terms by informational, commercial, and comparison goals, then propose article briefs with primary and secondary keyword coverage. This gives writers a clear structure before drafting begins. Teams that start with intent mapping usually improve both rankings and conversion relevance.",
                "Draft generation works best when tied to brand standards. Define tone rules, formatting patterns, citation expectations, and internal linking guidance in a reusable template. AI can then generate faster without producing inconsistent pages. Editors should focus on verifying claims, tightening arguments, and adding domain specific insight that generic drafts cannot provide.",
                "Technical SEO checks can also be automated before publication. Workflows can validate heading hierarchy, meta length, image alt text, and schema readiness. This reduces avoidable publishing errors and shortens QA cycles. A pre publish checklist generated automatically is one of the simplest ways to improve content reliability.",
                "Performance reporting should close the loop. Track ranking movement, click through rates, and assisted conversion impact by topic cluster. When these metrics feed back into briefing workflows, teams learn which content formats and topics deliver durable growth. This transforms SEO from reactive publishing into an optimization system.",
                "In the end, the best content teams are not replacing editors with AI. They are giving editors better leverage. AI handles throughput and structure, while humans shape authority and trust.",
            ],
            headings=[
                "Why Content Pipelines Need Structure",
                "Keyword Clustering by Intent",
                "Template Driven Drafting",
                "Automated Technical SEO Validation",
                "Close the Loop With Performance Data",
                "Editorial Leverage Is the Real Goal",
            ],
        ),
        "author_name": "Sana Qureshi",
        "author_title": "Head of Content Strategy",
        "category": "Marketing",
        "published_date": "February 21, 2026",
        "read_time_minutes": 7,
        "tags": ["SEO", "Content", "Marketing", "Workflow"],
        "featured": False,
        "hero_image_alt": "Marketing team building AI assisted SEO content workflow",
    },
    {
        "slug": "secure-enterprise-workflow-design",
        "title": "Secure Workflow Design for Enterprise AI Automation",
        "excerpt": "Enterprise adoption depends on governance as much as capability. Secure workflow design lets teams scale automation while keeping access, compliance, and change control aligned.",
        "content": _article_html(
            paragraphs=[
                "Enterprise leaders often discover that AI pilots succeed technically but stall operationally. The blocker is usually governance. Without clear controls, security teams hesitate to approve wider rollout. Secure workflow design solves this by making policy compliance a default property of automation, not an afterthought.",
                "Start with role based access boundaries. Not every user should trigger every workflow or access every output. Define permissions by function, business unit, and data sensitivity. Then enforce these boundaries in execution and output retrieval flows. This simple control significantly reduces accidental exposure risks.",
                "Next, build immutable workflow logs. Teams should capture who triggered a workflow, what data was used, which version of prompts or templates ran, and what output was produced. These logs support incident response, audit requests, and operational debugging. Without this traceability, enterprise scale becomes difficult.",
                "Change management is equally important. Prompt edits and workflow logic updates should be versioned and reviewed before release. A lightweight approval path with staging validation reduces regression risk. Organizations with clear release discipline can iterate quickly while preserving reliability for business users.",
                "Data handling policies also need explicit configuration. Set retention windows, masking rules, and environment boundaries based on regulatory requirements. Workflows involving sensitive records should include mandatory reviewer checkpoints before external distribution. This keeps automation aligned with legal and contractual obligations.",
                "When these controls are in place, adoption accelerates. Business teams gain speed, while security and compliance stakeholders keep confidence in operational integrity. That balance is the foundation of enterprise grade AI automation.",
            ],
            headings=[
                "Why Pilots Fail at Scale",
                "Role Based Access as a Core Control",
                "Traceability Through Immutable Logs",
                "Versioned Change Management",
                "Policy Driven Data Handling",
                "Speed and Governance Can Coexist",
            ],
        ),
        "author_name": "Vikram Ahuja",
        "author_title": "Enterprise Security Architect",
        "category": "Security",
        "published_date": "February 10, 2026",
        "read_time_minutes": 8,
        "tags": ["Enterprise", "Security", "Compliance", "Governance"],
        "featured": False,
        "hero_image_alt": "Security operations dashboard with enterprise automation controls",
    },
    {
        "slug": "hr-recruitment-automation-blueprint",
        "title": "Recruitment Automation Blueprint for Fast Growing Teams",
        "excerpt": "Hiring quality drops when processes become rushed and fragmented. Structured recruitment automation helps teams move faster while keeping candidate experience and evaluation quality consistent.",
        "content": _article_html(
            paragraphs=[
                "Fast growing companies often struggle to maintain hiring quality while scaling volume. Recruiters juggle sourcing, screening, scheduling, follow ups, and reporting across disconnected tools. Automation can reduce this load when workflows are designed around candidate journey stages rather than isolated tasks.",
                "The first stage is intake and role definition. AI workflows can transform hiring manager notes into structured job descriptions, skill criteria, and interview scorecards. This reduces ambiguity early and improves alignment between recruiters and interviewers. Clear definitions at this stage prevent downstream rework.",
                "The second stage is screening and coordination. Automations can parse resumes, classify candidates against criteria, and generate personalized outreach while preserving recruiter review for final shortlist decisions. Scheduling workflows can then coordinate interviewer availability and candidate preferences with minimal manual chasing.",
                "Candidate communication quality should remain a priority. Automated status updates, follow up reminders, and interview prep messages improve transparency and reduce candidate anxiety. Teams that automate communication consistently often improve acceptance rates because candidates feel informed and respected throughout the process.",
                "Analytics should guide improvement. Track time to shortlist, interview no show rate, stage wise drop off, and offer acceptance rate. These metrics reveal where workflows are working and where process design needs adjustment. Over time, automation should improve both speed and fairness.",
                "Recruitment automation is not about removing recruiter judgment. It is about removing repetitive coordination work so recruiters can spend more time evaluating fit, advising managers, and delivering a strong candidate experience.",
            ],
            headings=[
                "Hiring Bottlenecks in Growth Stages",
                "Stage One: Structured Role Intake",
                "Stage Two: Screening and Coordination",
                "Communication Drives Candidate Experience",
                "Use Metrics to Improve Continuously",
                "Keep Recruiters Focused on Judgment",
            ],
        ),
        "author_name": "Tanya Malhotra",
        "author_title": "People Operations Strategist",
        "category": "HR",
        "published_date": "January 30, 2026",
        "read_time_minutes": 7,
        "tags": ["HR", "Recruitment", "Hiring", "Automation"],
        "featured": False,
        "hero_image_alt": "Recruitment team using an automated candidate workflow board",
    },
    {
        "slug": "ecommerce-ops-order-to-refund",
        "title": "E Commerce Operations: Automating the Journey From Order to Refund",
        "excerpt": "Order workflows become fragile as channel volume increases. End to end automation across fulfillment, communication, and returns improves customer trust and operational margin.",
        "content": _article_html(
            paragraphs=[
                "E commerce teams often optimize acquisition first, then discover operational bottlenecks as volume grows. Orders, inventory updates, shipping milestones, support tickets, and refunds can quickly become disconnected processes. Customers feel this as delays and inconsistent communication. A workflow approach connects these operations and reduces failure points.",
                "Start with order orchestration. Automations should validate payment state, inventory availability, and fulfillment routing in one sequence. If a stock conflict occurs, the workflow can trigger back order communication automatically. This keeps customer messaging accurate and reduces support ticket spikes caused by uncertainty.",
                "Shipment visibility is the second critical layer. Integrate carrier events into customer updates and internal dashboards so teams can react before issues escalate. When delays happen, proactive notifications and alternate options improve trust. AI generated status summaries also help support agents resolve inquiries faster.",
                "Returns and refunds need equal attention. Automations can classify return reasons, verify policy eligibility, and route requests to the correct resolution path. For approved cases, workflows can trigger reverse logistics and refund actions without repetitive manual checks. This reduces turnaround times and improves post purchase experience.",
                "Operational analytics should connect all stages. Useful metrics include order exception rate, first response time for delivery issues, return processing duration, and refund completion time. Reviewing these regularly reveals where automation logic should be refined.",
                "Teams that automate the full order to refund lifecycle create a consistent customer experience and protect margins. The result is not only faster operations, but a stronger brand promise.",
            ],
            headings=[
                "Why Volume Creates Operational Fragility",
                "Order Orchestration as a Foundation",
                "Shipment Visibility and Proactive Communication",
                "Make Returns and Refunds Predictable",
                "End to End Metrics for Optimization",
                "Operational Consistency Builds Trust",
            ],
        ),
        "author_name": "Arjun Kapoor",
        "author_title": "E Commerce Operations Advisor",
        "category": "E Commerce",
        "published_date": "January 18, 2026",
        "read_time_minutes": 8,
        "tags": ["E Commerce", "Operations", "Fulfillment", "Returns"],
        "featured": False,
        "hero_image_alt": "E commerce operations dashboard tracking order and refund workflow stages",
    },
]


CHANGELOG_ENTRIES: list[dict[str, Any]] = [
    {
        "version": "v1.6.0",
        "date": "April 2026",
        "title": "Industry Playbooks and Expanded Public Website",
        "new_features": [
            "Launched 21 industry solution pages with tailored use case bundles.",
            "Released full public marketing site with pricing, blog, contact, and changelog sections.",
            "Added searchable 481 use case gallery with category filters and server side pagination.",
        ],
        "improvements": [
            "Improved navbar and footer responsiveness for mobile and tablet breakpoints.",
            "Enhanced pricing plan presentation with monthly and annual client side toggles.",
        ],
        "bug_fixes": [
            "Resolved intermittent nav collapse state mismatch on mobile reopen.",
            "Fixed category filter reset behavior for paginated use case views.",
        ],
        "breaking_changes": [],
    },
    {
        "version": "v1.5.0",
        "date": "March 2026",
        "title": "Workflow Template Improvements",
        "new_features": [
            "Added advanced template metadata fields for difficulty and estimated run time.",
            "Introduced featured template ranking support by usage and recency.",
        ],
        "improvements": [
            "Updated template loading logic for faster dashboard rendering.",
            "Improved template tag quality and category consistency.",
        ],
        "bug_fixes": [
            "Fixed occasional template list duplication after seed reruns.",
        ],
        "breaking_changes": [],
    },
    {
        "version": "v1.4.0",
        "date": "February 2026",
        "title": "Billing and Plan Lifecycle Enhancements",
        "new_features": [
            "Extended plan metadata for monthly and annual Razorpay plan references.",
            "Added richer plan display helpers for INR based billing views.",
        ],
        "improvements": [
            "Improved subscription status tracking fields for billing operations.",
            "Optimized invoice lookup indexes for reporting speed.",
        ],
        "bug_fixes": [
            "Corrected invoice currency display edge case in mixed environment data.",
        ],
        "breaking_changes": [],
    },
    {
        "version": "v1.3.0",
        "date": "January 2026",
        "title": "Task Execution and Output Reliability",
        "new_features": [
            "Added execution timing fields for task and step level observability.",
            "Introduced output metadata support for size and MIME tracking.",
        ],
        "improvements": [
            "Improved task index coverage for status based queue monitoring.",
            "Enhanced output query performance for recent exports.",
        ],
        "bug_fixes": [
            "Fixed rare task status update race condition during worker retries.",
        ],
        "breaking_changes": [],
    },
    {
        "version": "v1.2.0",
        "date": "December 2025",
        "title": "Security and Access Foundations",
        "new_features": [
            "Added encrypted credential vault model for integration secret storage.",
            "Expanded audit log coverage across organization level workflows.",
        ],
        "improvements": [
            "Improved default access checks for organization scoped operations.",
            "Strengthened session security defaults for production mode.",
        ],
        "bug_fixes": [
            "Resolved stale permission cache behavior after role updates.",
        ],
        "breaking_changes": [
            "Deprecated legacy credential plain text migration path.",
        ],
    },
    {
        "version": "v1.1.0",
        "date": "November 2025",
        "title": "Core Platform Foundation",
        "new_features": [
            "Introduced app factory architecture with extension based initialization.",
            "Added full model schema for organizations, users, tasks, workflows, and billing.",
            "Seeded plan catalog, integration catalog, and starter workflow templates.",
        ],
        "improvements": [
            "Improved migration strategy and database indexing defaults.",
            "Enhanced global UI shell and reusable macro library.",
        ],
        "bug_fixes": [
            "Fixed initial route registration ordering issue in development setup.",
        ],
        "breaking_changes": [],
    },
]


INTEGRATION_NAMES: list[str] = [
    "Google Workspace",
    "Gmail",
    "Google Drive",
    "Google Calendar",
    "Google Sheets",
    "Notion",
    "Airtable",
    "Slack",
    "GitHub",
    "GitLab",
    "Jira",
    "Trello",
    "Asana",
    "ClickUp",
    "HubSpot",
    "Salesforce",
    "Pipedrive",
    "Mailchimp",
    "SendGrid",
    "Twilio",
    "Zapier",
    "Make",
    "Shopify",
    "WooCommerce",
    "LinkedIn",
    "Twitter/X",
    "Facebook",
    "Instagram",
    "YouTube",
    "Dropbox",
    "OneDrive",
    "AWS S3",
    "Razorpay",
    "OpenAI",
    "Anthropic",
]

PRICING_FAQS: list[dict[str, str]] = [
    {
        "question": "Can I switch plans anytime?",
        "answer": "Yes. You can upgrade immediately, and plan downgrades are applied at the next billing cycle to avoid disruption.",
    },
    {
        "question": "Is GST included in the prices?",
        "answer": "Listed prices are base subscription charges in INR. Applicable GST is added at checkout and shown on the final invoice.",
    },
    {
        "question": "What payment methods do you accept?",
        "answer": "All paid plans are processed through Razorpay and support cards, UPI, netbanking, and enterprise billing arrangements.",
    },
    {
        "question": "What happens when I hit my task quota?",
        "answer": "You receive usage alerts before limits are reached. You can upgrade immediately to continue without interruption.",
    },
    {
        "question": "Is there a free trial for paid plans?",
        "answer": "Paid plans can be activated directly, and all subscriptions include a 7 day money back guarantee for new upgrades.",
    },
    {
        "question": "How does billing work for annual plans?",
        "answer": "Annual plans are billed once yearly at a discounted rate and include the same feature limits with better pricing efficiency.",
    },
    {
        "question": "Can I get a refund?",
        "answer": "Yes. New paid subscriptions are covered by a 7 day money back guarantee as long as refund conditions are met.",
    },
    {
        "question": "Do you offer nonprofit or student discounts?",
        "answer": "Yes. Eligible nonprofits and education organizations can request discounted pricing through our sales team.",
    },
    {
        "question": "What is a task exactly?",
        "answer": "A task is one automation execution run that produces an output, such as a report, dataset, email draft, or workflow result.",
    },
    {
        "question": "Is my data secure?",
        "answer": "Yes. We use role based access, audit trails, encryption controls, and environment best practices to protect customer data.",
    },
]


ABOUT_TEAM: list[dict[str, str]] = [
    {
        "name": "Aarohi Shah",
        "title": "Co Founder & CEO",
        "bio": "Drives product vision and customer outcomes across the platform.",
        "initials": "AS",
    },
    {
        "name": "Kunal Trivedi",
        "title": "Co Founder & CTO",
        "bio": "Leads platform architecture, security, and engineering scale.",
        "initials": "KT",
    },
    {
        "name": "Megha Krishnan",
        "title": "Head of Product",
        "bio": "Owns workflow experience and cross industry solution design.",
        "initials": "MK",
    },
    {
        "name": "Rohan Kulkarni",
        "title": "Lead Engineer",
        "bio": "Builds automation infrastructure and high reliability backend systems.",
        "initials": "RK",
    },
    {
        "name": "Ira Chatterjee",
        "title": "Head of Growth",
        "bio": "Scales go to market programs and customer education initiatives.",
        "initials": "IC",
    },
    {
        "name": "Siddharth Jain",
        "title": "Head of Customer Success",
        "bio": "Ensures customers achieve measurable automation outcomes quickly.",
        "initials": "SJ",
    },
]

ABOUT_TIMELINE: list[dict[str, str]] = [
    {"year": "2023", "title": "Founded in Ahmedabad", "detail": "Started with a mission to make AI automation practical for every team."},
    {"year": "2024", "title": "First 100 Customers", "detail": "Reached product market fit with early growth and operations teams."},
    {"year": "2025", "title": "10,000 Teams Reached", "detail": "Expanded platform capabilities and enterprise grade controls."},
    {"year": "2026", "title": "481 Task Types Live", "detail": "Launched broad cross industry automation library and public playbooks."},
]


@public_bp.get("/")
def home() -> str:
    """Render landing page with dynamic previews and resilient stat loading."""

    plans: list[Plan] = []
    featured_templates: list[WorkflowTemplate] = []

    task_total = 50000000
    integration_total = 32

    try:
        plans = (
            Plan.query.filter(Plan.is_active.is_(True))
            .order_by(
                case((Plan.price_monthly_inr < 0, 1), else_=0),
                Plan.price_monthly_inr.asc(),
            )
            .limit(3)
            .all()
        )
    except Exception:
        current_app.logger.exception("Unable to load pricing preview plans")

    try:
        featured_templates = (
            WorkflowTemplate.query.filter(
                WorkflowTemplate.is_active.is_(True),
                WorkflowTemplate.is_featured.is_(True),
            )
            .order_by(WorkflowTemplate.usage_count.desc(), WorkflowTemplate.created_at.desc())
            .limit(6)
            .all()
        )
    except Exception:
        current_app.logger.exception("Unable to load featured workflow templates")

    try:
        task_total_query = db.session.query(func.count(AutomationTask.id)).scalar()
        task_total = int(task_total_query or 0)
    except Exception:
        current_app.logger.exception("Unable to load task count stat")

    try:
        integration_total_query = (
            db.session.query(func.count(Integration.id))
            .filter(Integration.is_active.is_(True))
            .scalar()
        )
        integration_total = int(integration_total_query or 0)
    except Exception:
        current_app.logger.exception("Unable to load integration count stat")

    return render_template(
        "public/index.html",
        plans=plans,
        featured_templates=featured_templates,
        home_stats={
            "task_total": task_total,
            "integration_total": integration_total,
            "team_total": 10000,
            "task_types_total": len(TASK_REGISTRY_SUMMARY),
        },
    )


@public_bp.get("/features")
def features() -> str:
    """Render features overview grouped by task categories."""

    tasks_by_category: dict[str, list[dict[str, Any]]] = {}
    for task in TASK_REGISTRY_SUMMARY:
        tasks_by_category.setdefault(task["category"], []).append(task)

    return render_template(
        "public/features.html",
        task_categories=TASK_CATEGORIES,
        tasks_by_category=tasks_by_category,
        integration_names=INTEGRATION_NAMES,
    )


@public_bp.route("/pricing", methods=["GET", "POST"])
def pricing() -> str:
    """Render pricing page and process enterprise sales inquiries."""

    billing_cycle = request.args.get("billing_cycle", "monthly").strip().lower()
    if billing_cycle not in {"monthly", "annual"}:
        billing_cycle = "monthly"

    plans: list[Plan] = []
    try:
        plans = (
            Plan.query.filter(Plan.is_active.is_(True))
            .order_by(
                case((Plan.price_monthly_inr < 0, 1), else_=0),
                Plan.price_monthly_inr.asc(),
            )
            .all()
        )
    except Exception:
        current_app.logger.exception("Unable to load pricing plans")

    plans_by_slug = {plan.slug: plan for plan in plans}

    enterprise_form = EnterpriseInquiryForm()
    if enterprise_form.validate_on_submit():
        recipient = _mail_default_recipient()
        subject = f"Enterprise Inquiry: {enterprise_form.company_name.data}"
        message = Message(
            subject=subject,
            recipients=[recipient],
            reply_to=enterprise_form.work_email.data,
            body=(
                "New enterprise inquiry\n\n"
                f"Company: {enterprise_form.company_name.data}\n"
                f"Contact Name: {enterprise_form.name.data}\n"
                f"Email: {enterprise_form.work_email.data}\n"
                f"Team Size: {enterprise_form.team_size.data}\n"
                f"Message:\n{enterprise_form.message.data}\n"
            ),
        )
        try:
            mail.send(message)
            flash("Thanks for reaching out. Our enterprise team will contact you within one business day.", "success")
            return redirect(url_for("public.pricing"))
        except Exception:
            current_app.logger.exception("Failed to send enterprise inquiry email")
            flash("We could not send your inquiry right now. Please try again in a few minutes.", "danger")
    elif request.method == "POST":
        flash("Please review the enterprise form fields and try again.", "danger")

    return render_template(
        "public/pricing.html",
        plans=plans,
        plans_by_slug=plans_by_slug,
        billing_cycle=billing_cycle,
        enterprise_form=enterprise_form,
        pricing_faqs=PRICING_FAQS,
    )


@public_bp.get("/use-cases")
def use_cases() -> str:
    """Render searchable and paginated use case gallery."""

    selected_category = request.args.get("category", "all").strip().lower().replace("-", "_")
    search_query = strip_sql_injection(request.args.get("search", "").strip())
    page = max(_safe_int(request.args.get("page", 1), 1), 1)

    if selected_category != "all" and selected_category not in TASK_CATEGORIES:
        selected_category = "all"

    filtered = TASK_REGISTRY_SUMMARY

    if selected_category != "all":
        filtered = [task for task in filtered if task["category"] == selected_category]

    if search_query:
        query_lower = search_query.lower()
        filtered = [
            task
            for task in filtered
            if query_lower in task["name"].lower() or query_lower in task["description"].lower()
        ]

    pagination = _paginate_list(filtered, page=page, per_page=24)

    return render_template(
        "public/use_cases.html",
        task_registry=TASK_REGISTRY_SUMMARY,
        page_items=pagination.items,
        pagination=pagination,
        task_categories=TASK_CATEGORIES,
        selected_category=selected_category,
        search_query=search_query,
        total_use_cases=len(TASK_REGISTRY_SUMMARY),
        filtered_total=len(filtered),
    )


@public_bp.get("/industry/<slug>")
def industry(slug: str) -> str:
    """Render industry specific marketing page."""

    industry_data = INDUSTRY_DATA.get(slug)
    if industry_data is None:
        abort(404)

    task_ids = industry_data.get("task_ids", [])
    industry_tasks = [TASK_LOOKUP_BY_ID[task_id] for task_id in task_ids if task_id in TASK_LOOKUP_BY_ID]

    return render_template(
        "public/industry.html",
        industry=industry_data,
        industry_slug=slug,
        industry_tasks=industry_tasks,
        task_categories=TASK_CATEGORIES,
    )


@public_bp.get("/blog")
def blog() -> str:
    """Render blog listing with featured post and pagination."""

    page = max(_safe_int(request.args.get("page", 1), 1), 1)
    selected_category = request.args.get("category", "all").strip().lower()

    categories = sorted({post["category"] for post in BLOG_POSTS})

    filtered_posts = BLOG_POSTS
    if selected_category != "all":
        filtered_posts = [
            post for post in BLOG_POSTS if _slugify_label(post["category"]) == selected_category
        ]

    featured_post = next((post for post in filtered_posts if post["featured"]), None)
    listing_posts = filtered_posts
    if featured_post is not None:
        listing_posts = [post for post in filtered_posts if post["slug"] != featured_post["slug"]]

    pagination = _paginate_list(listing_posts, page=page, per_page=4)

    return render_template(
        "public/blog.html",
        featured_post=featured_post,
        pagination=pagination,
        categories=categories,
        selected_category=selected_category,
        popular_posts=BLOG_POSTS,
    )


@public_bp.get("/blog/<slug>")
def blog_post(slug: str) -> str:
    """Render a single blog post detail page."""

    post = next((item for item in BLOG_POSTS if item["slug"] == slug), None)
    if post is None:
        abort(404)

    same_category = [
        item
        for item in BLOG_POSTS
        if item["slug"] != post["slug"] and item["category"] == post["category"]
    ]
    fallback = [item for item in BLOG_POSTS if item["slug"] != post["slug"]]
    related_posts = (same_category + fallback)[:3]

    return render_template("public/blog_post.html", post=post, related_posts=related_posts)


@public_bp.get("/about")
def about() -> str:
    """Render company mission and team page."""

    return render_template(
        "public/about.html",
        team_members=ABOUT_TEAM,
        timeline=ABOUT_TIMELINE,
        backers=["Apex Ventures", "BlueOcean Capital", "Meridian Angels", "ScaleFoundry", "FirstSpark Labs"],
    )


@public_bp.get("/contact")
def contact() -> str:
    """Render contact page with empty form."""

    form = ContactForm()
    return render_template("public/contact.html", form=form)


@public_bp.post("/contact")
def contact_submit() -> str:
    """Validate contact form and send email via Flask-Mail."""

    form = ContactForm()
    if form.validate_on_submit():
        recipient = _mail_default_recipient()
        message = Message(
            subject=f"Contact Form: {form.subject.data}",
            recipients=[recipient],
            reply_to=form.email.data,
            body=(
                "New contact submission\n\n"
                f"Name: {form.name.data}\n"
                f"Email: {form.email.data}\n"
                f"Company: {form.company.data or 'Not provided'}\n"
                f"Subject: {form.subject.data}\n\n"
                f"Message:\n{form.message.data}\n"
            ),
        )
        try:
            mail.send(message)
            flash("Thank you! We'll get back to you within 24 hours.", "success")
            return redirect(url_for("public.contact"))
        except Exception:
            current_app.logger.exception("Failed to send contact email")
            flash("We could not send your message right now. Please try again shortly.", "danger")
            return render_template("public/contact.html", form=form), 500

    flash("Please fix the highlighted fields and submit again.", "danger")
    return render_template("public/contact.html", form=form), 400


@public_bp.get("/changelog")
def changelog() -> str:
    """Render public changelog page."""

    return render_template("public/changelog.html", changelog_entries=CHANGELOG_ENTRIES)


@public_bp.post("/newsletter/subscribe")
def newsletter_subscribe() -> str:
    """Basic newsletter endpoint used by footer and blog forms."""

    email = request.form.get("email", "").strip()
    if not email or "@" not in email:
        flash("Please enter a valid email address to subscribe.", "warning")
    else:
        flash("You are subscribed. Watch your inbox for weekly automation tips.", "success")

    return redirect(request.referrer or url_for("public.home"))
