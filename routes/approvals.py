"""Pending approvals: managers and administrators decide documents."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db
import workflow
from auth import current_user, login_required, roles_required
from routes.documents import DOCUMENT_SELECT, WorkflowError

approvals_bp = Blueprint("approvals", __name__)


@approvals_bp.route("/approvals")
@login_required
@roles_required("admin", "manager")
def index():
    user = current_user()
    is_admin = user["role"] == "admin"

    pending = db.fetch_all(
        """
        SELECT a.approval_id, a.document_id, a.created_at,
               d.document_number, d.title, d.status, d.priority,
               d.submission_date, d.due_date,
               dept.department_name, dt.type_name,
               submitter.full_name AS submitter_name,
               approver.full_name AS approver_name
          FROM approvals a
          JOIN documents d ON d.document_id = a.document_id
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
          JOIN users submitter ON submitter.user_id = d.submitted_by
          JOIN users approver ON approver.user_id = a.approver_id
         WHERE a.decision = 'pending'
           AND d.status = 'Pending'
           AND d.archived = FALSE
           AND (a.approver_id = %s OR %s = 1)
         ORDER BY d.due_date IS NULL, d.due_date, a.created_at
        """,
        (user["user_id"], 1 if is_admin else 0),
    )

    recent = db.fetch_all(
        """
        SELECT a.approval_id, a.document_id, a.decision, a.remarks,
               a.decision_timestamp,
               d.document_number, d.title, d.status,
               approver.full_name AS approver_name
          FROM approvals a
          JOIN documents d ON d.document_id = a.document_id
          JOIN users approver ON approver.user_id = a.approver_id
         WHERE a.decision <> 'pending'
           AND (a.approver_id = %s OR %s = 1)
         ORDER BY a.decision_timestamp DESC
         LIMIT 8
        """,
        (user["user_id"], 1 if is_admin else 0),
    )

    return render_template(
        "approvals/index.html", pending=pending, recent=recent, active_page="approvals"
    )


@approvals_bp.post("/approvals/<int:approval_id>/decide")
@login_required
@roles_required("admin", "manager")
def decide(approval_id):
    user = current_user()
    decision = (request.form.get("decision") or "").strip().lower()
    remarks = (request.form.get("remarks") or "").strip()

    if decision not in {"approved", "rejected"}:
        abort(400, description="Unknown approval decision.")

    if decision == "rejected" and not remarks:
        flash("A rejection must include remarks explaining the reason.", "danger")
        return redirect(url_for("approvals.index"))

    approval = db.fetch_one(
        "SELECT * FROM approvals WHERE approval_id = %s", (approval_id,)
    )
    if approval is None:
        abort(404)

    try:
        with db.transaction() as cur:
            # Lock the approval and the document so a decision can happen once.
            cur.execute(
                "SELECT * FROM approvals WHERE approval_id = %s FOR UPDATE", (approval_id,)
            )
            locked_approval = cur.fetchone()
            if locked_approval is None:
                abort(404)

            cur.execute(DOCUMENT_SELECT + " WHERE d.document_id = %s FOR UPDATE",
                        (locked_approval["document_id"],))
            doc = cur.fetchone()
            if doc is None:
                abort(404)

            if not workflow.can_decide_approval(user, doc, locked_approval):
                abort(403)
            if locked_approval["decision"] != "pending":
                raise WorkflowError("This approval has already been decided.")

            new_status = "Approved" if decision == "approved" else "Rejected"
            if not workflow.is_valid_transition(doc["status"], new_status):
                raise WorkflowError("The document is not waiting for approval.")

            cur.execute(
                """
                UPDATE approvals
                   SET decision = %s,
                       remarks = %s,
                       decision_timestamp = NOW()
                 WHERE approval_id = %s
                """,
                (decision, remarks or None, approval_id),
            )

            action = "Document approved" if decision == "approved" else "Document rejected"
            note = remarks or ("Approved by " + user["full_name"])
            db.set_tracking_context(cur, user["user_id"], action, note)
            cur.execute(
                "UPDATE documents SET status = %s WHERE document_id = %s",
                (new_status, locked_approval["document_id"]),
            )
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("approvals.index"))

    flash(f"Document {doc['document_number']} {decision}.", "success")
    return redirect(url_for("documents.detail", document_id=doc["document_id"]))
