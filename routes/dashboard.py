"""Dashboard: live counts and recent activity."""

from flask import Blueprint, render_template

import db
from auth import current_user, login_required
from routes.common import document_scope

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    user = current_user()
    scope_sql, scope_params = document_scope(user)

    counts = db.fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(d.status = 'Submitted'), 0)   AS submitted,
               COALESCE(SUM(d.status = 'Pending'), 0)     AS pending,
               COALESCE(SUM(d.status IN ('In Progress', 'Forwarded')), 0) AS in_progress,
               COALESCE(SUM(d.status = 'Approved'), 0)    AS approved,
               COALESCE(SUM(d.status = 'Rejected'), 0)    AS rejected,
               COALESCE(SUM(d.status = 'Closed'), 0)      AS closed,
               COALESCE(SUM(d.due_date IS NOT NULL AND d.due_date < CURDATE()
                            AND d.status IN ('Submitted', 'Pending', 'In Progress', 'Forwarded')), 0)
                                                         AS overdue
          FROM documents d
         WHERE d.archived = FALSE AND {scope_sql}
        """,
        scope_params,
    )
    counts = {key: int(value or 0) for key, value in counts.items()}

    recent_documents = db.fetch_all(
        f"""
        SELECT d.document_id, d.document_number, d.title, d.status, d.priority,
               d.submission_date, d.due_date, dt.type_name,
               dept.department_name, u.full_name AS assignee_name
          FROM documents d
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
          LEFT JOIN users u ON u.user_id = d.assigned_to
         WHERE d.archived = FALSE AND {scope_sql}
         ORDER BY d.updated_at DESC
         LIMIT 6
        """,
        scope_params,
    )

    recent_activity = db.fetch_all(
        f"""
        SELECT t.tracking_id, t.action, t.remarks, t.action_timestamp,
               d.document_id, d.document_number, d.title,
               actor.full_name AS actor_name,
               fd.department_name AS from_department,
               td.department_name AS to_department
          FROM document_tracking t
          JOIN documents d ON d.document_id = t.document_id
          LEFT JOIN users actor ON actor.user_id = t.actor_id
          LEFT JOIN departments fd ON fd.department_id = t.from_department_id
          LEFT JOIN departments td ON td.department_id = t.to_department_id
         WHERE {scope_sql}
         ORDER BY t.action_timestamp DESC, t.tracking_id DESC
         LIMIT 8
        """,
        scope_params,
    )

    # Department load: quick bar chart built from real data (pure CSS bars).
    department_load = db.fetch_all(
        f"""
        SELECT dept.department_name, COUNT(d.document_id) AS total
          FROM departments dept
          LEFT JOIN documents d
                 ON d.current_department_id = dept.department_id
                AND d.archived = FALSE
                AND {scope_sql}
         GROUP BY dept.department_id, dept.department_name
         ORDER BY total DESC, dept.department_name
         LIMIT 6
        """,
        scope_params,
    )
    max_load = max([row["total"] for row in department_load] or [1]) or 1

    pending_approvals = []
    if user["role"] in {"admin", "manager"}:
        pending_approvals = db.fetch_all(
            """
            SELECT a.approval_id, a.document_id, d.document_number, d.title,
                   d.status, d.submission_date, dept.department_name
              FROM approvals a
              JOIN documents d ON d.document_id = a.document_id
              LEFT JOIN departments dept ON dept.department_id = d.current_department_id
             WHERE a.decision = 'pending' AND d.status = 'Pending' AND d.archived = FALSE
               AND (a.approver_id = %s OR %s = 1)
             ORDER BY d.submission_date ASC
             LIMIT 4
            """,
            (user["user_id"], 1 if user["role"] == "admin" else 0),
        )

    return render_template(
        "dashboard.html",
        counts=counts,
        recent_documents=recent_documents,
        recent_activity=recent_activity,
        department_load=department_load,
        max_load=max_load,
        pending_approvals=pending_approvals,
    )
