"""Authentication, session and password tests."""

import db
from tests.conftest import DEV_PASSWORD, get_token, login


def test_login_and_logout(client, users):
    response = login(client, users["admin"]["email"])
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"] or response.headers["Location"].endswith("/")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"Dashboard" in dashboard.data

    token = get_token(client, "/documents")
    logout = client.post("/logout", data={"_csrf_token": token})
    assert logout.status_code == 302

    protected = client.get("/documents")
    assert protected.status_code == 302
    assert "/login" in protected.headers["Location"]


def test_wrong_password_is_rejected(client, users):
    response = login(client, users["admin"]["email"], password="wrong-password")
    assert response.status_code == 401
    assert b"Invalid email or password" in response.data


def test_unknown_email_gets_same_message(client):
    response = login(client, "nobody@test.local")
    assert response.status_code == 401
    assert b"Invalid email or password" in response.data


def test_post_without_csrf_token_is_rejected(client, users):
    response = client.post(
        "/login",
        data={"email": users["admin"]["email"], "password": DEV_PASSWORD},
    )
    assert response.status_code == 400
    assert b"form session expired" in response.data.lower()


def test_passwords_are_stored_as_hashes(flask_app, users):
    with flask_app.app_context():
        rows = db.fetch_all("SELECT email, password_hash FROM users")
    assert rows
    for row in rows:
        assert row["password_hash"] != DEV_PASSWORD
        # Werkzeug hashes look like "scrypt:32768:8:1$..." or "pbkdf2:sha256:..."
        assert ":" in row["password_hash"] and "$" in row["password_hash"]


def test_deactivated_user_cannot_log_in(client, flask_app, users):
    with flask_app.app_context():
        db.execute("UPDATE users SET is_active = FALSE WHERE email = %s", (users["hr"]["email"],))
    try:
        response = login(client, users["hr"]["email"])
        assert response.status_code == 403
        assert b"deactivated" in response.data
    finally:
        with flask_app.app_context():
            db.execute("UPDATE users SET is_active = TRUE WHERE email = %s", (users["hr"]["email"],))


def test_deactivated_user_session_is_ended(flask_app, users):
    client = flask_app.test_client()
    assert login(client, users["employee"]["email"]).status_code == 302
    with flask_app.app_context():
        db.execute("UPDATE users SET is_active = FALSE WHERE email = %s", (users["employee"]["email"],))
    try:
        response = client.get("/documents")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
    finally:
        with flask_app.app_context():
            db.execute("UPDATE users SET is_active = TRUE WHERE email = %s", (users["employee"]["email"],))


def test_404_page_is_friendly(client, users):
    login(client, users["admin"]["email"])
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert b"Not found" in response.data or b"404" in response.data
