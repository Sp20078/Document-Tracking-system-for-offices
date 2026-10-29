"""End-to-end document workflow: register, assign, forward, approve, reject, close."""

import io
import re

import pytest

import db
from tests.conftest import DEV_PASSWORD, form_token, login


def lookup(flask_app, sql, params):
    with flask_app.app_context():
        return db.fetch_one(sql, params)


def tracking_actions(flask_app, document_id):
    with flask_app.app_context():
        rows = db.fetch_all(
            "SELECT action FROM document_tracking WHERE document_id = %s ORDER BY tracking_id",
            (document_id,),
        )
    return [row["action"] for row in rows]


@pytest.fixture
def registration_form(flask_app):
    with flask_app.app_context():
        doc_type = db.fetch_one("SELECT document_type_id FROM document_types WHERE type_name = 'Report'")
        department = db.fetch_one("SELECT department_id FROM departments WHERE department_name = 'Finance'")
        employee = db.fetch_one("SELECT user_id FROM users WHERE email = 'employee@test.local'")
    return {
        "document_type_id": doc_type["document_type_id"],
        "department_id": department["department_id"],
        "employee_id": employee["user_id"],
    }


def test_register_assign_forward_reject_close(logged_in, flask_app, registration_form):
    officer = logged_in("officer")
    token = form_token(officer)
    form = registration_form

    response = officer.post(
        "/documents/register",
        data={
            "title": "Quarterly budget review",
            "description": "Review of the finance department spending.",
            "document_type_id": form["document_type_id"],
            "department_id": form["department_id"],
            "assigned_to": "",
            "submission_date": "2026-10-01",
            "due_date": "2026-10-20",
            "priority": "High",
            "attachment": (io.BytesIO(b"%PDF-1.4 demo attachment"), "budget.pdf"),
            "_csrf_token": token,
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    with flask_app.app_context():
        doc = db.fetch_one(
            "SELECT * FROM documents WHERE title = %s", ("Quarterly budget review",)
        )
        assert doc is not None
        assert re.fullmatch(r"DOC-\d{4}-\d{4}", doc["document_number"])
        assert doc["status"] == "Submitted"
        document_id = doc["document_id"]
        attachment = db.fetch_one(
            "SELECT * FROM attachments WHERE document_id = %s", (document_id,)
        )
    assert attachment is not None

    detail = officer.get(f"/documents/{document_id}")
    assert detail.status_code == 200
    assert b"budget.pdf" in detail.data

    # ---- assign -----------------------------------------------------------
    token = form_token(officer)
    assign = officer.post(
        f"/documents/{document_id}/assign",
        data={"assigned_to": form["employee_id"], "remarks": "Please review.", "_csrf_token": token},
    )
    assert assign.status_code == 302
    with flask_app.app_context():
        doc = db.fetch_one("SELECT * FROM documents WHERE document_id = %s", (document_id,))
    assert doc["status"] == "In Progress"
    assert doc["assigned_to"] == form["employee_id"]

    # ---- forward (by the assigned employee) -------------------------------
    employee = logged_in("employee")
    token = form_token(employee)
    hr_department = lookup(
        flask_app, "SELECT department_id FROM departments WHERE department_name = 'Human Resources'", ()
    )
    forward = employee.post(
        f"/documents/{document_id}/forward",
        data={"to_department_id": hr_department["department_id"], "remarks": "Needs HR input.",
              "_csrf_token": token},
    )
    assert forward.status_code == 302
    with flask_app.app_context():
        doc = db.fetch_one("SELECT * FROM documents WHERE document_id = %s", (document_id,))
    assert doc["status"] == "Forwarded"
    assert doc["current_department_id"] == hr_department["department_id"]

    # ---- send for approval ------------------------------------------------
    manager = lookup(flask_app, "SELECT user_id FROM users WHERE email = 'manager@test.local'", ())
    token = form_token(officer)
    submit = officer.post(
        f"/documents/{document_id}/submit-approval",
        data={"approver_id": manager["user_id"], "remarks": "Awaiting sign-off.",
              "_csrf_token": token},
    )
    assert submit.status_code == 302
    with flask_app.app_context():
        doc = db.fetch_one("SELECT * FROM documents WHERE document_id = %s", (document_id,))
        approval = db.fetch_one(
            "SELECT * FROM approvals WHERE document_id = %s AND decision = 'pending'",
            (document_id,),
        )
    assert doc["status"] == "Pending"
    assert approval is not None

    # ---- rejection without remarks is refused -----------------------------
    manager_client = logged_in("manager")
    token = form_token(manager_client)
    refused = manager_client.post(
        f"/approvals/{approval['approval_id']}/decide",
        data={"decision": "rejected", "remarks": "   ", "_csrf_token": token},
        follow_redirects=True,
    )
    assert refused.status_code == 200
    assert b"must include remarks" in refused.data
    with flask_app.app_context():
        doc = db.fetch_one("SELECT status FROM documents WHERE document_id = %s", (document_id,))
    assert doc["status"] == "Pending"

    # ---- rejection with remarks ------------------------------------------
    token = form_token(manager_client)
    rejected = manager_client.post(
        f"/approvals/{approval['approval_id']}/decide",
        data={"decision": "rejected", "remarks": "Missing the supplier invoice.",
              "_csrf_token": token},
    )
    assert rejected.status_code == 302
    with flask_app.app_context():
        doc = db.fetch_one("SELECT * FROM documents WHERE document_id = %s", (document_id,))
        approval = db.fetch_one(
            "SELECT * FROM approvals WHERE approval_id = %s", (approval["approval_id"],)
        )
    assert doc["status"] == "Rejected"
    assert approval["decision"] == "rejected"
    assert approval["remarks"] == "Missing the supplier invoice."
    assert approval["decision_timestamp"] is not None

    # ---- close ------------------------------------------------------------
    token = form_token(officer)
    closed = officer.post(f"/documents/{document_id}/close", data={"_csrf_token": token})
    assert closed.status_code == 302
    with flask_app.app_context():
        assert db.fetch_one(
            "SELECT status FROM documents WHERE document_id = %s", (document_id,)
        )["status"] == "Closed"

    # ---- every step produced a tracking event -----------------------------
    actions = tracking_actions(flask_app, document_id)
    for expected in ("Document registered", "Document assigned", "Document forwarded",
                     "Sent for approval", "Document rejected", "Document closed"):
        assert expected in actions, (expected, actions)

    # ---- closed documents cannot be closed again --------------------------
    token = form_token(officer)
    assert officer.post(
        f"/documents/{document_id}/close", data={"_csrf_token": token}
    ).status_code == 403


def test_approval_success_and_archive(logged_in, flask_app, make_document, users):
    doc = make_document(title="Purchase approval", department="Finance", assigned_to="employee")
    approval_id = None
    with flask_app.app_context():
        with db.transaction() as cur:
            cur.execute(
                "UPDATE documents SET status = 'Pending' WHERE document_id = %s",
                (doc["document_id"],),
            )
            cur.execute(
                "INSERT INTO approvals (document_id, approver_id, decision) VALUES (%s, %s, 'pending')",
                (doc["document_id"], users["manager"]["user_id"]),
            )
            approval_id = cur.lastrowid

    manager = logged_in("manager")
    token = form_token(manager)
    response = manager.post(
        f"/approvals/{approval_id}/decide",
        data={"decision": "approved", "remarks": "Approved for purchase.", "_csrf_token": token},
    )
    assert response.status_code == 302
    with flask_app.app_context():
        assert db.fetch_one(
            "SELECT status FROM documents WHERE document_id = %s", (doc["document_id"],)
        )["status"] == "Approved"

    officer = logged_in("officer")
    token = form_token(officer)
    archive = officer.post(f"/documents/{doc['document_id']}/archive", data={"_csrf_token": token})
    assert archive.status_code == 302
    with flask_app.app_context():
        assert db.fetch_one(
            "SELECT archived FROM documents WHERE document_id = %s", (doc["document_id"],)
        )["archived"] == 1

    # Archived documents are read-only.
    token = form_token(officer)
    blocked = officer.post(
        f"/documents/{doc['document_id']}/remarks",
        data={"remarks": "Late note", "_csrf_token": token},
    )
    assert blocked.status_code == 403


def test_invalid_transitions_are_blocked(logged_in, make_document, flask_app, users):
    # A pending document cannot be closed.
    doc = make_document(title="Pending close", status="Pending")
    officer = logged_in("officer")
    token = form_token(officer)
    assert officer.post(f"/documents/{doc['document_id']}/close", data={"_csrf_token": token}).status_code == 403

    # A closed document cannot be sent for approval.
    with flask_app.app_context():
        db.execute("UPDATE documents SET status = 'Closed' WHERE document_id = %s", (doc["document_id"],))
    token = form_token(officer)
    response = officer.post(
        f"/documents/{doc['document_id']}/submit-approval",
        data={"approver_id": users["manager"]["user_id"], "_csrf_token": token},
    )
    assert response.status_code == 403


def test_failed_transaction_rolls_back(flask_app, make_document):
    make_document(title="Existing document")
    with flask_app.app_context():
        before = db.fetch_one("SELECT COUNT(*) AS total FROM documents")["total"]
        with pytest.raises(Exception):
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (document_number, title, document_type_id, submitted_by,
                         current_department_id, submission_date, priority, status)
                    VALUES ('DOC-9999-9999', 'Broken row', 1, 1, 1, CURDATE(), 'Normal', 'Submitted')
                    """
                )
                # Invalid foreign key: the whole block must roll back.
                cur.execute(
                    """
                    INSERT INTO documents
                        (document_number, title, document_type_id, submitted_by,
                         current_department_id, submission_date, priority, status)
                    VALUES ('DOC-9999-9998', 'Broken row 2', 999999, 1, 1, CURDATE(), 'Normal', 'Submitted')
                    """
                )
        after = db.fetch_one("SELECT COUNT(*) AS total FROM documents")["total"]
        broken = db.fetch_one("SELECT * FROM documents WHERE title LIKE 'Broken row%'")
    assert after == before
    assert broken is None


def test_attachment_type_is_validated(logged_in, flask_app, make_document):
    doc = make_document(title="Upload validation")
    officer = logged_in("officer")
    token = form_token(officer)
    response = officer.post(
        f"/documents/{doc['document_id']}/attachments",
        data={"attachment": (io.BytesIO(b"not allowed"), "script.sh"), "_csrf_token": token},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Allowed file types" in response.data
    with flask_app.app_context():
        assert db.fetch_one(
            "SELECT * FROM attachments WHERE document_id = %s", (doc["document_id"],)
        ) is None


def test_search_and_filter(flask_app, logged_in, make_document):
    make_document(title="Invoice for stationery", document_type="Invoice",
                  department="Finance", status="Submitted", priority="High")
    make_document(title="Annual audit report", document_type="Report",
                  department="Administration", status="Approved", priority="Low")

    officer = logged_in("officer")
    found = officer.get("/documents?q=stationery")
    assert b"Invoice for stationery" in found.data
    assert b"Annual audit report" not in found.data

    filtered = officer.get("/documents?status=Approved")
    assert b"Annual audit report" in filtered.data

    by_type = officer.get("/documents?document_type_id=1")
    assert by_type.status_code == 200

    empty = officer.get("/documents?q=no-such-document")
    assert b"No documents match your search" in empty.data


def test_reports_show_database_data(logged_in, make_document):
    make_document(title="Report source row", department="Finance", assigned_to="employee")
    client = logged_in("officer")

    page = client.get("/reports")
    assert page.status_code == 200
    assert b"Finance" in page.data
    assert b"Report source row" in page.data  # live rows from MySQL, not placeholder data

    export = client.get("/reports/export.csv")
    assert export.status_code == 200
    assert export.headers["Content-Type"].startswith("text/csv")
    assert b"Document Number" in export.data
    assert b"Report source row" in export.data
