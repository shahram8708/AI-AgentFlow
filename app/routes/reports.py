"""Reports and analytics blueprint routes."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, timezone

from flask import Blueprint, abort, g, render_template, request, send_file
from flask_login import current_user
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import case, desc, func

from app.extensions import cache, db
from app.models import AutomationTask, TaskOutput
from app.services.agent_runner import CATEGORY_META, TASK_REGISTRY
from app.utils.decorators import login_required, org_required

reports_bp = Blueprint("reports", __name__)


def _day_bucket(column):
    bind = db.session.get_bind() or db.engine
    if bind.dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d", column)
    return func.date_trunc("day", column)


def _status_label(status: str) -> str:
    mapping = {
        "done": "Completed",
        "failed": "Failed",
        "running": "Running",
        "pending": "Pending",
        "cancelled": "Cancelled",
    }
    return mapping.get(status, status.replace("_", " ").title())


def _safe_percentage_change(current_value: int, previous_value: int) -> float:
    if previous_value <= 0:
        return 100.0 if current_value > 0 else 0.0
    return round(((current_value - previous_value) / previous_value) * 100, 1)


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid {label}. Expected format YYYY-MM-DD.") from exc


def _resolve_date_range() -> tuple[date, date, str]:
    today = datetime.now(timezone.utc).date()
    period = (request.args.get("period") or "30d").strip().lower()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()

    if period in {"7d", "30d", "90d"}:
        days = int(period.replace("d", ""))
        date_to = today
        date_from = today - timedelta(days=days - 1)
    else:
        period = "custom"
        date_to = _parse_date(date_to_raw, "date_to") if date_to_raw else today
        date_from = (
            _parse_date(date_from_raw, "date_from")
            if date_from_raw
            else (date_to - timedelta(days=29))
        )

    if date_from > date_to:
        raise ValueError("date_from must be less than or equal to date_to.")

    if (date_to - date_from).days > 365:
        raise ValueError("Date range cannot exceed 365 days.")

    return date_from, date_to, period


def _all_days(date_from: date, date_to: date) -> list[date]:
    total_days = (date_to - date_from).days + 1
    return [date_from + timedelta(days=index) for index in range(total_days)]


def _build_report_data(org_id, date_from: date, date_to: date) -> dict:
    start_dt = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
    all_days = _all_days(date_from, date_to)

    base_filters = [
        AutomationTask.org_id == org_id,
        AutomationTask.created_at >= start_dt,
        AutomationTask.created_at <= end_dt,
    ]

    total_tasks = db.session.query(func.count(AutomationTask.id)).filter(*base_filters).scalar() or 0
    completed_tasks = (
        db.session.query(func.count(AutomationTask.id))
        .filter(*base_filters, AutomationTask.status == "done")
        .scalar()
        or 0
    )
    failed_tasks = (
        db.session.query(func.count(AutomationTask.id))
        .filter(*base_filters, AutomationTask.status == "failed")
        .scalar()
        or 0
    )

    total_finished = completed_tasks + failed_tasks
    success_rate = round((completed_tasks / total_finished) * 100, 1) if total_finished else 100.0

    avg_duration_seconds = (
        db.session.query(
            func.avg(
                func.extract(
                    "epoch",
                    AutomationTask.completed_at - AutomationTask.started_at,
                )
            )
        )
        .filter(
            *base_filters,
            AutomationTask.status == "done",
            AutomationTask.started_at.isnot(None),
            AutomationTask.completed_at.isnot(None),
        )
        .scalar()
        or 0
    )

    total_outputs_generated = (
        db.session.query(func.count(TaskOutput.id))
        .filter(
            TaskOutput.org_id == org_id,
            TaskOutput.is_deleted.is_(False),
            TaskOutput.created_at >= start_dt,
            TaskOutput.created_at <= end_dt,
        )
        .scalar()
        or 0
    )

    period_days = (date_to - date_from).days + 1
    prev_start_dt = start_dt - timedelta(days=period_days)
    prev_end_dt = start_dt - timedelta(seconds=1)

    prev_filters = [
        AutomationTask.org_id == org_id,
        AutomationTask.created_at >= prev_start_dt,
        AutomationTask.created_at <= prev_end_dt,
    ]

    prev_total_tasks = db.session.query(func.count(AutomationTask.id)).filter(*prev_filters).scalar() or 0
    prev_completed_tasks = (
        db.session.query(func.count(AutomationTask.id))
        .filter(*prev_filters, AutomationTask.status == "done")
        .scalar()
        or 0
    )
    prev_failed_tasks = (
        db.session.query(func.count(AutomationTask.id))
        .filter(*prev_filters, AutomationTask.status == "failed")
        .scalar()
        or 0
    )

    tasks_vs_previous_period = _safe_percentage_change(total_tasks, prev_total_tasks)
    completed_vs_previous_period = _safe_percentage_change(completed_tasks, prev_completed_tasks)
    failed_vs_previous_period = _safe_percentage_change(failed_tasks, prev_failed_tasks)

    day_bucket = _day_bucket(AutomationTask.created_at)
    status_rows = (
        db.session.query(
            day_bucket.label("day"),
            AutomationTask.status,
            func.count(AutomationTask.id).label("count"),
        )
        .filter(*base_filters)
        .group_by(day_bucket, AutomationTask.status)
        .all()
    )

    status_breakdown = {
        day.strftime("%Y-%m-%d"): {"done": 0, "failed": 0, "cancelled": 0, "running": 0}
        for day in all_days
    }
    for row in status_rows:
        day_value = row.day
        if day_value is None:
            continue
        if hasattr(day_value, "strftime"):
            day_key = day_value.strftime("%Y-%m-%d")
        else:
            day_key = str(day_value)[:10]
        if day_key not in status_breakdown:
            continue
        status_breakdown[day_key][row.status] = int(row.count or 0)

    line_chart_data = []
    daily_success_rate = []
    for day in all_days:
        day_key = day.strftime("%Y-%m-%d")
        done_count = int(status_breakdown[day_key].get("done", 0))
        failed_count = int(status_breakdown[day_key].get("failed", 0))
        cancelled_count = int(status_breakdown[day_key].get("cancelled", 0))
        running_count = int(status_breakdown[day_key].get("running", 0))

        line_chart_data.append(
            {
                "date": day_key,
                "completed": done_count,
                "failed": failed_count,
                "count": done_count + failed_count + cancelled_count + running_count,
            }
        )

        day_finished = done_count + failed_count
        rate_value = round((done_count / day_finished) * 100, 1) if day_finished else 100.0
        daily_success_rate.append({"date": day_key, "rate": rate_value})

    task_type_rows = (
        db.session.query(
            AutomationTask.task_type,
            func.count(AutomationTask.id).label("count"),
        )
        .filter(*base_filters)
        .group_by(AutomationTask.task_type)
        .all()
    )

    category_counts: dict[str, dict] = {}
    for row in task_type_rows:
        task_type = row.task_type
        count = int(row.count or 0)
        task_meta = TASK_REGISTRY.get(task_type, {})
        category_slug = str(task_meta.get("category") or "other")
        category_info = CATEGORY_META.get(category_slug, {})
        category_label = category_info.get("display") or category_slug.replace("_", " ").title()

        if category_slug not in category_counts:
            category_counts[category_slug] = {
                "category": category_slug,
                "label": category_label,
                "count": 0,
            }
        category_counts[category_slug]["count"] += count

    bar_chart_data = sorted(
        category_counts.values(),
        key=lambda item: item["count"],
        reverse=True,
    )[:10]

    pie_rows = (
        db.session.query(AutomationTask.status, func.count(AutomationTask.id).label("count"))
        .filter(*base_filters)
        .group_by(AutomationTask.status)
        .all()
    )
    pie_chart_data = [
        {
            "status": row.status,
            "count": int(row.count or 0),
            "label": _status_label(row.status),
        }
        for row in pie_rows
    ]

    top_task_rows = (
        db.session.query(
            AutomationTask.task_type,
            func.count(AutomationTask.id).label("count"),
            func.avg(
                func.extract(
                    "epoch",
                    AutomationTask.completed_at - AutomationTask.started_at,
                )
            ).label("avg_duration"),
            func.sum(case((AutomationTask.status == "done", 1), else_=0)).label("done_count"),
            func.sum(case((AutomationTask.status == "failed", 1), else_=0)).label("failed_count"),
        )
        .filter(*base_filters)
        .group_by(AutomationTask.task_type)
        .order_by(desc("count"))
        .limit(10)
        .all()
    )

    top_task_types = []
    for row in top_task_rows:
        task_meta = TASK_REGISTRY.get(row.task_type, {})
        done_count = int(row.done_count or 0)
        failed_count = int(row.failed_count or 0)
        denominator = done_count + failed_count
        task_success_rate = round((done_count / denominator) * 100, 1) if denominator else 100.0
        top_task_types.append(
            {
                "task_type": row.task_type,
                "task_name": task_meta.get("name") or row.task_type.replace("_", " ").title(),
                "category": task_meta.get("category") or "other",
                "category_label": task_meta.get("category_display")
                or str(task_meta.get("category") or "other").replace("_", " ").title(),
                "count": int(row.count or 0),
                "avg_duration": float(row.avg_duration or 0),
                "success_rate": task_success_rate,
            }
        )

    return {
        "total_tasks": int(total_tasks),
        "completed_tasks": int(completed_tasks),
        "failed_tasks": int(failed_tasks),
        "success_rate": float(success_rate),
        "avg_duration_seconds": float(avg_duration_seconds or 0),
        "total_outputs_generated": int(total_outputs_generated),
        "tasks_vs_previous_period": float(tasks_vs_previous_period),
        "completed_vs_previous_period": float(completed_vs_previous_period),
        "failed_vs_previous_period": float(failed_vs_previous_period),
        "line_chart_data": line_chart_data,
        "bar_chart_data": bar_chart_data,
        "pie_chart_data": pie_chart_data,
        "top_task_types": top_task_types,
        "status_breakdown": status_breakdown,
        "daily_success_rate": daily_success_rate,
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "period_days": period_days,
    }


def _export_csv(report_data: dict, org_slug: str) -> io.BytesIO:
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["AgentFlow Report"])
    writer.writerow(["Organization", org_slug])
    writer.writerow(["Date From", report_data["date_from"]])
    writer.writerow(["Date To", report_data["date_to"]])
    writer.writerow([])

    writer.writerow(["Metric", "Value"])
    writer.writerow(["Total Tasks", report_data["total_tasks"]])
    writer.writerow(["Completed Tasks", report_data["completed_tasks"]])
    writer.writerow(["Failed Tasks", report_data["failed_tasks"]])
    writer.writerow(["Success Rate", f"{report_data['success_rate']}%"])
    writer.writerow(["Avg Duration (seconds)", round(report_data["avg_duration_seconds"], 2)])
    writer.writerow(["Total Outputs Generated", report_data["total_outputs_generated"]])
    writer.writerow(["Tasks vs Previous Period", f"{report_data['tasks_vs_previous_period']}%"])
    writer.writerow([])

    writer.writerow(["Top Task Types"])
    writer.writerow(["Task Type", "Task Name", "Category", "Count", "Avg Duration", "Success Rate"])
    for item in report_data["top_task_types"]:
        writer.writerow(
            [
                item["task_type"],
                item["task_name"],
                item["category_label"],
                item["count"],
                round(item["avg_duration"], 2),
                f"{item['success_rate']}%",
            ]
        )

    buffer = io.BytesIO()
    buffer.write(output.getvalue().encode("utf-8-sig"))
    buffer.seek(0)
    return buffer


def _export_pdf(report_data: dict, org_name: str) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36)
    styles = getSampleStyleSheet()

    elements = []
    elements.append(Paragraph("AgentFlow Analytics Report", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Organization: {org_name}", styles["Heading3"]))
    elements.append(
        Paragraph(
            f"Date range: {report_data['date_from']} to {report_data['date_to']}",
            styles["Normal"],
        )
    )
    elements.append(Paragraph("Generated by AgentFlow", styles["Italic"]))
    elements.append(PageBreak())

    elements.append(Paragraph("Metrics Summary", styles["Heading2"]))
    elements.append(Spacer(1, 8))

    metrics_table_data = [
        ["Metric", "Value"],
        ["Total Tasks", str(report_data["total_tasks"])],
        ["Completed Tasks", str(report_data["completed_tasks"])],
        ["Failed Tasks", str(report_data["failed_tasks"])],
        ["Success Rate", f"{report_data['success_rate']}%"],
        ["Average Duration", f"{round(report_data['avg_duration_seconds'], 2)} sec"],
        ["Total Outputs", str(report_data["total_outputs_generated"])],
        ["Tasks vs Previous", f"{report_data['tasks_vs_previous_period']}%"],
    ]

    metrics_table = Table(metrics_table_data, colWidths=[220, 220])
    metrics_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A56DB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ]
        )
    )
    elements.append(metrics_table)
    elements.append(Spacer(1, 16))

    elements.append(Paragraph("Top 10 Task Types", styles["Heading2"]))
    elements.append(Spacer(1, 8))

    top_table_rows = [["Task", "Category", "Count", "Avg Duration", "Success Rate"]]
    for item in report_data["top_task_types"]:
        top_table_rows.append(
            [
                item["task_name"],
                item["category_label"],
                str(item["count"]),
                f"{round(item['avg_duration'], 1)}s",
                f"{item['success_rate']}%",
            ]
        )

    top_tasks_table = Table(top_table_rows, colWidths=[135, 95, 55, 85, 80])
    top_tasks_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ]
        )
    )
    elements.append(top_tasks_table)

    def _draw_footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawRightString(A4[0] - 36, 18, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    buffer.seek(0)
    return buffer


@reports_bp.get("/reports")
@login_required
@org_required
def reports_home():
    """Render reports dashboard and export report documents."""

    try:
        date_from, date_to, period = _resolve_date_range()
    except ValueError as exc:
        abort(400, description=str(exc))

    cache_key = f"report_{g.org.id}_{date_from}_{date_to}"
    report_data = cache.get(cache_key)
    if report_data is None:
        report_data = _build_report_data(g.org.id, date_from, date_to)
        cache.set(cache_key, report_data, timeout=600)

    export_kind = (request.args.get("export") or "").strip().lower()
    if export_kind == "csv":
        buffer = _export_csv(report_data, g.org.slug)
        filename = f"agentflow_report_{g.org.slug}_{report_data['date_from']}_{report_data['date_to']}.csv"
        return send_file(buffer, mimetype="text/csv", as_attachment=True, download_name=filename)

    if export_kind == "pdf":
        buffer = _export_pdf(report_data, g.org.name)
        filename = f"agentflow_report_{g.org.slug}_{report_data['date_from']}_{report_data['date_to']}.pdf"
        return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)

    return render_template(
        "app/reports.html",
        period=period,
        **report_data,
    )
