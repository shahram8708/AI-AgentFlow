"""Agent execution orchestration service and task registry."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import redis
from flask import current_app

from app.extensions import cache
from app.extensions import db
from app.models import AuditLog, AutomationTask, Organization, Plan, TaskOutput, TaskStep, UsageRecord
from app.services.file_service import file_service
from app.services.llm_service import LLMService, LLMServiceError


# Module level registry required by task launcher, validation, and execution flows.
TASK_REGISTRY: dict[str, dict[str, Any]] = {}


CATEGORY_META: dict[str, dict[str, str]] = {
    "web_research": {"display": "Web Research", "icon": "bi-search"},
    "writing": {"display": "Writing", "icon": "bi-pencil-square"},
    "email_automation": {"display": "Email Automation", "icon": "bi-envelope"},
    "scheduling": {"display": "Calendar and Scheduling", "icon": "bi-calendar3"},
    "data_entry": {"display": "Data Entry", "icon": "bi-input-cursor-text"},
    "code_writing": {"display": "Code Writing", "icon": "bi-code-slash"},
    "data_scraping": {"display": "Data Scraping", "icon": "bi-database-down"},
    "spreadsheet_automation": {
        "display": "Spreadsheet Automation",
        "icon": "bi-table",
    },
    "api_integrations": {"display": "API Integrations", "icon": "bi-diagram-3"},
    "ecommerce": {"display": "Shopping and E-commerce", "icon": "bi-cart3"},
    "social_media": {"display": "Social Media", "icon": "bi-chat-dots"},
    "news_aggregation": {"display": "News Aggregation", "icon": "bi-newspaper"},
    "travel_research": {"display": "Travel Research", "icon": "bi-airplane"},
    "job_automation": {"display": "Job Applications", "icon": "bi-briefcase"},
    "customer_support": {"display": "Customer Support", "icon": "bi-headset"},
    "finance_banking": {"display": "Finance and Banking", "icon": "bi-cash-stack"},
    "legal": {"display": "Legal", "icon": "bi-bank"},
    "healthcare_medicine": {
        "display": "Healthcare and Medicine",
        "icon": "bi-heart-pulse",
    },
    "education": {"display": "Education", "icon": "bi-mortarboard"},
    "image_media": {"display": "Image and Media", "icon": "bi-camera"},
    "database_management": {
        "display": "Database Management",
        "icon": "bi-server",
    },
    "it_systems": {"display": "IT and Systems", "icon": "bi-hdd-network"},
    "project_management": {
        "display": "Project Management",
        "icon": "bi-kanban",
    },
    "crm_sales": {"display": "CRM and Sales", "icon": "bi-people"},
    "hr_recruitment": {"display": "HR and Recruitment", "icon": "bi-person-badge"},
    "marketing_seo": {"display": "Marketing and SEO", "icon": "bi-megaphone"},
    "complex_workflows": {
        "display": "Complex Workflows",
        "icon": "bi-bezier2",
    },
    "real_estate": {"display": "Real Estate", "icon": "bi-house-door"},
    "supply_chain": {"display": "Supply Chain", "icon": "bi-truck"},
    "government_compliance": {
        "display": "Government and Compliance",
        "icon": "bi-shield-check",
    },
    "lifestyle_productivity": {
        "display": "Lifestyle and Productivity",
        "icon": "bi-stars",
    },
    "manufacturing": {"display": "Manufacturing", "icon": "bi-gear"},
    "nonprofit": {"display": "Nonprofit", "icon": "bi-heart"},
    "research": {"display": "Research", "icon": "bi-journal-text"},
    "personal_productivity": {
        "display": "Personal Productivity",
        "icon": "bi-lightning-charge",
    },
    "media": {"display": "Media", "icon": "bi-play-btn"},
    "food_hospitality": {"display": "Food", "icon": "bi-cup-hot"},
    "agriculture": {"display": "Agriculture", "icon": "bi-flower1"},
    "logistics": {"display": "Logistics", "icon": "bi-box-seam"},
    "insurance": {"display": "Insurance", "icon": "bi-shield"},
    "telecom": {"display": "Telecom", "icon": "bi-broadcast"},
    "energy_utilities": {"display": "Energy", "icon": "bi-lightbulb"},
}


CATEGORY_COLORS: dict[str, str] = {
    "web_research": "#1a56db",
    "code_writing": "#059669",
    "email_automation": "#d97706",
    "writing": "#7c3aed",
    "data_scraping": "#0891b2",
    "finance_banking": "#dc2626",
    "healthcare_medicine": "#16a34a",
    "legal": "#9333ea",
    "education": "#2563eb",
    "scheduling": "#0f766e",
    "data_entry": "#0d9488",
    "spreadsheet_automation": "#15803d",
    "api_integrations": "#0f4c81",
    "ecommerce": "#ea580c",
    "social_media": "#c026d3",
    "news_aggregation": "#1d4ed8",
    "travel_research": "#0284c7",
    "job_automation": "#2563eb",
    "customer_support": "#334155",
    "image_media": "#0ea5e9",
    "database_management": "#0f766e",
    "it_systems": "#1f2937",
    "project_management": "#0369a1",
    "crm_sales": "#b45309",
    "hr_recruitment": "#4f46e5",
    "marketing_seo": "#be185d",
    "complex_workflows": "#4338ca",
    "real_estate": "#7c2d12",
    "supply_chain": "#0369a1",
    "government_compliance": "#475569",
    "lifestyle_productivity": "#0f766e",
    "manufacturing": "#64748b",
    "nonprofit": "#db2777",
    "research": "#1d4ed8",
    "personal_productivity": "#0f766e",
    "media": "#7c3aed",
    "food_hospitality": "#b45309",
    "agriculture": "#15803d",
    "logistics": "#0891b2",
    "insurance": "#1d4ed8",
    "telecom": "#0284c7",
    "energy_utilities": "#ca8a04",
}


LEGAL_DISCLAIMER = (
    "This AI-generated draft is for informational purposes only. Have all legal "
    "documents reviewed and signed off by a qualified advocate licensed in the "
    "relevant jurisdiction before use."
)
MEDICAL_DISCLAIMER = (
    "This output is for informational purposes only and is not medical advice. "
    "Consult a qualified healthcare professional before making any medical decisions."
)
FINANCIAL_DISCLAIMER = (
    "This output is informational and should not be treated as investment, tax, "
    "or accounting advice. Consult qualified professionals before making decisions."
)


def _to_label(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _make_options(values: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in values]


def _text_field(
    name: str,
    required: bool = True,
    placeholder: str = "",
    max_length: int | None = None,
    min_length: int | None = None,
    help_text: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": _to_label(name),
        "type": "text",
        "required": required,
        "placeholder": placeholder,
    }
    if max_length is not None:
        field["max_length"] = max_length
    if min_length is not None:
        field["min_length"] = min_length
    if help_text:
        field["help_text"] = help_text
    return field


def _textarea_field(
    name: str,
    required: bool = True,
    placeholder: str = "",
    max_length: int | None = None,
    min_length: int | None = None,
    help_text: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": _to_label(name),
        "type": "textarea",
        "required": required,
        "placeholder": placeholder,
    }
    if max_length is not None:
        field["max_length"] = max_length
    if min_length is not None:
        field["min_length"] = min_length
    if help_text:
        field["help_text"] = help_text
    return field


def _number_field(
    name: str,
    required: bool = True,
    placeholder: str = "",
    help_text: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": _to_label(name),
        "type": "number",
        "required": required,
        "placeholder": placeholder,
    }
    if help_text:
        field["help_text"] = help_text
    return field


def _url_field(name: str, required: bool = False, placeholder: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "label": _to_label(name),
        "type": "url",
        "required": required,
        "placeholder": placeholder,
    }


def _email_field(name: str, required: bool = False, placeholder: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "label": _to_label(name),
        "type": "email",
        "required": required,
        "placeholder": placeholder,
    }


def _select_field(
    name: str,
    options: list[tuple[str, str]],
    required: bool = True,
    default: str | None = None,
    help_text: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": _to_label(name),
        "type": "select",
        "required": required,
        "options": _make_options(options),
    }
    if default is not None:
        field["default"] = default
    if help_text:
        field["help_text"] = help_text
    return field


def _checkboxes_field(
    name: str,
    options: list[tuple[str, str]],
    required: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": _to_label(name),
        "type": "checkboxes",
        "required": required,
        "options": _make_options(options),
    }


def _guess_field_type(field_name: str) -> str:
    lowered = field_name.lower()
    if "url" in lowered:
        return "url"
    if "email" in lowered:
        return "email"
    if any(
        token in lowered
        for token in [
            "count",
            "number",
            "years",
            "year",
            "months",
            "days",
            "minutes",
            "duration",
            "budget",
            "amount",
            "price",
            "salary",
            "quantity",
            "age",
            "size",
        ]
    ):
        return "number"
    if any(token in lowered for token in ["list", "categories", "platforms", "types"]):
        return "textarea"
    if any(
        token in lowered
        for token in [
            "description",
            "details",
            "notes",
            "context",
            "requirements",
            "summary",
            "history",
            "objective",
            "question",
            "content",
            "prompt",
            "focus",
            "aspects",
            "analysis",
            "data",
            "criteria",
            "constraints",
            "skills",
            "features",
            "message",
            "topics",
            "issue",
        ]
    ):
        return "textarea"
    return "text"


def _fields_from_names(field_names: list[str], required_count: int = 2) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for idx, field_name in enumerate(field_names):
        is_required = idx < required_count
        field_type = _guess_field_type(field_name)
        if field_type == "textarea":
            fields.append(
                _textarea_field(
                    field_name,
                    required=is_required,
                    max_length=3000,
                    min_length=5 if is_required else None,
                    placeholder=f"Enter {field_name.replace('_', ' ')}",
                )
            )
        elif field_type == "number":
            fields.append(_number_field(field_name, required=is_required))
        elif field_type == "url":
            fields.append(_url_field(field_name, required=is_required))
        elif field_type == "email":
            fields.append(_email_field(field_name, required=is_required))
        else:
            fields.append(
                _text_field(
                    field_name,
                    required=is_required,
                    max_length=500,
                    min_length=2 if is_required else None,
                    placeholder=f"Enter {field_name.replace('_', ' ')}",
                )
            )
    return fields


def _default_fields_for_category(category: str, strategy: str) -> list[dict[str, Any]]:
    if strategy == "research_grounded":
        return [
            _textarea_field(
                "research_topic",
                required=True,
                min_length=8,
                max_length=2500,
                placeholder="Describe what needs to be researched",
            ),
            _select_field(
                "depth_level",
                [
                    ("quick", "Quick"),
                    ("standard", "Standard"),
                    ("deep", "Deep"),
                ],
                required=True,
                default="standard",
            ),
            _textarea_field(
                "specific_requirements",
                required=False,
                max_length=1200,
                placeholder="Key requirements, constraints, or focus areas",
            ),
            _select_field(
                "output_format",
                [
                    ("markdown", "Markdown Report"),
                    ("structured", "Structured Sections"),
                    ("bullet_points", "Bullet Summary"),
                ],
                required=False,
                default="markdown",
            ),
        ]
    if strategy == "code":
        return [
            _textarea_field(
                "technical_requirement",
                required=True,
                min_length=10,
                max_length=3000,
                placeholder="Describe the coding requirement in detail",
            ),
            _select_field(
                "programming_language",
                [
                    ("python", "Python"),
                    ("javascript", "JavaScript"),
                    ("typescript", "TypeScript"),
                    ("java", "Java"),
                    ("go", "Go"),
                    ("other", "Other"),
                ],
                default="python",
            ),
            _textarea_field(
                "existing_code_or_context",
                required=False,
                max_length=4000,
                placeholder="Paste existing code, errors, or implementation context",
            ),
            _select_field(
                "output_format",
                [
                    ("code", "Code"),
                    ("markdown", "Markdown with code"),
                    ("json", "JSON"),
                ],
                required=False,
                default="code",
            ),
        ]
    if strategy == "data_analysis":
        return [
            _textarea_field(
                "data_description",
                required=True,
                min_length=8,
                max_length=2500,
                placeholder="Describe dataset shape, columns, and quality",
            ),
            _textarea_field(
                "analysis_goal",
                required=True,
                min_length=8,
                max_length=2000,
                placeholder="What decisions or insights are needed",
            ),
            _select_field(
                "output_format",
                [
                    ("table", "Table"),
                    ("report", "Narrative report"),
                    ("csv", "CSV format"),
                    ("json", "JSON"),
                ],
                required=False,
                default="table",
            ),
            _textarea_field(
                "constraints",
                required=False,
                max_length=1000,
                placeholder="Any constraints on assumptions, calculations, or delivery",
            ),
        ]
    if category == "email_automation":
        return [
            _textarea_field(
                "email_context",
                required=True,
                min_length=8,
                max_length=2500,
                placeholder="Describe email objective and context",
            ),
            _select_field(
                "tone",
                [
                    ("formal", "Formal"),
                    ("professional", "Professional"),
                    ("friendly", "Friendly"),
                ],
                default="professional",
            ),
            _textarea_field(
                "recipient_details",
                required=False,
                max_length=1000,
                placeholder="Recipient role, company, and constraints",
            ),
            _select_field(
                "output_format",
                [
                    ("markdown", "Markdown"),
                    ("plain_text", "Plain text"),
                ],
                required=False,
                default="markdown",
            ),
        ]
    return [
        _textarea_field(
            "task_objective",
            required=True,
            min_length=8,
            max_length=2500,
            placeholder="Describe what the agent should achieve",
        ),
        _textarea_field(
            "context_details",
            required=True,
            min_length=8,
            max_length=2500,
            placeholder="Provide all relevant context and constraints",
        ),
        _select_field(
            "tone_or_style",
            [
                ("professional", "Professional"),
                ("formal", "Formal"),
                ("conversational", "Conversational"),
            ],
            required=False,
            default="professional",
        ),
        _select_field(
            "output_format",
            [
                ("markdown", "Markdown"),
                ("structured", "Structured"),
                ("plain_text", "Plain text"),
            ],
            required=False,
            default="markdown",
        ),
    ]


def _strategy_defaults(strategy: str) -> tuple[str, int, int]:
    if strategy == "research_grounded":
        return "markdown", 420, 300
    if strategy == "code":
        return "code", 600, 420
    if strategy == "data_analysis":
        return "table", 480, 300
    return "markdown", 360, 240


def _disclaimer_for_task(task_id: str, category: str, task_name: str) -> str:
    if task_id.startswith("17.") or task_id.startswith("LE") or category == "legal":
        return LEGAL_DISCLAIMER
    if (
        task_id.startswith("18.")
        or task_id.startswith("H")
        or category == "healthcare_medicine"
    ):
        return MEDICAL_DISCLAIMER
    lower_name = task_name.lower()
    if (
        task_id.startswith("16.")
        or task_id.startswith("F")
        or category == "finance_banking"
        or "tax" in lower_name
        or "salary" in lower_name
    ):
        return FINANCIAL_DISCLAIMER
    return ""


def _role_for_category(category: str, strategy: str) -> str:
    role_map = {
        "web_research": "research analyst",
        "email_automation": "business communication specialist",
        "scheduling": "executive scheduling coordinator",
        "data_entry": "operations data specialist",
        "code_writing": "senior software engineer",
        "data_scraping": "data extraction analyst",
        "spreadsheet_automation": "data operations analyst",
        "api_integrations": "integration engineer",
        "ecommerce": "e-commerce intelligence analyst",
        "social_media": "social media strategist",
        "news_aggregation": "news editor",
        "travel_research": "travel research specialist",
        "job_automation": "career strategy consultant",
        "customer_support": "customer success specialist",
        "finance_banking": "financial analyst",
        "legal": "legal documentation specialist",
        "healthcare_medicine": "medical research specialist",
        "education": "learning design specialist",
        "image_media": "media research specialist",
        "database_management": "database engineer",
        "it_systems": "platform reliability engineer",
        "project_management": "project management specialist",
        "crm_sales": "sales operations strategist",
        "hr_recruitment": "HR operations specialist",
        "marketing_seo": "marketing strategist",
        "complex_workflows": "automation architect",
        "real_estate": "real estate analyst",
        "supply_chain": "supply chain analyst",
        "government_compliance": "compliance specialist",
        "lifestyle_productivity": "personal productivity advisor",
        "manufacturing": "manufacturing operations specialist",
        "nonprofit": "nonprofit strategy advisor",
        "research": "research program specialist",
        "personal_productivity": "productivity systems coach",
        "media": "media intelligence analyst",
        "food_hospitality": "food and hospitality specialist",
        "agriculture": "agri operations specialist",
        "logistics": "logistics strategist",
        "insurance": "insurance operations analyst",
        "telecom": "telecom domain specialist",
        "energy_utilities": "energy market analyst",
    }
    if category in role_map:
        return role_map[category]
    if strategy == "code":
        return "senior software engineer"
    if strategy == "data_analysis":
        return "data analysis specialist"
    return "domain specialist"


def _generate_typical_steps(task_name: str, strategy: str, requires_web_search: bool) -> list[str]:
    if strategy == "research_grounded":
        return [
            f"Clarify objective and scope for {task_name}",
            "Search reliable and current public sources",
            "Extract key evidence and cross-verify major claims",
            "Synthesize findings into a structured narrative",
            "Deliver citation-aware output with assumptions and limitations",
        ]
    if strategy == "code":
        return [
            f"Analyze requirements and constraints for {task_name}",
            "Design implementation approach and edge-case handling",
            "Generate production quality code and supporting artifacts",
            "Validate logic, error paths, and expected outputs",
            "Summarize implementation decisions and usage guidance",
        ]
    if strategy == "data_analysis":
        return [
            f"Profile inputs and define analysis goals for {task_name}",
            "Normalize data assumptions and transformation rules",
            "Compute metrics and trend or variance insights",
            "Package outputs in structured tables and summaries",
            "Provide recommendations and validation checks",
        ]
    steps = [
        f"Understand user objective for {task_name}",
        "Organize inputs into an execution-ready structure",
        "Draft complete output with clear sections",
        "Apply quality review for clarity and correctness",
        "Deliver final polished version with actionable next steps",
    ]
    if requires_web_search:
        steps.insert(1, "Gather current web references where needed")
        steps = steps[:5]
    return steps


def _build_system_prompt(
    task_id: str,
    name: str,
    description: str,
    category: str,
    category_display: str,
    strategy: str,
    requires_web_search: bool,
    input_fields: list[dict[str, Any]],
    output_format: str,
) -> str:
    role = _role_for_category(category, strategy)
    field_labels = ", ".join(field["label"] for field in input_fields[:6])
    web_instruction = (
        "Use live web research, prioritize official or high credibility sources, "
        "and provide citation references for factual statements."
        if requires_web_search
        else "Rely on user provided context and your domain reasoning, and do not "
        "invent external facts that are not provided."
    )
    output_instruction = (
        "Produce complete implementation ready code with clear structure, robust error "
        "handling, and concise inline comments only where logic is non-obvious."
        if strategy == "code"
        else "Produce a polished, structured output with clear headings, concise language, "
        "and concrete recommendations that can be executed directly."
    )
    disclaimer = _disclaimer_for_task(task_id, category, name)

    prompt = (
        f"You are an expert {role}. Execute the task '{name}' in the {category_display} "
        f"domain. Task context: {description}. Start by interpreting every user input field "
        f"carefully, especially: {field_labels}. Define assumptions explicitly when details "
        f"are missing, and prefer precise statements over generic phrasing. {web_instruction} "
        f"{output_instruction} Ensure the final response is delivered in {output_format} "
        f"format, with an executive summary first, followed by detailed sections, and a short "
        f"quality checklist at the end. Verify internal consistency, avoid contradictory claims, "
        f"and keep recommendations specific, measurable, and relevant to Indian business context "
        f"where applicable."
    )
    if disclaimer:
        prompt = f"{prompt} {disclaimer}"
    return prompt


def _add_task(
    task_id: str,
    name: str,
    category: str,
    description: str,
    execution_strategy: str,
    requires_web_search: bool,
    input_fields: list[dict[str, Any]] | None = None,
    output_format: str | None = None,
    timeout_seconds: int | None = None,
    estimated_seconds: int | None = None,
    icon: str | None = None,
    industry_tags: list[str] | None = None,
    difficulty: str | None = None,
) -> None:
    if task_id in TASK_REGISTRY:
        raise ValueError(f"Duplicate task id detected: {task_id}")

    category_info = CATEGORY_META.get(category, {"display": _to_label(category), "icon": "bi-stars"})
    resolved_output_format, resolved_timeout, resolved_estimated = _strategy_defaults(
        execution_strategy
    )
    resolved_output = output_format or resolved_output_format
    resolved_timeout = timeout_seconds or resolved_timeout
    resolved_estimated = estimated_seconds or resolved_estimated
    resolved_fields = input_fields or _default_fields_for_category(category, execution_strategy)
    resolved_icon = icon or category_info.get("icon", "bi-stars")
    resolved_difficulty = difficulty
    if not resolved_difficulty:
        if execution_strategy == "code":
            resolved_difficulty = "advanced"
        elif execution_strategy == "research_grounded":
            resolved_difficulty = "intermediate"
        else:
            resolved_difficulty = "beginner"

    TASK_REGISTRY[task_id] = {
        "id": task_id,
        "name": name,
        "category": category,
        "category_display": category_info.get("display", _to_label(category)),
        "description": description,
        "execution_strategy": execution_strategy,
        "requires_web_search": requires_web_search,
        "typical_steps": _generate_typical_steps(name, execution_strategy, requires_web_search),
        "system_prompt": _build_system_prompt(
            task_id=task_id,
            name=name,
            description=description,
            category=category,
            category_display=category_info.get("display", _to_label(category)),
            strategy=execution_strategy,
            requires_web_search=requires_web_search,
            input_fields=resolved_fields,
            output_format=resolved_output,
        ),
        "input_fields": resolved_fields,
        "output_format": resolved_output,
        "timeout_seconds": resolved_timeout,
        "estimated_seconds": resolved_estimated,
        "icon": resolved_icon,
        "industry_tags": industry_tags or [],
        "difficulty": resolved_difficulty,
    }


NUMERIC_GROUPS: dict[int, dict[str, Any]] = {
    1: {
        "category": "web_research",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Deep Multi-Source Research Synthesis",
            "Real-Time Product Price Comparison",
            "Academic Literature Search and Summarization",
            "Company Background Research",
            "Regulatory and Compliance Research",
            "Technology Stack Discovery",
            "Wikipedia Research and Cross-Referencing",
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
    2: {
        "category": "writing",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Multi-Section Report Generation",
            "Template Population from Data",
            "Bulk File Renaming and Organization",
            "Document Summarization",
            "Resume and Cover Letter Generation",
            "Contract Redlining and Markup Drafting",
            "Code File Documentation Generation",
            "Presentation Outline and Slide Script Creation",
            "Policy and Procedure Document Drafting",
            "Data-Driven Narrative Writing",
        ],
    },
    3: {
        "category": "email_automation",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Inbox Triage and Priority Classification",
            "Personalized Cold Email Drafting at Scale",
            "Email Thread Summarization",
            "Meeting Follow-Up Email Drafting",
            "Customer Complaint Response Drafting",
            "Email Campaign Copy Writing",
            "Vendor and Supplier Correspondence",
            "Legal Notice and Demand Letter Drafting",
            "Unsubscribe and Inbox Cleaning Assistance",
            "Proposal and Quote Email Writing",
        ],
    },
    4: {
        "category": "scheduling",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Meeting Scheduling from Availability Context",
            "Calendar Event Creation from Email or Notes",
            "Weekly Agenda Preparation",
            "Recurring Event and Deadline Tracking",
            "Interview Scheduling Coordination",
        ],
    },
    5: {
        "category": "data_entry",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Web Form Auto-Population",
            "Government Form Assistance and Population",
            "CRM Data Entry from Meeting Notes",
            "Insurance Claims Data Preparation",
            "Survey Response Data Consolidation",
            "E-commerce Order Form Completion",
        ],
    },
    6: {
        "category": "code_writing",
        "execution_strategy": "code",
        "requires_web_search": False,
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
            "Infrastructure-as-Code Generation",
            "Regex Pattern Generation",
            "Code Migration Between Languages or Frameworks",
            "Security Vulnerability Review",
            "Environment Setup and Dependency Configuration",
            "Algorithm Design and Implementation",
        ],
    },
    7: {
        "category": "data_scraping",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
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
    8: {
        "category": "spreadsheet_automation",
        "execution_strategy": "data_analysis",
        "requires_web_search": False,
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
    9: {
        "category": "api_integrations",
        "execution_strategy": "code",
        "requires_web_search": False,
        "names": [
            "REST API Data Retrieval and Processing",
            "Webhook Configuration Assistance",
            "GraphQL Query Writing",
            "Authentication Flow Implementation",
            "API Response Schema Documentation",
            "Third-Party Service Configuration Code",
            "Data Transformation Between API Formats",
        ],
    },
    10: {
        "category": "ecommerce",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Multi-Site Price Tracking and Alerting Logic",
            "Product Availability Checking",
            "Coupon and Promo Code Research",
            "Product Specification Comparison",
            "Wholesale Supplier Discovery",
            "Amazon Product Research",
            "Subscription Management Research",
        ],
    },
    11: {
        "category": "social_media",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    12: {
        "category": "news_aggregation",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Industry Newsletter Compilation",
            "Earnings and Financial News Aggregation",
            "Regulatory Filing and Announcement Monitoring",
            "Research Paper Alert Aggregation",
            "Competitor Blog and Content Monitoring",
            "Social News Trend Identification",
        ],
    },
    13: {
        "category": "travel_research",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Flight Option Research",
            "Hotel Research and Comparison",
            "Visa and Entry Requirement Research",
            "Travel Itinerary Building",
            "Travel Insurance Research",
            "Ground Transportation Research",
            "Currency and Cost-of-Living Research",
        ],
    },
    14: {
        "category": "job_automation",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    15: {
        "category": "customer_support",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    16: {
        "category": "finance_banking",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Equity Research Report Drafting",
            "Financial Statement Analysis",
            "Macro-Economic Data Research",
            "Crypto and DeFi Market Research",
            "Tax Law Research",
            "Budget Variance Report Writing",
            "M&A Target Research",
        ],
    },
    17: {
        "category": "legal",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    18: {
        "category": "healthcare_medicine",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
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
    19: {
        "category": "education",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    20: {
        "category": "image_media",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Stock Image Search and Curation",
            "Brand Asset Competitive Analysis",
            "Icon and Illustration Library Research",
            "Video Content Research",
            "Image Metadata and Attribution Research",
        ],
    },
    21: {
        "category": "database_management",
        "execution_strategy": "code",
        "requires_web_search": False,
        "names": [
            "Database Schema Design",
            "SQL Query Generation for Analytics",
            "Data Migration Script Writing",
            "Index Optimization Recommendation",
            "ETL Pipeline Code Writing",
            "NoSQL Data Model Design",
        ],
    },
    22: {
        "category": "it_systems",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Infrastructure Documentation Writing",
            "Network Diagram Description and Design",
            "Log Analysis and Error Diagnosis",
            "Security Policy Drafting",
            "CI/CD Pipeline Configuration",
            "Cloud Cost Optimization Research",
            "Container and Orchestration Configuration",
        ],
    },
    23: {
        "category": "project_management",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Project Plan Generation",
            "Risk Register Creation",
            "Status Report Writing",
            "Meeting Agenda Preparation",
            "RACI Matrix Creation",
            "Retrospective Facilitation Framework",
        ],
    },
    24: {
        "category": "crm_sales",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    25: {
        "category": "hr_recruitment",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
    26: {
        "category": "marketing_seo",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Keyword Research and Clustering",
            "SEO-Optimized Blog Post Writing",
            "Technical SEO Audit Preparation",
            "Competitor Content Gap Analysis",
            "Ad Copy Writing",
            "Landing Page Copy Writing",
            "Email Marketing A/B Test Variant Creation",
            "Marketing Brief Writing",
            "Content Repurposing Across Formats",
            "Influencer Outreach Email Writing",
        ],
    },
    27: {
        "category": "complex_workflows",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "End-to-End Lead Research and Outreach Workflow",
            "Competitive Intelligence Report Production",
            "Research to Writing to Publish Workflow",
            "RFP Response Assembly",
            "Due Diligence Checklist Completion",
        ],
    },
    28: {
        "category": "real_estate",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Comparable Sales Analysis",
            "Rental Market Research",
            "Neighborhood Research Report",
            "Zoning and Permit Research",
            "HOA Research",
        ],
    },
    29: {
        "category": "supply_chain",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Supplier Qualification Research",
            "Shipping Rate Comparison",
            "Import/Export Regulation Research",
            "Logistics Technology Vendor Research",
        ],
    },
    30: {
        "category": "government_compliance",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Business License Application Assistance",
            "Tax Form Preparation Support",
            "Compliance Calendar Building",
            "Grant Application Drafting",
            "Regulatory Comment Letter Drafting",
        ],
    },
    31: {
        "category": "lifestyle_productivity",
        "execution_strategy": "writing",
        "requires_web_search": False,
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
}


TASK_OVERRIDES: dict[str, dict[str, Any]] = {
    "10.5": {"execution_strategy": "writing", "requires_web_search": True},
    "10.6": {"execution_strategy": "writing", "requires_web_search": True},
    "10.7": {"execution_strategy": "writing", "requires_web_search": False},
    "11.3": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "11.4": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "11.5": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "11.7": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "14.7": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "19.7": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "22.5": {"execution_strategy": "code", "requires_web_search": False},
    "22.6": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "22.7": {"execution_strategy": "code", "requires_web_search": False},
    "24.3": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "24.4": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "24.6": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "25.7": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "26.1": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "26.3": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "26.4": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "31.1": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "31.3": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "31.4": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "31.6": {"execution_strategy": "research_grounded", "requires_web_search": True},
    "31.10": {"execution_strategy": "research_grounded", "requires_web_search": True},
}


TASK_FIELD_OVERRIDES: dict[str, list[dict[str, Any]]] = {
    "1.1": [
        {
            "name": "research_topic",
            "label": "Research Topic or Question",
            "type": "textarea",
            "required": True,
            "placeholder": "e.g., Competitive landscape of B2B SaaS CRM tools in India",
            "help_text": "Be specific. The more detail you provide, the better the research output.",
            "min_length": 10,
            "max_length": 2000,
        },
        {
            "name": "depth_level",
            "label": "Research Depth",
            "type": "select",
            "required": True,
            "options": [
                {"value": "quick", "label": "Quick (2 to 3 min, 3 sources)"},
                {"value": "standard", "label": "Standard (5 to 7 min, 5 to 8 sources)"},
                {"value": "deep", "label": "Deep (10 to 15 min, 10 plus sources)"},
            ],
            "default": "standard",
            "help_text": "Deeper research takes longer but produces more comprehensive results.",
        },
        {
            "name": "output_format",
            "label": "Output Format",
            "type": "select",
            "required": False,
            "options": [
                {"value": "markdown", "label": "Formatted Report (Markdown)"},
                {"value": "structured", "label": "Structured Sections"},
                {"value": "bullet_points", "label": "Bullet Point Summary"},
            ],
            "default": "markdown",
        },
        {
            "name": "specific_aspects",
            "label": "Specific Aspects to Focus On",
            "type": "textarea",
            "required": False,
            "placeholder": "e.g., Focus on pricing, market share, and customer reviews",
            "help_text": "Optional. Leave blank to let the agent determine the most relevant aspects.",
            "max_length": 500,
        },
        _select_field(
            "target_audience",
            [
                ("general", "General"),
                ("expert", "Expert"),
                ("executive", "Executive"),
            ],
            required=False,
            default="general",
        ),
    ],
    "1.2": [
        _text_field("product_name", required=True, min_length=2, max_length=200),
        _textarea_field("product_specifications", required=False, max_length=1200),
        _checkboxes_field(
            "target_platforms",
            [
                ("amazon_india", "Amazon India"),
                ("flipkart", "Flipkart"),
                ("snapdeal", "Snapdeal"),
                ("meesho", "Meesho"),
                ("official_website", "Official Website"),
                ("other", "Other"),
            ],
            required=False,
        ),
        _text_field("budget_range_inr", required=False, max_length=80),
    ],
    "1.3": [
        _textarea_field("research_question", required=True, min_length=8, max_length=2000),
        _text_field("academic_field", required=True, min_length=2, max_length=200),
        _select_field(
            "date_range",
            [
                ("last_year", "Last year"),
                ("last_3_years", "Last 3 years"),
                ("last_5_years", "Last 5 years"),
                ("all_time", "All time"),
            ],
            default="last_5_years",
        ),
        _select_field(
            "paper_count",
            [("5", "5"), ("10", "10"), ("20", "20")],
            default="10",
        ),
        _select_field(
            "include_methodology",
            [("yes", "Yes"), ("no", "No")],
            required=False,
            default="yes",
        ),
    ],
    "1.4": _fields_from_names(
        [
            "company_name",
            "company_website",
            "research_focus",
            "output_length",
        ],
        required_count=2,
    ),
    "1.5": _fields_from_names(
        [
            "regulation_topic",
            "jurisdiction",
            "industry_sector",
            "specific_regulation_name",
        ],
        required_count=3,
    ),
    "1.6": _fields_from_names(
        [
            "company_or_website",
            "focus_areas",
            "competitor_comparison",
        ],
        required_count=2,
    ),
    "1.7": _fields_from_names(
        ["topic", "depth", "cross_reference_topics", "fact_check_claims"],
        required_count=2,
    ),
    "1.8": _fields_from_names(
        ["topic_or_product", "platforms", "sentiment_period", "language"],
        required_count=2,
    ),
    "1.9": _fields_from_names(
        [
            "job_title_or_skill",
            "location",
            "experience_level",
            "industry",
            "include_salary_data",
        ],
        required_count=3,
    ),
    "1.10": _fields_from_names(
        [
            "location",
            "property_type",
            "transaction_type",
            "budget_inr",
            "bedrooms",
            "specific_requirements",
        ],
        required_count=4,
    ),
    "1.11": _fields_from_names(
        [
            "event_topic_or_industry",
            "time_period",
            "location_preference",
            "event_type",
        ],
        required_count=2,
    ),
    "1.12": _fields_from_names(
        [
            "topics",
            "time_period",
            "news_sources_preference",
            "briefing_length",
            "language",
        ],
        required_count=2,
    ),
    "1.13": _fields_from_names(
        [
            "product_or_company",
            "review_platforms",
            "min_review_count",
            "competitor_comparison",
        ],
        required_count=2,
    ),
    "1.14": _fields_from_names(
        [
            "technology_or_topic",
            "patent_scope",
            "applicant_company",
            "date_range",
            "include_citations",
        ],
        required_count=2,
    ),
    "1.15": _fields_from_names(
        [
            "organization_type",
            "sector",
            "location",
            "stage",
            "budget_requirement_inr",
        ],
        required_count=2,
    ),
}


TASK_FIELD_NAME_HINTS: dict[str, list[str]] = {
    "2.1": [
        "report_title",
        "report_purpose",
        "sections_needed",
        "raw_data_or_notes",
        "target_audience",
        "tone",
        "output_length",
    ],
    "2.2": ["template_text", "data_to_fill", "output_format", "repetitions"],
    "2.3": ["file_list", "naming_convention", "organization_criteria"],
    "2.4": [
        "document_text",
        "summary_length",
        "summary_style",
        "focus_topics",
        "output_for",
    ],
    "2.5": [
        "job_title_applying_for",
        "job_description",
        "candidate_experience",
        "candidate_education",
        "target_company",
    ],
    "2.6": [
        "original_contract_text",
        "party_representing",
        "key_concerns",
        "desired_changes",
        "jurisdiction",
    ],
    "2.7": [
        "code_content",
        "programming_language",
        "documentation_style",
        "include_examples",
        "audience",
    ],
    "2.8": [
        "presentation_topic",
        "presentation_purpose",
        "target_audience",
        "key_message",
        "number_of_slides",
    ],
    "2.9": [
        "policy_name",
        "organization_name",
        "policy_purpose",
        "key_rules_or_requirements",
        "affected_employees_or_departments",
    ],
    "2.10": [
        "data_or_metrics",
        "narrative_purpose",
        "target_audience",
        "key_insight_to_highlight",
        "word_count",
    ],
    "3.1": ["email_list", "classification_criteria", "email_context", "output_format"],
    "3.2": [
        "prospect_list",
        "product_or_service",
        "value_proposition",
        "sender_name",
        "email_goal",
    ],
    "3.3": [
        "email_thread",
        "desired_output",
        "include_participants",
        "identify_open_questions",
    ],
    "3.4": [
        "meeting_notes_or_transcript",
        "attendees",
        "sender_name",
        "meeting_type",
        "action_items",
    ],
    "3.5": [
        "complaint_text",
        "company_name",
        "product_or_service",
        "resolution_available",
        "respondent_name",
    ],
    "3.6": [
        "campaign_goal",
        "product_or_service",
        "target_audience_description",
        "email_sequence_count",
        "offer_or_cta",
    ],
    "3.7": [
        "correspondence_type",
        "vendor_name",
        "company_name",
        "subject_matter",
        "desired_outcome",
    ],
    "3.8": [
        "notice_type",
        "sender_name_or_company",
        "recipient_name_or_company",
        "situation_description",
        "deadline_days",
    ],
    "3.9": [
        "subscription_list",
        "email_provider",
        "additional_filters_to_create",
        "preferred_inbox_structure",
    ],
    "3.10": [
        "client_name_and_company",
        "service_or_product_description",
        "pricing_breakdown",
        "project_timeline",
        "payment_terms",
    ],
}


def _description_from_name(name: str, category: str, requires_web_search: bool) -> str:
    category_display = CATEGORY_META.get(category, {}).get("display", _to_label(category))
    data_source_phrase = (
        "using current web and public source data"
        if requires_web_search
        else "using provided inputs and domain best practices"
    )
    return (
        f"Executes {name.lower()} in the {category_display.lower()} workflow, "
        f"{data_source_phrase}, and returns a structured production-ready output."
    )


def _build_numeric_tasks() -> None:
    for group_number, group_config in NUMERIC_GROUPS.items():
        names: list[str] = group_config["names"]
        base_category = group_config["category"]
        base_strategy = group_config["execution_strategy"]
        base_requires_web = group_config["requires_web_search"]

        for idx, name in enumerate(names, start=1):
            task_id = f"{group_number}.{idx}"
            override = TASK_OVERRIDES.get(task_id, {})
            category = override.get("category", base_category)
            strategy = override.get("execution_strategy", base_strategy)
            requires_web_search = override.get("requires_web_search", base_requires_web)
            description = _description_from_name(name, category, bool(requires_web_search))

            if task_id in TASK_FIELD_OVERRIDES:
                input_fields = TASK_FIELD_OVERRIDES[task_id]
            elif task_id in TASK_FIELD_NAME_HINTS:
                input_fields = _fields_from_names(TASK_FIELD_NAME_HINTS[task_id], required_count=2)
            else:
                input_fields = _default_fields_for_category(category, strategy)

            _add_task(
                task_id=task_id,
                name=name,
                category=category,
                description=description,
                execution_strategy=strategy,
                requires_web_search=bool(requires_web_search),
                input_fields=input_fields,
                industry_tags=[category.replace("_", " ")],
            )


INDUSTRY_GROUPS: dict[str, dict[str, Any]] = {
    "F": {
        "count": 20,
        "category": "finance_banking",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Equity Portfolio Rebalancing Strategy",
            "Cash Flow Forecast Modeling",
            "Credit Risk Screening Summary",
            "Loan Covenants Compliance Checklist",
            "Mutual Fund Performance Comparison",
            "Personal Tax Saving Allocation Plan",
            "Treasury Management Dashboard Brief",
            "Invoice Discounting Feasibility Study",
            "Working Capital Optimization Plan",
            "Cap Table Dilution Scenario Builder",
            "IPO Readiness Gap Assessment",
            "Expense Leakage Detection Review",
            "Payment Reconciliation Exception Audit",
            "Fundraising Narrative Financial Pack",
            "Banking Partner Selection Matrix",
            "Debt Restructuring Option Analysis",
            "ESG Finance Metrics Compilation",
            "Finance Policy Manual Drafting",
            "Fraud Signal Detection Notes",
            "Investor Update Financial Summary",
        ],
    },
    "H": {
        "count": 17,
        "category": "healthcare_medicine",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Hospital Care Pathway Research",
            "Clinical Coding Compliance Review",
            "Pharmacovigilance Signal Summary",
            "Patient Education Handout Draft",
            "Discharge Planning Checklist",
            "Healthcare Cost Benchmark Analysis",
            "Telemedicine Workflow Design",
            "Medical Device Risk File Summary",
            "Clinical SOP Drafting",
            "Hospital Operations KPI Dashboard",
            "ICU Protocol Comparison",
            "Nursing Competency Matrix Draft",
            "Care Quality Incident Trend Review",
            "Medical Procurement Policy Draft",
            "Hospital Accreditation Gap Assessment",
            "Clinical Communication Script Drafting",
            "Public Health Advisory Summary",
        ],
    },
    "LE": {
        "count": 12,
        "category": "legal",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Commercial Contract Clause Drafting",
            "Litigation Brief Structure Draft",
            "Legal Risk Register Preparation",
            "Corporate Resolution Drafting",
            "Arbitration Notice Drafting",
            "Policy Compliance Legal Memo",
            "Intellectual Property Filing Brief",
            "Employment Policy Legal Review",
            "Data Protection Impact Statement",
            "Shareholder Agreement Clause Draft",
            "Regulatory Investigation Response Draft",
            "Legal Due Diligence Summary",
        ],
    },
    "ED": {
        "count": 12,
        "category": "education",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Curriculum Mapping Framework",
            "Student Assessment Blueprint",
            "Course Outcome Alignment Report",
            "Learning Gap Diagnostic Plan",
            "Teacher Training Module Draft",
            "Question Paper Design Pack",
            "Rubric and Grading Guide",
            "Academic Counseling Conversation Script",
            "School Communication Newsletter Draft",
            "Classroom Intervention Strategy",
            "Academic Progress Reporting Template",
            "Education Program Impact Summary",
        ],
    },
    "EC": {
        "count": 14,
        "category": "ecommerce",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Marketplace Listing Optimization",
            "Category Demand Heatmap Analysis",
            "Conversion Funnel Drop-Off Review",
            "Cart Abandonment Recovery Strategy",
            "Seller Review Risk Monitoring",
            "Warehouse Stockout Forecast",
            "Assortment Expansion Opportunity Scan",
            "Product Content SEO Audit",
            "Return and Refund Policy Optimization",
            "Shipping SLA Performance Review",
            "Promotion Calendar Draft",
            "Customer Cohort Retention Analysis",
            "Marketplace Fee Impact Simulation",
            "E-commerce Profitability Health Check",
        ],
    },
    "M": {
        "count": 13,
        "category": "manufacturing",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Production Plan Sequencing",
            "Machine Utilization Review",
            "Quality Defect Root Cause Summary",
            "Maintenance Window Planner",
            "Inventory Buffer Optimization",
            "Plant Safety SOP Draft",
            "Procurement Lead-Time Risk Report",
            "Batch Traceability Template",
            "Vendor Quality Scorecard",
            "Manufacturing Cost Reduction Plan",
            "Capacity Expansion Feasibility Brief",
            "OEE Performance Dashboard Blueprint",
            "Factory Compliance Checklist",
        ],
    },
    "NP": {
        "count": 6,
        "category": "nonprofit",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Donor Communication Narrative",
            "Program Outcome Measurement Plan",
            "Grant Impact Storyline Draft",
            "Volunteer Engagement Framework",
            "Fundraising Campaign Brief",
            "Nonprofit Governance Policy Draft",
        ],
    },
    "RA": {
        "count": 9,
        "category": "research",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Research Question Framing",
            "Methodology Selection Guidance",
            "Evidence Synthesis Matrix",
            "Literature Gap Identification",
            "Research Ethics Checklist",
            "Citation Network Mapping",
            "Experimental Design Critique",
            "Research Abstract Drafting",
            "Peer Review Preparation Brief",
        ],
    },
    "PP": {
        "count": 15,
        "category": "personal_productivity",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Weekly Priority Map",
            "Deep Work Calendar Planner",
            "Habit Tracker Design",
            "Goal Breakdown Framework",
            "Decision Journal Template",
            "Personal Knowledge Management Setup",
            "Meeting Notes Consolidation",
            "Email Zero Workflow",
            "Task Delegation Planner",
            "Focus Sprint Checklist",
            "Energy Management Routine",
            "Personal OKR Draft",
            "Daily Shutdown Ritual",
            "Meeting Preparation Playbook",
            "Quarterly Reflection Report",
        ],
    },
    "RE": {
        "count": 10,
        "category": "real_estate",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Property Yield Analysis",
            "Builder Reputation Check",
            "Rental Demand Trend Report",
            "Micro-Market Growth Scan",
            "Lease Agreement Risk Summary",
            "Commercial Property Site Comparison",
            "Tenant Screening Template",
            "Property Tax and Charges Review",
            "Real Estate Deal Due Diligence",
            "Exit Value Scenario Planning",
        ],
    },
    "HR": {
        "count": 12,
        "category": "hr_recruitment",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Hiring Funnel Health Review",
            "Candidate Interview Debrief Pack",
            "Role Competency Matrix Draft",
            "Workforce Planning Sheet",
            "Internal Mobility Framework",
            "Employee Engagement Action Plan",
            "Attrition Driver Analysis Note",
            "Policy Handbook Clause Drafting",
            "Learning and Development Pathway",
            "Performance Calibration Guide",
            "Manager Feedback Toolkit",
            "Compensation Communication Script",
        ],
    },
    "MA": {
        "count": 12,
        "category": "marketing_seo",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Brand Positioning Narrative",
            "Campaign Messaging Matrix",
            "Audience Persona Deep Dive",
            "Channel Mix Planning",
            "SEO Opportunity Prioritization",
            "Landing Funnel Conversion Audit",
            "Creative Brief for Ad Production",
            "Marketing KPI Scorecard",
            "PR Story Pitch Draft",
            "Webinar Promotion Playbook",
            "Lifecycle Marketing Journey Map",
            "Content Calendar Strategy",
        ],
    },
    "IT": {
        "count": 15,
        "category": "it_systems",
        "execution_strategy": "code",
        "requires_web_search": False,
        "names": [
            "Incident Runbook Draft",
            "Service Reliability Improvement Plan",
            "Observability Dashboard Spec",
            "Access Control Review Script",
            "Environment Hardening Checklist",
            "Deployment Rollback Plan",
            "Infrastructure Drift Detection Logic",
            "Alert Noise Reduction Rules",
            "Backup and Restore Validation Script",
            "System Capacity Forecast Model",
            "Patch Management Workflow",
            "Security Baseline Configuration",
            "Disaster Recovery Exercise Pack",
            "API Gateway Policy Draft",
            "Platform Migration Runbook",
        ],
    },
    "CS": {
        "count": 10,
        "category": "customer_support",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Support SLA Breach Analysis",
            "Escalation Decision Tree",
            "Agent Coaching Feedback Template",
            "Case Deflection Knowledge Draft",
            "Support Queue Capacity Plan",
            "Customer Churn Risk Ticket Review",
            "First Response Quality Guide",
            "Complaint Resolution Framework",
            "Support Reporting Dashboard Brief",
            "Customer Follow-up Script Set",
        ],
    },
    "G": {
        "count": 10,
        "category": "government_compliance",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Public Consultation Brief Draft",
            "Regulatory Filing Checklist",
            "Policy Impact Note",
            "Government Scheme Eligibility Review",
            "Licensing Documentation Pack",
            "Tender Submission Readiness Review",
            "Compliance Audit Trail Template",
            "Public Disclosure Draft",
            "Regulator Response Letter Draft",
            "Departmental Circular Summary",
        ],
    },
    "ME": {
        "count": 9,
        "category": "media",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Media Coverage Landscape Scan",
            "Editorial Calendar Design",
            "Press Release Briefing Draft",
            "Channel Content Mix Analysis",
            "Audience Engagement Signal Review",
            "Narrative Risk Monitoring",
            "Video Script Research Pack",
            "Podcast Topic Pipeline",
            "Media Partnership Prospecting",
        ],
    },
    "TH": {
        "count": 8,
        "category": "travel_research",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Destination Suitability Comparison",
            "Seasonal Travel Risk Brief",
            "Business Trip Optimization Plan",
            "Family Travel Planning Checklist",
            "Travel Spend Savings Analysis",
            "Adventure Activity Safety Research",
            "Travel Document Readiness Review",
            "Local Experience Curation",
        ],
    },
    "FR": {
        "count": 7,
        "category": "food_hospitality",
        "execution_strategy": "writing",
        "requires_web_search": False,
        "names": [
            "Restaurant Menu Engineering Review",
            "Kitchen SOP Documentation",
            "Food Cost Control Sheet",
            "Catering Proposal Draft",
            "Recipe Standardization Plan",
            "Guest Experience Improvement Brief",
            "Food Safety Compliance Checklist",
        ],
    },
    "AG": {
        "count": 7,
        "category": "agriculture",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Crop Planning Advisory",
            "Soil Health Improvement Roadmap",
            "Irrigation Efficiency Assessment",
            "Farm Input Cost Benchmark",
            "Agri Market Price Watch",
            "Post-Harvest Loss Reduction Plan",
            "Government Subsidy Opportunity Scan",
        ],
    },
    "LT": {
        "count": 8,
        "category": "logistics",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Route Optimization Feasibility",
            "Carrier Performance Scorecard",
            "Freight Cost Leakage Review",
            "Last Mile Delivery Benchmark",
            "Warehouse Throughput Analysis",
            "Dispatch SLA Compliance Check",
            "Cross-Border Shipment Risk Scan",
            "Logistics Vendor Shortlist",
        ],
    },
    "IN": {
        "count": 8,
        "category": "insurance",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Policy Coverage Comparison",
            "Claims Documentation Readiness",
            "Underwriting Risk Summary",
            "Renewal Premium Optimization Note",
            "Insurance Product Gap Analysis",
            "Claims Turnaround Trend Review",
            "Distribution Partner Assessment",
            "Policyholder Communication Draft",
        ],
    },
    "TE": {
        "count": 6,
        "category": "telecom",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Network Plan Benchmark",
            "Subscriber Churn Diagnostics",
            "Tariff Pack Comparison",
            "Spectrum Policy Monitoring",
            "Telecom Compliance Alert Summary",
            "Customer Experience Optimization Brief",
        ],
    },
    "EU": {
        "count": 7,
        "category": "energy_utilities",
        "execution_strategy": "research_grounded",
        "requires_web_search": True,
        "names": [
            "Energy Tariff Trend Analysis",
            "Renewable Procurement Strategy",
            "Utility Reliability Risk Report",
            "Power Demand Forecast Brief",
            "Carbon Reporting Data Pack",
            "Grid Policy Update Tracker",
            "Energy Efficiency Opportunity Scan",
        ],
    },
}


INDUSTRY_FIELD_NAMES: dict[str, list[str]] = {
    "finance_banking": [
        "business_context",
        "financial_objective",
        "time_period",
        "risk_constraints",
        "amount_inr",
    ],
    "healthcare_medicine": [
        "medical_context",
        "patient_or_population_profile",
        "clinical_question",
        "data_or_reports_available",
        "urgency_level",
    ],
    "legal": [
        "legal_context",
        "parties_involved",
        "jurisdiction",
        "specific_issue",
        "desired_outcome",
    ],
    "education": [
        "learning_context",
        "target_learners",
        "objectives",
        "duration_or_timeline",
        "assessment_needs",
    ],
    "ecommerce": [
        "business_goal",
        "product_or_category",
        "marketplace_scope",
        "budget_inr",
        "target_metric",
    ],
    "manufacturing": [
        "plant_context",
        "process_or_line",
        "current_issues",
        "target_improvement",
        "timeline",
    ],
    "nonprofit": [
        "program_context",
        "beneficiary_group",
        "impact_objective",
        "available_data",
    ],
    "research": [
        "research_objective",
        "scope",
        "method_constraints",
        "output_expectation",
    ],
    "personal_productivity": [
        "personal_goal",
        "current_workload_context",
        "time_available",
        "constraints",
    ],
    "real_estate": [
        "location_scope",
        "property_goal",
        "budget_inr",
        "decision_criteria",
    ],
    "hr_recruitment": [
        "role_or_people_context",
        "current_hr_challenge",
        "target_outcome",
        "timeline",
    ],
    "marketing_seo": [
        "campaign_context",
        "audience",
        "primary_goal",
        "channel_constraints",
    ],
    "it_systems": [
        "system_context",
        "technical_problem",
        "environment_details",
        "target_state",
        "implementation_constraints",
    ],
    "customer_support": [
        "support_context",
        "customer_issue",
        "policy_constraints",
        "resolution_goal",
    ],
    "government_compliance": [
        "regulatory_context",
        "entity_type",
        "compliance_requirement",
        "jurisdiction",
        "deadline",
    ],
    "media": [
        "content_context",
        "target_audience",
        "story_angle",
        "distribution_channels",
    ],
    "travel_research": [
        "travel_context",
        "destination_or_route",
        "budget_inr",
        "time_window",
    ],
    "food_hospitality": [
        "operations_context",
        "service_or_menu_focus",
        "target_customer_segment",
        "constraints",
    ],
    "agriculture": [
        "farm_context",
        "crop_or_activity",
        "location",
        "season_or_timeline",
    ],
    "logistics": [
        "supply_chain_context",
        "shipment_or_network_scope",
        "cost_or_sla_goal",
        "constraints",
    ],
    "insurance": [
        "policy_context",
        "coverage_or_claim_goal",
        "risk_profile",
        "jurisdiction",
    ],
    "telecom": [
        "network_or_business_context",
        "service_scope",
        "target_metric",
        "compliance_constraints",
    ],
    "energy_utilities": [
        "energy_context",
        "asset_or_market_scope",
        "target_outcome",
        "policy_or_cost_constraints",
    ],
}


def _build_industry_tasks() -> None:
    for prefix, config in INDUSTRY_GROUPS.items():
        category = config["category"]
        strategy = config["execution_strategy"]
        requires_web_search = bool(config["requires_web_search"])
        names: list[str] = config["names"]
        count: int = config["count"]

        for idx in range(1, count + 1):
            task_id = f"{prefix}{idx}"
            if idx <= len(names):
                name = names[idx - 1]
            else:
                name = f"{CATEGORY_META.get(category, {}).get('display', _to_label(category))} Task {idx}"

            field_names = INDUSTRY_FIELD_NAMES.get(
                category,
                ["task_objective", "context_details", "constraints", "output_format"],
            )
            input_fields = _fields_from_names(field_names, required_count=3)
            description = (
                f"Specialized industry workflow for {name.lower()} with domain terminology, "
                "Indian compliance context where applicable, and structured executive output."
            )

            _add_task(
                task_id=task_id,
                name=name,
                category=category,
                description=description,
                execution_strategy=strategy,
                requires_web_search=requires_web_search,
                input_fields=input_fields,
                industry_tags=[prefix.lower(), category.replace("_", " ")],
                difficulty="intermediate" if strategy != "code" else "advanced",
            )


def _initialize_registry() -> None:
    _build_numeric_tasks()
    _build_industry_tasks()


_initialize_registry()


if len(TASK_REGISTRY) != 481:
    raise RuntimeError(
        f"TASK_REGISTRY must contain exactly 481 entries, found {len(TASK_REGISTRY)}"
    )


def _cache_get(cache_key: str) -> Any:
    try:
        return cache.get(cache_key)
    except Exception:
        return None


def _cache_set(cache_key: str, value: Any, timeout: int = 600) -> None:
    try:
        cache.set(cache_key, value, timeout=timeout)
    except Exception:
        return


def get_task_config(task_type: str) -> dict[str, Any]:
    """Return task configuration by task type."""

    cache_key = f"task_registry:config:{task_type}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return deepcopy(cached)

    if task_type not in TASK_REGISTRY:
        raise KeyError(f"Task type '{task_type}' was not found in TASK_REGISTRY")

    task_config = TASK_REGISTRY[task_type]
    _cache_set(cache_key, task_config)
    return deepcopy(task_config)


def get_tasks_by_category(category: str) -> list[dict[str, Any]]:
    """Return all task configurations for a category."""

    cache_key = f"task_registry:category:{category}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return deepcopy(cached)

    tasks = [task for task in TASK_REGISTRY.values() if task.get("category") == category]
    _cache_set(cache_key, tasks)
    return deepcopy(tasks)


def get_tasks_requiring_web_search() -> list[dict[str, Any]]:
    """Return task configurations requiring web search."""

    cache_key = "task_registry:web_search_required"
    cached = _cache_get(cache_key)
    if cached is not None:
        return deepcopy(cached)

    tasks = [task for task in TASK_REGISTRY.values() if task.get("requires_web_search") is True]
    _cache_set(cache_key, tasks)
    return deepcopy(tasks)


def search_tasks(query: str) -> list[dict[str, Any]]:
    """Search tasks by name, description, or category."""

    search_query = (query or "").strip().lower()
    if not search_query:
        return sorted(deepcopy(list(TASK_REGISTRY.values())), key=lambda task: task["name"])

    results = []
    for task in TASK_REGISTRY.values():
        name = str(task.get("name", "")).lower()
        description = str(task.get("description", "")).lower()
        category = str(task.get("category", "")).lower()
        if search_query in name or search_query in description or search_query in category:
            results.append(deepcopy(task))
    return sorted(results, key=lambda task: task["name"])


def get_all_categories() -> list[str]:
    """Return sorted unique category list present in registry."""

    categories = {task["category"] for task in TASK_REGISTRY.values()}
    return sorted(categories)


def get_task_count() -> int:
    """Return total registry count."""

    return len(TASK_REGISTRY)


def _is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    if "." not in parsed.netloc:
        return False
    return True


def validate_task_inputs(task_type: str, input_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate task input payload based on task input field definitions."""

    errors: list[str] = []

    try:
        task_config = get_task_config(task_type)
    except KeyError:
        return False, [f"Unknown task type: {task_type}"]

    for field in task_config.get("input_fields", []):
        field_name = str(field.get("name", "")).strip()
        if not field_name:
            continue

        field_label = field.get("label", field_name)
        field_type = str(field.get("type", "text"))
        required = bool(field.get("required", False))
        value = input_data.get(field_name)

        if field_type == "checkboxes":
            if value is None:
                normalized_values: list[str] = []
            elif isinstance(value, list):
                normalized_values = [str(item).strip() for item in value if str(item).strip()]
            else:
                normalized_values = [
                    item.strip() for item in str(value).split(",") if item.strip()
                ]

            if required and not normalized_values:
                errors.append(f"{field_label} is required.")

            valid_values = {
                option.get("value")
                for option in field.get("options", [])
                if option.get("value") is not None
            }
            if valid_values and any(item not in valid_values for item in normalized_values):
                errors.append(f"{field_label} contains an invalid option.")
            continue

        if value is None:
            normalized_value = ""
        elif isinstance(value, str):
            normalized_value = value.strip()
        else:
            normalized_value = str(value).strip()

        if required and not normalized_value:
            errors.append(f"{field_label} is required.")
            continue

        if not normalized_value:
            continue

        min_length = field.get("min_length")
        max_length = field.get("max_length")

        if min_length is not None and len(normalized_value) < int(min_length):
            errors.append(f"{field_label} must be at least {min_length} characters.")
        if max_length is not None and len(normalized_value) > int(max_length):
            errors.append(f"{field_label} must be at most {max_length} characters.")

        if field_type == "select":
            allowed_values = {
                option.get("value")
                for option in field.get("options", [])
                if option.get("value") is not None
            }
            if allowed_values and normalized_value not in allowed_values:
                errors.append(f"{field_label} contains an invalid selection.")

        if field_type == "url" and normalized_value and not _is_valid_url(normalized_value):
            errors.append(f"{field_label} must be a valid URL.")

        if field_type == "email" and normalized_value:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", normalized_value):
                errors.append(f"{field_label} must be a valid email address.")

        if field_type == "number" and normalized_value:
            try:
                float(normalized_value)
            except ValueError:
                errors.append(f"{field_label} must be a valid number.")

    return (len(errors) == 0, errors)


