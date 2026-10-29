"""Shared helpers used by more than one blueprint."""

from flask import abort, g, url_for
from flask import current_app

import db
from auth import current_user
from workflow import can_manage_records, can_view_document


def get_document_or_404(document_id, for_update=False):
    """Fetch a document and make sure the current user is allowed to see it."""
    sql = """
        SELECT d.*, dt.type_name,
               dept.department_name,
               u.full_name AS assignee_name,
               s.full_name AS submitter_name
          FROM documents d
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
          LEFT JOIN users u ON u.user_id = d.assigned_to
          JOIN users s ON s.user_id = d.submitted_by
         WHERE d.document_id = %s
    """
    if for_update:
        sql += " FOR UPDATE"
    doc = db.fetch_one(sql, (document_id,))
    if doc is None:
        abort(404)

    user = current_user()
    has_approval = has_approval_for(user, document_id)
    if not can_view_document(user, doc, has_approval):
        abort(403)
    doc["has_approval"] = has_approval
    return doc


def has_approval_for(user, document_id):
    if not user:
        return False
    row = db.fetch_one(
        "SELECT 1 AS x FROM approvals WHERE document_id = %s AND approver_id = %s LIMIT 1",
        (document_id, user["user_id"]),
    )
    return row is not None


def pending_approval_for(document_id):
    return db.fetch_one(
        """
        SELECT a.*, u.full_name AS approver_name
          FROM approvals a
          JOIN users u ON u.user_id = a.approver_id
         WHERE a.document_id = %s AND a.decision = 'pending'
         ORDER BY a.approval_id DESC
         LIMIT 1
        """,
        (document_id,),
    )


def sidebar_counts():
    """Small badge counters for the sidebar (cached per request)."""
    if "sidebar_counts" in g:
        return g.sidebar_counts

    user = current_user()
    counts = {"documents": 0, "assignments": 0, "approvals": 0}
    if user:
        scope_sql, scope_params = document_scope(user)
        row = db.fetch_one(
            f"SELECT COUNT(*) AS total FROM documents d WHERE d.archived = FALSE AND {scope_sql}",
            scope_params,
        )
        counts["documents"] = row["total"] if row else 0
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS total FROM documents d
             WHERE d.assigned_to = %s AND d.archived = FALSE
               AND d.status IN ('Submitted', 'In Progress', 'Forwarded', 'Rejected')
            """,
            (user["user_id"],),
        )
        counts["assignments"] = row["total"] if row else 0
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS total
              FROM approvals a
              JOIN documents d ON d.document_id = a.document_id
             WHERE a.decision = 'pending' AND d.archived = FALSE
               AND (%s = 1 OR a.approver_id = %s)
            """,
            (1 if user["role"] == "admin" else 0, user["user_id"]),
        )
        counts["approvals"] = row["total"] if row else 0

    g.sidebar_counts = counts
    return counts


def document_scope(user, alias="d"):
    """SQL fragment (plus parameters) limiting documents to what ``user`` may see."""
    if can_manage_records(user):
        return "1 = 1", ()

    uid = user["user_id"]
    if user["role"] == "manager":
        return (
            f"({alias}.current_department_id = %s OR {alias}.submitted_by = %s OR "
            f"EXISTS (SELECT 1 FROM approvals a WHERE a.document_id = {alias}.document_id"
            f" AND a.approver_id = %s))",
            (user["department_id"], uid, uid),
        )
    return (
        f"({alias}.assigned_to = %s OR {alias}.submitted_by = %s OR {alias}.current_department_id = %s)",
        (uid, uid, user["department_id"]),
    )


def register_template_helpers(app):
    from datetime import date, datetime

    import workflow

    @app.template_filter("nice_date")
    def nice_date(value):
        if not value:
            return "—"
        if isinstance(value, datetime):
            return value.strftime("%d %b %Y, %H:%M")
        if isinstance(value, date):
            return value.strftime("%d %b %Y")
        return value

    @app.context_processor
    def inject_globals():
        return {
            "sidebar": sidebar_counts(),
            "status_badges": workflow.STATUS_BADGES,
            "priority_badges": workflow.PRIORITY_BADGES,
            "statuses": workflow.STATUSES,
            "priorities": workflow.PRIORITIES,
            "today": date.today().isoformat(),
            "app_name": "DocTrack",
        }

    @app.template_global()
    def page_link(page):
        """Keep current filters while changing the page number."""
        from flask import request

        args = request.args.to_dict()
        args["page"] = page
        return url_for(request.endpoint, **request.view_args, **args)
