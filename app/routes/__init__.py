"""Blueprint exports for application routing."""

from app.routes.admin import admin_bp
from app.routes.api import api_bp
from app.routes.audit import audit_bp
from app.routes.auth import auth_bp
from app.routes.billing import billing_bp
from app.routes.dashboard import dashboard_bp
from app.routes.integrations import integrations_bp
from app.routes.knowledge import knowledge_bp
from app.routes.notifications import notifications_bp
from app.routes.outputs import outputs_bp
from app.routes.projects import projects_bp
from app.routes.public import public_bp
from app.routes.reports import reports_bp
from app.routes.schedules import schedules_bp
from app.routes.settings import settings_bp
from app.routes.support import support_bp
from app.routes.tasks import tasks_bp
from app.routes.team import team_bp
from app.routes.templates_bp import templates_bp
from app.routes.usage import usage_bp
from app.routes.vault import vault_bp
from app.routes.workflows import workflows_bp

__all__ = [
    "admin_bp",
    "api_bp",
    "audit_bp",
    "auth_bp",
    "billing_bp",
    "dashboard_bp",
    "integrations_bp",
    "knowledge_bp",
    "notifications_bp",
    "outputs_bp",
    "projects_bp",
    "public_bp",
    "reports_bp",
    "schedules_bp",
    "settings_bp",
    "support_bp",
    "tasks_bp",
    "team_bp",
    "templates_bp",
    "usage_bp",
    "vault_bp",
    "workflows_bp",
]
