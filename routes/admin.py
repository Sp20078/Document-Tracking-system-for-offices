"""Administration: users, departments and document types."""

import mysql.connector
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from werkzeug.security import generate_password_hash

import db
from auth import ROLE_LABELS, admin_required, current_user, login_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _departments():
    return db.fetch_all("SELECT * FROM departments ORDER BY department_name")


def _is_duplicate(exc):
    return isinstance(exc, mysql.connector.Error) and exc.errno == 1062


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@admin_bp.route("/users")
@login_required
@admin_required
def users():
    rows = db.fetch_all(
        """
        SELECT u.user_id, u.full_name, u.email, u.role, u.is_active, u.created_at,
               d.department_id, d.department_name
          FROM users u
          LEFT JOIN departments d ON d.department_id = u.department_id
         ORDER BY u.is_active DESC, u.full_name
        """
    )
    return render_template(
        "admin/users.html", users=rows, departments=_departments(),
        roles=ROLE_LABELS, active_page="users",
    )


@admin_bp.post("/users")
@login_required
@admin_required
def create_user():
    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    role = request.form.get("role") or ""
    department_id = request.form.get("department_id", type=int)

    errors = []
    if not full_name:
        errors.append("Full name is required.")
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if role not in ROLE_LABELS:
        errors.append("Please choose a valid role.")
    if department_id and db.fetch_one(
        "SELECT 1 AS x FROM departments WHERE department_id = %s", (department_id,)
    ) is None:
        errors.append("That department does not exist.")

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("admin.users"))

    try:
        db.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, department_id, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            """,
            (full_name, email, generate_password_hash(password), role, department_id or None),
        )
    except mysql.connector.Error as exc:
        if _is_duplicate(exc):
            flash("That email address is already registered.", "danger")
        else:
            raise
        return redirect(url_for("admin.users"))

    flash(f"User {full_name} created.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/toggle")
@login_required
@admin_required
def toggle_user(user_id):
    if user_id == current_user()["user_id"]:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.users"))

    user = db.fetch_one("SELECT user_id, full_name, is_active FROM users WHERE user_id = %s", (user_id,))
    if user is None:
        abort(404)

    new_state = not user["is_active"]
    db.execute("UPDATE users SET is_active = %s WHERE user_id = %s", (new_state, user_id))
    flash(
        f"{user['full_name']} has been {'reactivated' if new_state else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_user(user_id):
    user = db.fetch_one("SELECT * FROM users WHERE user_id = %s", (user_id,))
    if user is None:
        abort(404)

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role = request.form.get("role") or ""
        department_id = request.form.get("department_id", type=int)
        new_password = request.form.get("password") or ""

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if not email or "@" not in email:
            errors.append("A valid email address is required.")
        if role not in ROLE_LABELS:
            errors.append("Please choose a valid role.")
        if user_id == current_user()["user_id"] and role != "admin":
            errors.append("You cannot remove your own administrator role.")
        if new_password and len(new_password) < 8:
            errors.append("A new password must be at least 8 characters long.")

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            try:
                if new_password:
                    db.execute(
                        """
                        UPDATE users
                           SET full_name = %s, email = %s, role = %s,
                               department_id = %s, password_hash = %s
                         WHERE user_id = %s
                        """,
                        (full_name, email, role, department_id or None,
                         generate_password_hash(new_password), user_id),
                    )
                else:
                    db.execute(
                        """
                        UPDATE users
                           SET full_name = %s, email = %s, role = %s, department_id = %s
                         WHERE user_id = %s
                        """,
                        (full_name, email, role, department_id or None, user_id),
                    )
            except mysql.connector.Error as exc:
                if _is_duplicate(exc):
                    flash("That email address is already registered.", "danger")
                else:
                    raise
            else:
                flash("User updated.", "success")
                return redirect(url_for("admin.users"))

    return render_template(
        "admin/user_edit.html", user=user, departments=_departments(),
        roles=ROLE_LABELS, active_page="users",
    )


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
@admin_bp.route("/departments")
@login_required
@admin_required
def departments():
    rows = db.fetch_all(
        """
        SELECT d.*, COUNT(u.user_id) AS user_count,
               COUNT(doc.document_id) AS document_count
          FROM departments d
          LEFT JOIN users u ON u.department_id = d.department_id AND u.is_active = TRUE
          LEFT JOIN documents doc ON doc.current_department_id = d.department_id
         GROUP BY d.department_id, d.department_name, d.description, d.created_at
         ORDER BY d.department_name
        """
    )
    return render_template("admin/departments.html", departments=rows, active_page="departments")


@admin_bp.post("/departments")
@login_required
@admin_required
def create_department():
    name = (request.form.get("department_name") or "").strip()
    description = (request.form.get("description") or "").strip() or None

    if not name:
        flash("Department name is required.", "danger")
        return redirect(url_for("admin.departments"))

    try:
        db.execute(
            "INSERT INTO departments (department_name, description) VALUES (%s, %s)",
            (name, description),
        )
    except mysql.connector.Error as exc:
        if _is_duplicate(exc):
            flash("A department with that name already exists.", "danger")
            return redirect(url_for("admin.departments"))
        raise

    flash(f"Department {name} created.", "success")
    return redirect(url_for("admin.departments"))


@admin_bp.post("/departments/<int:department_id>/edit")
@login_required
@admin_required
def edit_department(department_id):
    department = db.fetch_one(
        "SELECT * FROM departments WHERE department_id = %s", (department_id,)
    )
    if department is None:
        abort(404)

    name = (request.form.get("department_name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Department name is required.", "danger")
        return redirect(url_for("admin.departments"))

    try:
        db.execute(
            "UPDATE departments SET department_name = %s, description = %s WHERE department_id = %s",
            (name, description, department_id),
        )
    except mysql.connector.Error as exc:
        if _is_duplicate(exc):
            flash("Another department already uses that name.", "danger")
            return redirect(url_for("admin.departments"))
        raise

    flash("Department updated.", "success")
    return redirect(url_for("admin.departments"))


# ---------------------------------------------------------------------------
# Document types
# ---------------------------------------------------------------------------
@admin_bp.route("/document-types")
@login_required
@admin_required
def document_types():
    rows = db.fetch_all(
        """
        SELECT t.*, COUNT(d.document_id) AS document_count
          FROM document_types t
          LEFT JOIN documents d ON d.document_type_id = t.document_type_id
         GROUP BY t.document_type_id, t.type_name, t.description
         ORDER BY t.type_name
        """
    )
    return render_template(
        "admin/document_types.html", document_types=rows, active_page="types"
    )


@admin_bp.post("/document-types")
@login_required
@admin_required
def create_document_type():
    name = (request.form.get("type_name") or "").strip()
    description = (request.form.get("description") or "").strip() or None

    if not name:
        flash("Document type name is required.", "danger")
        return redirect(url_for("admin.document_types"))

    try:
        db.execute(
            "INSERT INTO document_types (type_name, description) VALUES (%s, %s)",
            (name, description),
        )
    except mysql.connector.Error as exc:
        if _is_duplicate(exc):
            flash("That document type already exists.", "danger")
            return redirect(url_for("admin.document_types"))
        raise

    flash(f"Document type {name} created.", "success")
    return redirect(url_for("admin.document_types"))


@admin_bp.post("/document-types/<int:type_id>/edit")
@login_required
@admin_required
def edit_document_type(type_id):
    doc_type = db.fetch_one(
        "SELECT * FROM document_types WHERE document_type_id = %s", (type_id,)
    )
    if doc_type is None:
        abort(404)

    name = (request.form.get("type_name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Document type name is required.", "danger")
        return redirect(url_for("admin.document_types"))

    try:
        db.execute(
            "UPDATE document_types SET type_name = %s, description = %s WHERE document_type_id = %s",
            (name, description, type_id),
        )
    except mysql.connector.Error as exc:
        if _is_duplicate(exc):
            flash("Another document type already uses that name.", "danger")
            return redirect(url_for("admin.document_types"))
        raise

    flash("Document type updated.", "success")
    return redirect(url_for("admin.document_types"))
