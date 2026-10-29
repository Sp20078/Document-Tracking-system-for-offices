"""Reports and CSV export (administrators and records officers)."""

import csv
import io
from datetime import date

from flask import Blueprint, Response, render_template, request

import db
from auth import login_required, roles_required
from routes.common import document_scope

reports_bp = Blueprint("reports", __name__)


def _report_data():
    """All report numbers come straight from MySQL."""
    status_counts = db.fetch_all(
        """
        SELECT status, COUNT(*) AS total
          FROM documents
         WHERE archived = FALSE
         GROUP BY status
         ORDER BY FIELD(status, 'Submitted', 'Pending', 'In Progress', 'Forwarded',
                        'Approved', 'Rejected', 'Closed')
        """
    )

    department_summary = db.fetch_all(
        "SELECT * FROM v_department_status_summary ORDER BY department_name"
    )

    pending_documents = db.fetch_all(
        "SELECT * FROM v_pending_documents ORDER BY pending_days DESC, submission_date LIMIT 8"
    )

    overdue_documents = db.fetch_all(
        """
        SELECT d.document_id, d.document_number, d.title, d.status, d.priority,
               d.due_date, dept.department_name,
               DATEDIFF(CURDATE(), d.due_date) AS days_overdue
          FROM documents d
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
         WHERE d.archived = FALSE
           AND d.due_date IS NOT NULL
           AND d.due_date < CURDATE()
           AND d.status IN ('Submitted', 'Pending', 'In Progress', 'Forwarded')
         ORDER BY d.due_date ASC
         LIMIT 10
        """
    )

    recently_completed = db.fetch_all(
        """
        SELECT d.document_id, d.document_number, d.title, d.status,
               d.updated_at, dept.department_name
          FROM documents d
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
         WHERE d.archived = FALSE AND d.status IN ('Approved', 'Closed')
         ORDER BY d.updated_at DESC
         LIMIT 8
        """
    )

    totals = db.fetch_one(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(archived = TRUE), 0) AS archived
          FROM documents
        """
    )

    return {
        "status_counts": status_counts,
        "department_summary": department_summary,
        "pending_documents": pending_documents,
        "overdue_documents": overdue_documents,
        "recently_completed": recently_completed,
        "totals": totals,
    }


@reports_bp.route("/reports")
@login_required
@roles_required("admin", "records_officer")
def index():
    data = _report_data()
    max_department_total = max(
        [int(row["total_documents"]) for row in data["department_summary"]] or [1]
    ) or 1
    return render_template(
        "reports/index.html",
        max_department_total=max_department_total,
        active_page="reports",
        **data,
    )


@reports_bp.route("/reports/export.csv")
@login_required
@roles_required("admin", "records_officer")
def export_csv():
    """CSV of the documents in the system (respects the same access rules)."""
    from auth import current_user

    scope_sql, scope_params = document_scope(current_user())
    rows = db.fetch_all(
        f"""
        SELECT d.document_number, d.title, dt.type_name, d.status, d.priority,
               dept.department_name, u.full_name AS assignee,
               submitter.full_name AS submitted_by,
               d.submission_date, d.due_date, d.created_at, d.updated_at
          FROM documents d
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          JOIN users submitter ON submitter.user_id = d.submitted_by
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
          LEFT JOIN users u ON u.user_id = d.assigned_to
         WHERE {scope_sql}
         ORDER BY d.document_id
        """,
        scope_params,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Document Number", "Title", "Type", "Status", "Priority", "Department",
        "Assignee", "Submitted By", "Submission Date", "Due Date", "Created", "Updated",
    ])
    for row in rows:
        writer.writerow([
            row["document_number"], row["title"], row["type_name"], row["status"],
            row["priority"], row["department_name"] or "", row["assignee"] or "",
            row["submitted_by"], row["submission_date"], row["due_date"] or "",
            row["created_at"], row["updated_at"],
        ])

    filename = f"documents-report-{date.today().isoformat()}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