def build_gemini_prompt(task_type: str, input_data: dict[str, Any]) -> str:
    """Build final Gemini prompt from system prompt and user inputs."""

    task_config = get_task_config(task_type)
    prompt_lines: list[str] = [task_config["system_prompt"], "", "Task Input Details:"]

    for field in task_config.get("input_fields", []):
        field_name = field.get("name")
        field_label = field.get("label", field_name)
        value = input_data.get(field_name)

        if isinstance(value, list):
            display_value = ", ".join(str(item) for item in value if str(item).strip())
        else:
            display_value = "" if value is None else str(value)

        prompt_lines.append(f"[{field_label}]: {display_value}")

    return "\n".join(prompt_lines).strip()


def get_task_display_name(task_type: str) -> str:
    """Return display name for task type with fallback to task type."""

    task = TASK_REGISTRY.get(task_type)
    if task is None:
        return task_type
    return str(task.get("name") or task_type)


class AgentRunner:
    """Service responsible for agent task execution orchestration."""

    def build_payload(self, task_type: str, input_json: dict[str, Any]) -> dict[str, Any]:
        """Build normalized payload for downstream worker execution."""

        task_config = get_task_config(task_type)
        return {
            "task_type": task_type,
            "task_name": task_config.get("name"),
            "execution_strategy": task_config.get("execution_strategy"),
            "requires_web_search": task_config.get("requires_web_search", False),
            "input": input_json,
            "prompt": build_gemini_prompt(task_type, input_json),
            "meta": {"source": "platform", "version": "1.0.0"},
        }

    def run_preview(self, task_type: str, input_json: dict[str, Any]) -> dict[str, Any]:
        """Return local preview response for task setup confirmation."""

        task_config = get_task_config(task_type)
        return {
            "status": "queued",
            "task_type": task_type,
            "task_name": task_config.get("name"),
            "input_keys": sorted(input_json.keys()),
            "estimated_seconds": task_config.get("estimated_seconds"),
        }


