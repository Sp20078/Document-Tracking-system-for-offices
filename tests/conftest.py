"""Shared pytest fixtures.

The tests run against a real MySQL database (``office_document_tracking_test``)
so that the schema, triggers, stored procedures, views and constraints are all
exercised exactly as they are in development.

Requirements:
    * MySQL must be running and reachable with the credentials in .env
    * The MySQL user must be allowed to create and drop the test database

Run from the project root:
    python -m pytest -q
"""

import re
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from config import Config  # noqa: E402
from scripts.init_db import split_statements  # noqa: E402

TEST_DB = "office_document_tracking_test"
DEV_PASSWORD = "Password@123"

USERS = [
    # key, full name, email, role, department
    ("admin", "System Administrator", "admin@test.local", "admin", "Administration"),
    ("officer", "Rita Sharma", "officer@test.local", "records_officer", "Administration"),
    ("manager", "Meera Nair", "manager@test.local", "manager", "Finance"),
    ("employee", "Arjun Patel", "employee@test.local", "employee", "Finance"),
    ("hr", "Priya Das", "hr@test.local", "employee", "Human Resources"),
]


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    MYSQL_DATABASE = TEST_DB
    DEBUG = False


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def _apply_sql_files(connection):
    for filename in ("schema.sql", "advanced_features.sql", "seed.sql"):
        text = (ROOT / "sql" / filename).read_text(encoding="utf-8")
        # The SQL files target the development database; point them at the
        # test database instead.
        text = text.replace(Config.MYSQL_DATABASE, TEST_DB)
        cursor = connection.cursor()
        for statement in split_statements(text):
            cursor.execute(statement)
        cursor.close()
    connection.commit()


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    connection = db.create_connection(Config, database=None)
    cursor = connection.cursor()
    cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    cursor.execute(
        f"CREATE DATABASE {TEST_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.close()
    conn = db.create_connection(Config, database=TEST_DB)
    _apply_sql_files(conn)
    conn.close()
    connection.close()
    yield


@pytest.fixture(scope="session")
def flask_app(prepared_database, tmp_path_factory):
    TestConfig.UPLOAD_FOLDER = str(tmp_path_factory.mktemp("uploads"))
    from app import create_app

    app = create_app(TestConfig)
    return app


@pytest.fixture(autouse=True)
def reset_documents(flask_app):
    """Keep every test independent: remove documents, keep departments/users."""
    with flask_app.app_context():
        conn = db.get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM approvals")
        cur.execute("DELETE FROM attachments")
        cur.execute("DELETE FROM document_tracking")
        cur.execute("DELETE FROM documents")
        cur.execute("ALTER TABLE documents AUTO_INCREMENT = 1")
        conn.commit()
    yield


@pytest.fixture(scope="session")
def users(flask_app):
    """Create the test accounts once per session."""
    data = {}
    with flask_app.app_context():
        with db.transaction() as cur:
            cur.execute("DELETE FROM users")
            cur.execute("ALTER TABLE users AUTO_INCREMENT = 1")
            for key, name, email, role, department in USERS:
                cur.execute(
                    "SELECT department_id FROM departments WHERE department_name = %s",
                    (department,),
                )
                department_id = cur.fetchone()["department_id"]
                cur.execute(
                    """
                    INSERT INTO users (full_name, email, password_hash, role, department_id, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    """,
                    (name, email, generate_password_hash(DEV_PASSWORD), role, department_id),
                )
                data[key] = {
                    "user_id": cur.lastrowid,
                    "email": email,
                    "role": role,
                    "department_id": department_id,
                }
    return data


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')


def get_token(client, path="/login"):
    response = client.get(path)
    match = TOKEN_RE.search(response.get_data(as_text=True))
    assert match, f"No CSRF token found on {path}"
    return match.group(1)


def login(client, email, password=DEV_PASSWORD):
    token = get_token(client, "/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "_csrf_token": token},
        follow_redirects=False,
    )


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def logged_in(flask_app, users):
    """Factory: logged_in('officer') -> test client authenticated as that user."""
    clients = []

    def _login(key):
        test_client = flask_app.test_client()
        response = login(test_client, users[key]["email"])
        assert response.status_code == 302, f"Login failed for {key}"
        clients.append(test_client)
        return test_client

    yield _login


def form_token(client):
    """CSRF token for a client that is already logged in."""
    response = client.get("/documents")
    assert response.status_code == 200
    match = TOKEN_RE.search(response.get_data(as_text=True))
    assert match, "No CSRF token on /documents"
    return match.group(1)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def make_document(flask_app, users):
    """Insert a document fixture directly (fast setup for read/permission tests)."""

    def _make(
        title="Test document",
        document_type="Report",
        department="Finance",
        submitted_by="officer",
        assigned_to=None,
        status="Submitted",
        priority="Normal",
        archived=False,
        due_date=None,
    ):
        submitter_id = users[submitted_by]["user_id"]
        assignee_id = users[assigned_to]["user_id"] if assigned_to else None
        with flask_app.app_context():
            with db.transaction() as cur:
                cur.execute(
                    "SELECT document_type_id FROM document_types WHERE type_name = %s",
                    (document_type,),
                )
                type_id = cur.fetchone()["document_type_id"]
                cur.execute(
                    "SELECT department_id FROM departments WHERE department_name = %s",
                    (department,),
                )
                department_id = cur.fetchone()["department_id"]
                cur.execute(
                    """
                    INSERT INTO documents
                        (document_number, title, document_type_id, submitted_by,
                         current_department_id, assigned_to, submission_date,
                         due_date, priority, status, archived)
                    VALUES (CONCAT('TMP-', LEFT(REPLACE(UUID(), '-', ''), 20)),
                            %s, %s, %s, %s, %s, CURDATE(), %s, %s, %s, %s)
                    """,
                    (title, type_id, submitter_id, department_id, assignee_id,
                     due_date, priority, status, archived),
                )
                document_id = cur.lastrowid
                cur.execute(
                    """
                    UPDATE documents
                       SET document_number = CONCAT('DOC-', YEAR(CURDATE()), '-',
                                                   LPAD(%s, 4, '0'))
                     WHERE document_id = %s
                    """,
                    (document_id, document_id),
                )
                cur.execute(
                    "SELECT document_number FROM documents WHERE document_id = %s",
                    (document_id,),
                )
                number = cur.fetchone()["document_number"]
        return {
            "document_id": document_id,
            "document_number": number,
            "department_id": department_id,
            "submitted_by": submitter_id,
            "assigned_to": assignee_id,
        }

    return _make
