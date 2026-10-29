"""Global tracking history page."""

from flask import Blueprint, render_template, request

import db
from auth import current_user, login_required
from routes.common import document_scope

tracking_bp = Blueprint("tracking", __name__)


@tracking_bp.route("/tracking")
@login_required
def index():
    user = current_user()
    scope_sql, scope_params = document_scope(user)

    q = (request.args.get("q") or "").strip()
    action = (request.args.get("action") or "").strip()
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    where = [scope_sql]
    params = list(scope_params)

    if q:
        where.append("(d.document_number LIKE %s OR d.title LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if action:
        where.append("t.action LIKE %s")
        params.append(f"%{action}%")
    if date_from:
        where.append("DATE(t.action_timestamp) >= %s")
        params.append(date_from)
    if date_to:
        where.append("DATE(t.action_timestamp) <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where)

    events = db.fetch_all(
        f"""
        SELECT t.tracking_id, t.action, t.remarks, t.action_timestamp,
               d.document_id, d.document_number, d.title,
               actor.full_name AS actor_name,
               fd.department_name AS from_department,
               td.department_name AS to_department,
               fu.full_name AS from_user_name,
               tu.full_name AS to_user_name
          FROM document_tracking t
          JOIN documents d ON d.document_id = t.document_id
          LEFT JOIN users actor ON actor.user_id = t.actor_id
          LEFT JOIN departments fd ON fd.department_id = t.from_department_id
          LEFT JOIN departments td ON td.department_id = t.to_department_id
          LEFT JOIN users fu ON fu.user_id = t.from_user_id
          LEFT JOIN users tu ON tu.user_id = t.to_user_id
         WHERE {where_sql}
         ORDER BY t.action_timestamp DESC, t.tracking_id DESC
         LIMIT 200
        """,
        tuple(params),
    )

    actions = db.fetch_all(
        "SELECT DISTINCT action FROM document_tracking ORDER BY action"
    )

    return render_template(
        "tracking/index.html",
        events=events,
        actions=actions,
        q=q,
        action=action,
        date_from=date_from,
        date_to=date_to,
        active_page="tracking",
    )