class AgentExecutionEngine:
    """Core AI agent execution engine for background task execution."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.task: AutomationTask | None = None
        self.task_config: dict[str, Any] | None = None
        self.llm = LLMService()
        self.redis_client = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        )
        self.log_channel = f"task_log:{task_id}"

    def execute(self) -> bool:
        """Main execution method. Returns True on success, False on failure."""

        try:
            if not self._load_task():
                return False

            self._update_task_status("running")
            self.task.started_at = datetime.utcnow()
            db.session.commit()

            self._publish_log(
                message=f"Starting task: {self.task.task_name}",
                level="info",
            )

            self._create_steps()
            output_text = self._execute_steps()
            _task_output = self._store_output(output_text)

            self.task.status = "done"
            self.task.completed_at = datetime.utcnow()
            db.session.commit()

            usage_record = UsageRecord(
                org_id=self.task.org_id,
                user_id=self.task.user_id,
                task_id=self.task.id,
                usage_type="task_run",
                units_consumed=1,
            )
            db.session.add(usage_record)
            db.session.commit()

            duration_seconds = 0
            if self.task.completed_at and self.task.started_at:
                duration_seconds = int(
                    (self.task.completed_at - self.task.started_at).total_seconds()
                )

            audit_log = AuditLog(
                org_id=self.task.org_id,
                user_id=self.task.user_id,
                action="task.completed",
                resource_type="task",
                resource_id=str(self.task.id),
                extra_json={
                    "task_type": self.task.task_type,
                    "duration_seconds": duration_seconds,
                    "output_length": len(output_text),
                },
            )
            db.session.add(audit_log)
            db.session.commit()

            from app.services.notification_service import NotificationService

            notif_service = NotificationService()
            notif_service.notify_task_complete(self.task)

            self._publish_log(
                message="Task completed successfully!",
                level="complete",
                output_chunk=output_text[:500],
            )

            self._check_quota_warning()
            return True

        except LLMServiceError as exc:
            self._handle_failure(exc)
            return False
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.error(
                "Unexpected error executing task %s: %s",
                self.task_id,
                exc,
                exc_info=True,
            )
            self._handle_failure(exc)
            return False

    def _load_task(self) -> bool:
        """Load task from DB and validate it can be executed."""

        task = AutomationTask.query.get(UUID(self.task_id))
        if task is None:
            current_app.logger.error("Task not found: %s", self.task_id)
            return False

        if task.status not in ["pending"]:
            current_app.logger.warning(
                "Task not in pending state: %s (%s)",
                self.task_id,
                task.status,
            )
            return False

        try:
            task_config = get_task_config(task.task_type)
        except KeyError:
            task.status = "failed"
            task.error_message = (
                f"Unsupported task type '{task.task_type}'. "
                "Please reconfigure and try again."
            )
            task.completed_at = datetime.utcnow()
            db.session.commit()
            current_app.logger.error("Task type missing in registry: %s", task.task_type)
            return False

        self.task = task
        self.task_config = task_config
        return True

    def _create_steps(self) -> None:
        """Create TaskStep records based on task configuration typical steps."""

        typical_steps = list(self.task_config.get("typical_steps") or [])

        if not typical_steps:
            typical_steps = [
                "Analyze Inputs",
                "Generate Output",
            ]

        for index, step_name in enumerate(typical_steps):
            step = TaskStep(
                task_id=self.task.id,
                step_number=index + 1,
                step_name=step_name,
                status="pending",
            )
            db.session.add(step)

        db.session.commit()
        self._publish_log(f"Created {len(typical_steps)} execution steps", level="info")

    def _execute_steps(self) -> str:
        """Execute all steps and return final output text."""

        steps = (
            TaskStep.query.filter_by(task_id=self.task.id)
            .order_by(TaskStep.step_number.asc())
            .all()
        )
        total_steps = len(steps)
        _strategy = self.task_config.get("execution_strategy", "writing")
        _requires_web_search = bool(self.task_config.get("requires_web_search", False))

        accumulated_context = ""
        final_output = ""

        for step in steps:
            self._update_step_status(step, "running")
            step.started_at = datetime.utcnow()
            db.session.commit()

            self._publish_log(
                message=f"Step {step.step_number}/{total_steps}: {step.step_name}",
                level="step_start",
                step_number=step.step_number,
                step_status="running",
            )

            start_time = time.time()

            try:
                step_output = self._execute_single_step(
                    step,
                    step.step_number - 1,
                    total_steps,
                    accumulated_context,
                )
            except Exception as exc:  # pylint: disable=broad-except
                duration_ms = int((time.time() - start_time) * 1000)
                self._update_step_status(
                    step,
                    "failed",
                    error=str(exc),
                    duration_ms=duration_ms,
                )
                self._publish_log(
                    message=f"Step failed: {step.step_name}",
                    level="step_failed",
                    step_number=step.step_number,
                    step_status="failed",
                )
                raise

            duration_ms = int((time.time() - start_time) * 1000)
            self._update_step_status(step, "done", output=step_output, duration_ms=duration_ms)

            accumulated_context += (
                f"\n\n[Step {step.step_number} Result: {step.step_name}]\n{step_output}"
            )
            final_output = step_output

            self._publish_log(
                message=f"Completed: {step.step_name}",
                level="step_complete",
                step_number=step.step_number,
                step_status="done",
                output_chunk=step_output[:300],
            )

        return final_output or accumulated_context.strip()

    def _execute_single_step(
        self,
        step: TaskStep,
        step_index: int,
        total_steps: int,
        accumulated_context: str,
    ) -> str:
        """Execute one step and return its output."""

        prompt = self._build_execution_prompt(
            step_name=step.step_name,
            step_index=step_index,
            total_steps=total_steps,
            accumulated_context=accumulated_context,
        )

        strategy = str(self.task_config.get("execution_strategy", "writing"))
        requires_web_search = bool(self.task_config.get("requires_web_search", False))

        if strategy == "research_grounded" or requires_web_search:
            if step_index == 0:
                step_output = self.llm.generate_with_search(prompt)
            else:
                step_output = self.llm.generate(prompt)
        elif strategy == "code":
            code_prompt = (
                "Focus on executable, maintainable code with clear structure and edge cases.\n\n"
                f"{prompt}"
            )
            step_output = self.llm.generate(code_prompt)
        elif strategy == "data_analysis":
            analysis_prompt = (
                "Focus on data interpretation, key insights, and concise business reporting.\n\n"
                f"{prompt}"
            )
            step_output = self.llm.generate(analysis_prompt)
        else:
            step_output = self.llm.generate(prompt)

        self._publish_log(
            message=f"Generated intermediate output for step {step.step_number}",
            level="info",
            step_number=step.step_number,
            output_chunk=step_output[:300],
        )

        return step_output

    def _build_execution_prompt(
        self,
        step_name: str,
        step_index: int,
        total_steps: int,
        accumulated_context: str,
    ) -> str:
        """Build the full prompt for Gemini for a specific step."""

        input_data = self.task.input_json or {}
        system_prompt = str(self.task_config.get("system_prompt") or "")

        input_section = "\n".join(
            [
                f"[{str(key).replace('_', ' ').title()}]: {value}"
                for key, value in input_data.items()
                if value
            ]
        )

        context_section = ""
        if accumulated_context:
            context_section = f"\n\n[Previous Steps Context]:\n{accumulated_context[:3000]}"

        step_instruction = f"\n\n[Current Step {step_index + 1} of {total_steps}: {step_name}]"
        if step_index == total_steps - 1:
            step_instruction += "\nThis is the FINAL step. Produce the complete, polished final output."
        elif step_index == 0:
            step_instruction += "\nThis is the FIRST step. Begin executing this task with the provided inputs."
        else:
            step_instruction += "\nContinue the task execution building on the previous steps."

        full_prompt = (
            f"{system_prompt}\n\n[Task Inputs]:\n{input_section}{context_section}{step_instruction}"
        )
        return full_prompt

    def _store_output(self, output_text: str) -> TaskOutput:
        """Store task output in DB or external file depending on size."""

        output_format = str(self.task_config.get("output_format", "text")).lower()
        output_type = "text"

        if output_format == "code":
            output_type = "code"
        elif output_format == "json":
            try:
                json.loads(output_text)
                output_type = "json"
            except json.JSONDecodeError:
                output_type = "text"
        elif output_format == "table":
            output_type = "table"
        elif output_format == "html":
            output_type = "html"

        content_text: str | None = None
        file_path: str | None = None
        file_name: str | None = None
        encoded_output = output_text.encode("utf-8")
        file_size = len(encoded_output)

        if len(output_text) <= 500_000:
            content_text = output_text
        else:
            file_path = file_service.save_output_file(str(self.task.id), output_text)
            file_name = os.path.basename(file_path)

        task_output = TaskOutput(
            task_id=self.task.id,
            org_id=self.task.org_id,
            output_type=output_type,
            content_text=content_text,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            file_mime="text/plain",
        )
        db.session.add(task_output)
        db.session.commit()
        return task_output

    def _publish_log(
        self,
        message: str,
        level: str = "info",
        step_number: int | None = None,
        step_status: str | None = None,
        output_chunk: str | None = None,
    ) -> None:
        """Publish structured execution log event to Redis pub sub channel."""

        event_data = {
            "type": "log",
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
            "step_number": step_number,
            "step_status": step_status,
            "output_chunk": output_chunk,
        }

        try:
            # Redis pub sub payload schema used by SSE monitor clients.
            self.redis_client.publish(self.log_channel, json.dumps(event_data))
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.warning("Failed to publish log to Redis: %s", exc)

    def _update_task_status(self, status: str, error_message: str | None = None) -> None:
        """Update task status with optional error message and commit immediately."""

        self.task.status = status
        if error_message:
            self.task.error_message = error_message[:2000]
        if status == "done":
            self.task.completed_at = datetime.utcnow()

        try:
            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed to update task status: %s", exc)

    def _update_step_status(
        self,
        step: TaskStep,
        status: str,
        output: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Update individual step status in DB with immediate commit."""

        step.status = status

        if status == "running" and step.started_at is None:
            step.started_at = datetime.utcnow()
        if status in {"done", "failed"}:
            step.completed_at = datetime.utcnow()

        if output is not None:
            step.output_json = {"text": output}
        if error is not None:
            step.error_msg = error[:2000]
        if duration_ms is not None:
            step.duration_ms = duration_ms

        try:
            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed to update step status: %s", exc)

    def _record_run_log(
        self,
        message: str,
        log_level: str = "info",
        step_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write task execution log entry when run log model is available."""

        try:
            from app.models import RunLog  # type: ignore[attr-defined]

            run_log = RunLog(
                task_id=self.task.id,
                step_id=step_id,
                log_level=log_level,
                message=message,
                metadata_json=metadata or {},
            )
            db.session.add(run_log)
            db.session.commit()
        except Exception:  # pylint: disable=broad-except
            current_app.logger.log(
                {
                    "debug": 10,
                    "info": 20,
                    "warning": 30,
                    "error": 40,
                }.get(log_level, 20),
                "Task log[%s] task=%s step=%s: %s",
                log_level,
                self.task_id,
                step_id,
                message,
            )

    def _handle_failure(self, error: Exception) -> None:
        """Handle task failure and publish terminal events and notifications."""

        error_message = str(error)[:2000]

        if self.task is None:
            current_app.logger.error("Task execution failed before loading task: %s", error_message)
            return

        TaskStep.query.filter_by(task_id=self.task.id, status="running").update(
            {
                "status": "failed",
                "error_msg": error_message,
                "completed_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()

        self.task.completed_at = datetime.utcnow()
        self._update_task_status("failed", error_message=error_message)
        self._record_run_log(f"Task failed: {error_message}", log_level="error")
        self._publish_log(f"Task failed: {error_message}", level="failed")

        from app.services.notification_service import NotificationService

        notification_service = NotificationService()
        notification_service.notify_task_failed(self.task, error_message)

        try:
            audit_log = AuditLog(
                org_id=self.task.org_id,
                user_id=self.task.user_id,
                action="task.failed",
                resource_type="task",
                resource_id=str(self.task.id),
                extra_json={
                    "task_type": self.task.task_type,
                    "error": error_message,
                },
            )
            db.session.add(audit_log)
            db.session.commit()
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            current_app.logger.error("Failed writing task failure audit log: %s", exc)

    def _check_quota_warning(self) -> None:
        """Send quota warning notification once per month at 80 percent usage."""

        if self.task is None:
            return

        org = Organization.query.get(self.task.org_id)
        if org is None:
            return

        plan = Plan.query.get(org.plan_id) if org.plan_id else None
        quota_limit = int(plan.task_quota_monthly) if plan else 0
        if quota_limit <= 0:
            return

        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        quota_used = (
            UsageRecord.query.filter(
                UsageRecord.org_id == self.task.org_id,
                UsageRecord.usage_type == "task_run",
                UsageRecord.recorded_at >= month_start,
            ).count()
        )

        usage_percent = (quota_used / quota_limit) * 100
        if usage_percent < 80 or usage_percent >= 100:
            return

        month_year = month_start.strftime("%Y%m")
        redis_key = f"quota_warned:{self.task.org_id}:{month_year}"

        try:
            created = self.redis_client.set(redis_key, "1", nx=True, ex=35 * 24 * 3600)
            if not created:
                return
        except Exception:  # pylint: disable=broad-except
            return

        from app.services.notification_service import NotificationService

        NotificationService().notify_quota_warning(
            user_id=self.task.user_id,
            org_id=self.task.org_id,
            used=quota_used,
            quota=quota_limit,
        )


def execute_task(task_id: str) -> bool:
    """Convenience function called by Celery worker to execute one task."""

    engine = AgentExecutionEngine(task_id)
    return engine.execute()
