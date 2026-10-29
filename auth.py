"""Login, logout, CSRF protection and role-based access control.

The rules are enforced here (on the server), not only by hiding links in HTML.
"""

import hmac
import secrets
from functools import wraps

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import db

auth_bp = Blueprint("auth", __name__)

ROLE_LABELS = {
    "admin": "Administrator",
    "records_officer": "Records Officer",
    "employee": "Employee",
    "manager": "Manager / Approver",
}


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------
def load_logged_in_user():
    """Load the user from the session on every request."""
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        user = db.fetch_one(
            """
            SELECT u.user_id, u.full_name, u.email, u.role, u.department_id,
                   u.is_active, d.department_name
              FROM users u
              LEFT JOIN departments d ON d.department_id = u.department_id
             WHERE u.user_id = %s
            """,
            (user_id,),
        )
        if user is None or not user["is_active"]:
            # Deleted or deactivated while logged in: end the session.
            session.clear()
            g.user = None
        else:
            g.user = user


def current_user():
    return getattr(g, "user", None)


def login_user(user):
    # A fresh session id/value prevents session fixation after login.
    session.clear()
    session.permanent = True
    session["user_id"] = user["user_id"]
    session["role"] = user["role"]


def logout_user():
    session.clear()


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
def csrf_token():
    """Return (and lazily create) the CSRF token stored in the session."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    sent = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token", "")
    stored = session.get("_csrf_token", "")
    if not stored or not hmac.compare_digest(stored, sent):
        abort(400, description="Your form session expired. Please reload the page and try again.")


def init_csrf(app):
    @app.before_request
    def _csrf_protect():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf()


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    """Allow only the listed roles (must be combined with login_required)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


admin_required = roles_required("admin")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = db.fetch_one(
            "SELECT * FROM users WHERE email = %s",
            (email,),
        )

        if user is None or not check_password_hash(user["password_hash"], password):
            # Deliberately the same message for unknown email and wrong password.
            flash("Invalid email or password.", "danger")
            return render_template("login.html", email=email), 401

        if not user["is_active"]:
            flash("This account has been deactivated. Please contact the administrator.", "danger")
            return render_template("login.html", email=email), 403

        login_user(user)
        flash(f"Welcome back, {user['full_name']}.", "success")

        next_url = request.args.get("next") or ""
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for("dashboard.index"))

    return render_template("login.html", email="")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------
@auth_bp.app_context_processor
def inject_auth_helpers():
    return {
        "current_user": current_user(),
        "csrf_token": csrf_token,
        "role_labels": ROLE_LABELS,
    }
