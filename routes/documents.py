"""Document registration, listing, details and workflow actions."""

import os
import uuid
from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, url_for)
from werkzeug.utils import secure_filename

import db
import workflow
from auth import current_user, login_required, roles_required
from routes.common import (document_scope, get_document_or_404,
                           pending_approval_for)

documents_bp = Blueprint("documents", __name__)

DOCUMENT_SELECT = """
    SELECT d.*, dt.type_name,
           dept.department_name,
           ua.full_name AS assignee_name,
           us.full_name AS submitter_name
      FROM documents d
      JOIN document_types dt ON dt.document_type_id = d.document_type_id
      LEFT JOIN departments dept ON dept.department_id = d.current_department_id
      LEFT JOIN users ua ON ua.user_id = d.assigned_to
      JOIN users us ON us.user_id = d.submitted_by
"""


class WorkflowError(Exception):
    """Raised when an action is not allowed; shown to the user as a flash message."""


# ---------------------------------------------------------------------------
# List / search
# ---------------------------------------------------------------------------
@documents_bp.route("/documents")
@login_required
def index():
    user = current_user()
    scope_sql, scope_params = document_scope(user)

    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    department_id = request.args.get("department_id", type=int)
    document_type_id = request.args.get("document_type_id", type=int)
    priority = request.args.get("priority") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    show_archived = request.args.get("archived") == "1"

    where = [scope_sql, "d.archived = %s"]
    params = [*scope_params, 1 if show_archived else 0]

    if q:
        where.append("(d.document_number LIKE %s OR d.title LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if status in workflow.STATUSES:
        where.append("d.status = %s")
        params.append(status)
    if department_id:
        where.append("d.current_department_id = %s")
        params.append(department_id)
    if document_type_id:
        where.append("d.document_type_id = %s")
        params.append(document_type_id)
    if priority in workflow.PRIORITIES:
        where.append("d.priority = %s")
        params.append(priority)
    if date_from:
        where.append("d.submission_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("d.submission_date <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where)

    total = db.fetch_one(
        f"SELECT COUNT(*) AS total FROM documents d WHERE {where_sql}", tuple(params)
    )["total"]

    per_page = current_app.config["ITEMS_PER_PAGE"]
    page = max(request.args.get("page", type=int, default=1), 1)
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(page, pages)

    documents = db.fetch_all(
        f"""
        SELECT d.document_id, d.document_number, d.title, d.status, d.priority,
               d.submission_date, d.due_date, d.archived, d.updated_at,
               dt.type_name, dept.department_name, ua.full_name AS assignee_name
          FROM documents d
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
          LEFT JOIN users ua ON ua.user_id = d.assigned_to
         WHERE {where_sql}
         ORDER BY d.updated_at DESC
         LIMIT %s OFFSET %s
        """,
        tuple(params) + (per_page, (page - 1) * per_page),
    )

    departments = db.fetch_all("SELECT * FROM departments ORDER BY department_name")
    document_types = db.fetch_all("SELECT * FROM document_types ORDER BY type_name")

    filters = {
        "q": q,
        "status": status,
        "department_id": department_id,
        "document_type_id": document_type_id,
        "priority": priority,
        "date_from": date_from,
        "date_to": date_to,
        "archived": show_archived,
    }

    return render_template(
        "documents/index.html",
        documents=documents,
        departments=departments,
        document_types=document_types,
        filters=filters,
        total=total,
        page=page,
        pages=pages,
        active_page="documents",
    )


@documents_bp.route("/my-assignments")
@login_required
def my_assignments():
    user = current_user()
    documents = db.fetch_all(
        """
        SELECT d.document_id, d.document_number, d.title, d.status, d.priority,
               d.submission_date, d.due_date, d.updated_at, dt.type_name,
               dept.department_name,
               (d.due_date IS NOT NULL AND d.due_date < CURDATE()
                AND d.status IN ('Submitted', 'In Progress', 'Forwarded')) AS is_overdue
          FROM documents d
          JOIN document_types dt ON dt.document_type_id = d.document_type_id
          LEFT JOIN departments dept ON dept.department_id = d.current_department_id
         WHERE d.assigned_to = %s AND d.archived = FALSE
         ORDER BY FIELD(d.priority, 'Urgent', 'High', 'Normal', 'Low'), d.due_date IS NULL, d.due_date
        """,
        (user["user_id"],),
    )
    return render_template(
        "documents/assignments.html", documents=documents, active_page="assignments"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def _form_options():
    return {
        "departments": db.fetch_all("SELECT * FROM departments ORDER BY department_name"),
        "document_types": db.fetch_all("SELECT * FROM document_types ORDER BY type_name"),
        "assignees": db.fetch_all(
            """
            SELECT user_id, full_name, department_id, role
              FROM users
             WHERE is_active = TRUE
             ORDER BY full_name
            """
        ),
    }


@documents_bp.route("/documents/register", methods=["GET", "POST"])
@login_required
@roles_required("admin", "records_officer")
def register():
    user = current_user()
    options = _form_options()
    form = {
        "title": "",
        "description": "",
        "document_type_id": "",
        "department_id": str(user["department_id"] or ""),
        "assigned_to": "",
        "submission_date": date.today().isoformat(),
        "due_date": "",
        "priority": "Normal",
    }
    errors = {}

    if request.method == "POST":
        form = {key: (request.form.get(key) or "").strip() for key in form}
        upload = request.files.get("attachment")

        # ---- validation ----
        if not form["title"]:
            errors["title"] = "Title is required."
        elif len(form["title"]) > 200:
            errors["title"] = "Title must be 200 characters or fewer."

        type_ids = {str(row["document_type_id"]) for row in options["document_types"]}
        if form["document_type_id"] not in type_ids:
            errors["document_type_id"] = "Please choose a document type."

        dept_ids = {str(row["department_id"]) for row in options["departments"]}
        if form["department_id"] not in dept_ids:
            errors["department_id"] = "Please choose a department."

        try:
            submission_date = date.fromisoformat(form["submission_date"])
        except ValueError:
            submission_date = None
            errors["submission_date"] = "A valid submission date is required."

        due_date = None
        if form["due_date"]:
            try:
                due_date = date.fromisoformat(form["due_date"])
                if submission_date and due_date < submission_date:
                    errors["due_date"] = "Due date cannot be before the submission date."
            except ValueError:
                errors["due_date"] = "Due date is not a valid date."

        if form["priority"] not in workflow.PRIORITIES:
            errors["priority"] = "Please choose a priority."

        assignee = None
        if form["assigned_to"]:
            assignee = db.fetch_one(
                "SELECT user_id, full_name, department_id, is_active FROM users WHERE user_id = %s",
                (form["assigned_to"],),
            )
            if assignee is None or not assignee["is_active"]:
                errors["assigned_to"] = "Selected employee was not found or is inactive."
            elif str(assignee["department_id"]) != form["department_id"]:
                errors["assigned_to"] = "The employee must belong to the selected department."

        if upload and upload.filename:
            ext = _extension(upload.filename)
            if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
                errors["attachment"] = "Allowed file types: PDF, PNG, JPG, JPEG, DOCX."

        if errors:
            return render_template(
                "documents/register.html", form=form, errors=errors,
                active_page="register", **options
            ), 400

        saved = None
        try:
            if upload and upload.filename:
                saved = _save_upload(upload)

            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (document_number, title, description, document_type_id,
                         submitted_by, current_department_id, assigned_to,
                         submission_date, due_date, priority, status)
                    VALUES (CONCAT('TMP-', UUID()), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Submitted')
                    """,
                    (form["title"], form["description"] or None,
                     int(form["document_type_id"]), user["user_id"],
                     int(form["department_id"]),
                     assignee["user_id"] if assignee else None,
                     submission_date, due_date, form["priority"]),
                )
                document_id = cur.lastrowid
                number = db.generate_document_number(cur, document_id, submission_date.year)

                # The registration tracking row is written by the DB trigger.
                if saved:
                    cur.execute(
                        """
                        INSERT INTO attachments
                            (document_id, uploaded_by, original_filename, stored_filename,
                             storage_path, file_size)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (document_id, user["user_id"], saved["original_filename"],
                         saved["stored_filename"], saved["storage_path"], saved["file_size"]),
                    )
        except Exception:
            # Never leave a saved file behind when the transaction fails.
            if saved and os.path.exists(saved["storage_path"]):
                os.remove(saved["storage_path"])
            current_app.logger.exception("Document registration failed")
            flash("The document could not be registered. Please try again.", "danger")
            return render_template(
                "documents/register.html", form=form, errors={},
                active_page="register", **options
            ), 500

        flash(f"Document {number} registered successfully.", "success")
        return redirect(url_for("documents.detail", document_id=document_id))

    return render_template(
        "documents/register.html", form=form, errors={}, active_page="register", **options
    )


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------
@documents_bp.route("/documents/<int:document_id>")
@login_required
def detail(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    pending = pending_approval_for(document_id)

    tracking = db.fetch_all(
        """
        SELECT t.*, actor.full_name AS actor_name,
               fd.department_name AS from_department,
               td.department_name AS to_department,
               fu.full_name AS from_user_name,
               tu.full_name AS to_user_name
          FROM document_tracking t
          LEFT JOIN users actor ON actor.user_id = t.actor_id
          LEFT JOIN departments fd ON fd.department_id = t.from_department_id
          LEFT JOIN departments td ON td.department_id = t.to_department_id
          LEFT JOIN users fu ON fu.user_id = t.from_user_id
          LEFT JOIN users tu ON tu.user_id = t.to_user_id
         WHERE t.document_id = %s
         ORDER BY t.action_timestamp ASC, t.tracking_id ASC
        """,
        (document_id,),
    )

    attachments = db.fetch_all(
        """
        SELECT a.*, u.full_name AS uploader_name
          FROM attachments a
          JOIN users u ON u.user_id = a.uploaded_by
         WHERE a.document_id = %s
         ORDER BY a.uploaded_at ASC
        """,
        (document_id,),
    )

    approvals = db.fetch_all(
        """
        SELECT a.*, u.full_name AS approver_name
          FROM approvals a
          JOIN users u ON u.user_id = a.approver_id
         WHERE a.document_id = %s
         ORDER BY a.created_at ASC
        """,
        (document_id,),
    )

    actions = workflow.available_actions(user, doc, pending)
    if pending and not workflow.can_decide_approval(user, doc, pending):
        actions["decide"] = False

    forward_users = db.fetch_all(
        """
        SELECT user_id, full_name, department_id
          FROM users
         WHERE is_active = TRUE
         ORDER BY full_name
        """
    )
    approvers = db.fetch_all(
        """
        SELECT user_id, full_name
          FROM users
         WHERE is_active = TRUE AND role IN ('manager', 'admin')
         ORDER BY full_name
        """
    )
    departments = db.fetch_all("SELECT department_id, department_name FROM departments ORDER BY department_name")

    return render_template(
        "documents/detail.html",
        doc=doc,
        tracking=tracking,
        attachments=attachments,
        approvals=approvals,
        pending_approval=pending,
        actions=actions,
        forward_users=forward_users,
        approvers=approvers,
        departments=departments,
        active_page="documents",
    )


# ---------------------------------------------------------------------------
# Workflow actions
# ---------------------------------------------------------------------------
def _locked_document(cur, document_id, user):
    """Re-read the row with FOR UPDATE so two users cannot race each other."""
    cur.execute(DOCUMENT_SELECT + " WHERE d.document_id = %s FOR UPDATE", (document_id,))
    locked = cur.fetchone()
    if locked is None:
        abort(404)
    if not workflow.can_view_document(user, locked):
        abort(403)
    return locked


def _apply_status_change(cur, document_id, new_status, current_status):
    if not workflow.is_valid_transition(current_status, new_status):
        raise WorkflowError(
            f"The document cannot move from '{current_status}' to '{new_status}'."
        )


@documents_bp.post("/documents/<int:document_id>/assign")
@login_required
def assign(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_assign(user, doc):
        abort(403)

    assignee_id = request.form.get("assigned_to", type=int)
    remarks = (request.form.get("remarks") or "").strip() or None

    if not assignee_id:
        flash("Please choose an employee to assign the document to.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    assignee = db.fetch_one(
        "SELECT user_id, full_name, department_id, is_active FROM users WHERE user_id = %s",
        (assignee_id,),
    )
    if assignee is None or not assignee["is_active"]:
        flash("That employee is inactive or does not exist.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))
    if assignee["department_id"] is None:
        flash("The employee must belong to a department before documents can be assigned.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    try:
        with db.transaction() as cur:
            locked = _locked_document(cur, document_id, user)
            if not workflow.can_assign(user, locked):
                raise WorkflowError("This document can no longer be assigned.")

            new_status = "In Progress"
            _apply_status_change(cur, document_id, new_status, locked["status"])

            note = f"Assigned to {assignee['full_name']}."
            if remarks:
                note += f" {remarks}"

            db.set_tracking_context(cur, user["user_id"], "Document assigned", note)
            cur.execute(
                """
                UPDATE documents
                   SET assigned_to = %s,
                       current_department_id = %s,
                       status = %s
                 WHERE document_id = %s
                """,
                (assignee["user_id"], assignee["department_id"], new_status, document_id),
            )
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash(f"Document assigned to {assignee['full_name']}.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.post("/documents/<int:document_id>/forward")
@login_required
def forward(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_forward(user, doc):
        abort(403)

    to_user_id = request.form.get("to_user_id", type=int)
    to_department_id = request.form.get("to_department_id", type=int)
    remarks = (request.form.get("remarks") or "").strip() or None

    if not to_user_id and not to_department_id:
        flash("Choose a department or an employee to forward the document to.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    target_user = None
    if to_user_id:
        target_user = db.fetch_one(
            "SELECT user_id, full_name, department_id, is_active FROM users WHERE user_id = %s",
            (to_user_id,),
        )
        if target_user is None or not target_user["is_active"]:
            flash("That employee is inactive or does not exist.", "danger")
            return redirect(url_for("documents.detail", document_id=document_id))
        if target_user["department_id"] is None:
            flash("The employee must belong to a department.", "danger")
            return redirect(url_for("documents.detail", document_id=document_id))
        to_department_id = target_user["department_id"]

    department = db.fetch_one(
        "SELECT department_id, department_name FROM departments WHERE department_id = %s",
        (to_department_id,),
    )
    if department is None:
        flash("That department does not exist.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    if target_user and target_user["user_id"] == doc["assigned_to"]:
        flash("The document is already assigned to that employee.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    try:
        with db.transaction() as cur:
            locked = _locked_document(cur, document_id, user)
            if not workflow.can_forward(user, locked):
                raise WorkflowError("This document can no longer be forwarded.")
            _apply_status_change(cur, document_id, "Forwarded", locked["status"])

            note = f"Forwarded to {department['department_name']}"
            note += f" ({target_user['full_name']})" if target_user else " department"
            if remarks:
                note += f". {remarks}"

            db.set_tracking_context(cur, user["user_id"], "Document forwarded", note)
            cur.execute(
                """
                UPDATE documents
                   SET assigned_to = %s,
                       current_department_id = %s,
                       status = 'Forwarded'
                 WHERE document_id = %s
                """,
                (target_user["user_id"] if target_user else None,
                 department["department_id"], document_id),
            )
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash("Document forwarded successfully.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.post("/documents/<int:document_id>/submit-approval")
@login_required
def submit_for_approval(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_submit_for_approval(user, doc):
        abort(403)

    approver_id = request.form.get("approver_id", type=int)
    remarks = (request.form.get("remarks") or "").strip() or None

    if not approver_id:
        flash("Please choose an approver.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    approver = db.fetch_one(
        "SELECT user_id, full_name, role, is_active FROM users WHERE user_id = %s",
        (approver_id,),
    )
    if approver is None or not approver["is_active"] or approver["role"] not in {"manager", "admin"}:
        flash("Please choose an active manager or administrator as the approver.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))
    if approver["user_id"] in {doc["submitted_by"], doc["assigned_to"]}:
        flash("A document cannot be sent to its own submitter or assignee for approval.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    try:
        with db.transaction() as cur:
            locked = _locked_document(cur, document_id, user)
            if not workflow.can_submit_for_approval(user, locked):
                raise WorkflowError("This document can no longer be sent for approval.")

            cur.execute(
                "SELECT approval_id FROM approvals WHERE document_id = %s AND decision = 'pending'",
                (document_id,),
            )
            if cur.fetchone():
                raise WorkflowError("This document already has a pending approval.")

            _apply_status_change(cur, document_id, "Pending", locked["status"])

            cur.execute(
                "INSERT INTO approvals (document_id, approver_id, decision) VALUES (%s, %s, 'pending')",
                (document_id, approver["user_id"]),
            )

            note = f"Awaiting decision from {approver['full_name']}."
            if remarks:
                note += f" {remarks}"
            db.set_tracking_context(cur, user["user_id"], "Sent for approval", note)
            cur.execute(
                "UPDATE documents SET status = 'Pending' WHERE document_id = %s",
                (document_id,),
            )
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash(f"Document sent to {approver['full_name']} for approval.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.post("/documents/<int:document_id>/close")
@login_required
def close(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_close(user, doc):
        abort(403)
    remarks = (request.form.get("remarks") or "").strip() or None

    try:
        with db.transaction() as cur:
            locked = _locked_document(cur, document_id, user)
            if not workflow.can_close(user, locked):
                raise WorkflowError("Only approved or rejected documents can be closed.")
            _apply_status_change(cur, document_id, "Closed", locked["status"])

            db.set_tracking_context(cur, user["user_id"], "Document closed", remarks)
            cur.execute("UPDATE documents SET status = 'Closed' WHERE document_id = %s", (document_id,))
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash("Document closed.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.post("/documents/<int:document_id>/archive")
@login_required
def archive(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_archive(user, doc):
        abort(403)

    try:
        with db.transaction() as cur:
            locked = _locked_document(cur, document_id, user)
            if not workflow.can_archive(user, locked):
                raise WorkflowError("This document cannot be archived yet.")

            db.set_tracking_context(cur, user["user_id"], "Document archived", None)
            cur.execute("UPDATE documents SET archived = TRUE WHERE document_id = %s", (document_id,))
            db.clear_tracking_context(cur)
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash("Document archived. It is now read-only.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.post("/documents/<int:document_id>/remarks")
@login_required
def add_remarks(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_add_remarks(user, doc):
        abort(403)

    remarks = (request.form.get("remarks") or "").strip()
    if not remarks:
        flash("Please type a remark before saving.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    with db.transaction() as cur:
        db.add_tracking(cur, document_id, "Remarks added", user["user_id"], remarks)

    flash("Remark added to the tracking history.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
def _extension(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def _save_upload(file_storage):
    """Validate and store one uploaded file. Returns metadata for MySQL."""
    original = secure_filename(file_storage.filename) or "attachment"
    ext = _extension(original)
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise WorkflowError("Allowed file types: PDF, PNG, JPG, JPEG, DOCX.")

    stored_filename = f"{uuid.uuid4().hex}.{ext}"
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    storage_path = os.path.join(upload_folder, stored_filename)
    file_storage.save(storage_path)

    size = os.path.getsize(storage_path)
    if size <= 0:
        os.remove(storage_path)
        raise WorkflowError("The uploaded file is empty.")
    if size > current_app.config["MAX_CONTENT_LENGTH"]:
        os.remove(storage_path)
        raise WorkflowError("The file is larger than the 5 MB limit.")

    return {
        "original_filename": original,
        "stored_filename": stored_filename,
        "storage_path": storage_path,
        "file_size": size,
    }


@documents_bp.post("/documents/<int:document_id>/attachments")
@login_required
def upload_attachment(document_id):
    user = current_user()
    doc = get_document_or_404(document_id)
    if not workflow.can_attach(user, doc):
        abort(403)

    upload = request.files.get("attachment")
    if upload is None or not upload.filename:
        flash("Please choose a file to upload.", "warning")
        return redirect(url_for("documents.detail", document_id=document_id))

    try:
        saved = _save_upload(upload)
        with db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO attachments
                    (document_id, uploaded_by, original_filename, stored_filename,
                     storage_path, file_size)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (document_id, user["user_id"], saved["original_filename"],
                 saved["stored_filename"], saved["storage_path"], saved["file_size"]),
            )
            db.add_tracking(
                cur, document_id, "Attachment uploaded", user["user_id"],
                f"Uploaded {saved['original_filename']}",
            )
    except WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("documents.detail", document_id=document_id))
    except Exception:
        current_app.logger.exception("Attachment upload failed")
        flash("The attachment could not be saved.", "danger")
        return redirect(url_for("documents.detail", document_id=document_id))

    flash("Attachment uploaded.", "success")
    return redirect(url_for("documents.detail", document_id=document_id))


@documents_bp.route("/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(attachment_id):
    user = current_user()
    attachment = db.fetch_one(
        "SELECT * FROM attachments WHERE attachment_id = %s", (attachment_id,)
    )
    if attachment is None:
        abort(404)

    doc = get_document_or_404(attachment["document_id"])
    if not workflow.can_view_document(user, doc, doc.get("has_approval")):
        abort(403)

    upload_folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    path = os.path.realpath(attachment["storage_path"])
    # Defence in depth: never serve a path outside the uploads folder.
    if not path.startswith(upload_folder + os.sep) or not os.path.exists(path):
        abort(404)

    return send_file(path, as_attachment=True,
                     download_name=attachment["original_filename"])
