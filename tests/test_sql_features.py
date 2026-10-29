"""Database objects: triggers, views, procedures, function, indexes, constraints."""

import pytest

import db


def test_registration_trigger_creates_audit_row(flask_app, make_document):
    doc = make_document(title="Trigger registration")
    with flask_app.app_context():
        rows = db.fetch_all(
            "SELECT action, actor_id FROM document_tracking WHERE document_id = %s",
            (doc["document_id"],),
        )
    assert len(rows) == 1
    assert rows[0]["action"] == "Document registered"
    assert rows[0]["actor_id"] == doc["submitted_by"]


def test_status_change_trigger_records_rich_action(flask_app, make_document):
    doc = make_document(title="Trigger status change")
    with flask_app.app_context():
        with db.transaction() as cur:
            db.set_tracking_context(cur, doc["submitted_by"], "Document forwarded",
                                    "Sent to HR for review")
            cur.execute(
                "UPDATE documents SET status = 'Forwarded' WHERE document_id = %s",
                (doc["document_id"],),
            )
            db.clear_tracking_context(cur)
        event = db.fetch_one(
            """SELECT action, remarks FROM document_tracking
                WHERE document_id = %s ORDER BY tracking_id DESC LIMIT 1""",
            (doc["document_id"],),
        )
    assert event["action"] == "Document forwarded"
    assert event["remarks"] == "Sent to HR for review"


def test_pending_days_function(flask_app, make_document):
    doc = make_document(title="Function pending days")
    with flask_app.app_context():
        # Backdate the audit trail so the document looks five days old.
        db.execute(
            """UPDATE document_tracking
                  SET action_timestamp = NOW() - INTERVAL 5 DAY
                WHERE document_id = %s""",
            (doc["document_id"],),
        )
        pending = db.fetch_one(
            "SELECT fn_pending_days(%s) AS days", (doc["document_id"],)
        )["days"]
        db.execute("UPDATE documents SET status = 'Closed' WHERE document_id = %s",
                   (doc["document_id"],))
        closed = db.fetch_one(
            "SELECT fn_pending_days(%s) AS days", (doc["document_id"],)
        )["days"]
    assert pending >= 5
    assert closed == 0


def test_views_return_real_information(flask_app, make_document):
    pending_doc = make_document(title="Still pending", status="Submitted")
    approved_doc = make_document(title="Already approved", status="Approved")

    with flask_app.app_context():
        pending_rows = db.fetch_all("SELECT * FROM v_pending_documents")
        pending_ids = {row["document_id"] for row in pending_rows}
        summary = db.fetch_all("SELECT * FROM v_department_status_summary")
        departments = db.fetch_one("SELECT COUNT(*) AS total FROM departments")["total"]

        finance = db.fetch_one(
            "SELECT * FROM v_department_status_summary WHERE department_name = 'Finance'"
        )

    assert pending_doc["document_id"] in pending_ids
    assert approved_doc["document_id"] not in pending_ids
    assert len(summary) == departments
    assert finance["total_documents"] >= 1
    assert "pending_days" in pending_rows[0]


def test_forward_procedure_updates_document(flask_app, make_document, users):
    doc = make_document(title="Procedure forward", status="In Progress", assigned_to="employee")
    with flask_app.app_context():
        hr_department = db.fetch_one(
            "SELECT department_id FROM departments WHERE department_name = 'Human Resources'"
        )["department_id"]
        with db.transaction() as cur:
            cur.callproc(
                "sp_forward_document",
                (doc["document_id"], hr_department, users["hr"]["user_id"],
                 users["officer"]["user_id"], "Forwarded by stored procedure"),
            )
        updated = db.fetch_one("SELECT * FROM documents WHERE document_id = %s", (doc["document_id"],))
        action = db.fetch_one(
            """SELECT action, remarks FROM document_tracking
                WHERE document_id = %s ORDER BY tracking_id DESC LIMIT 1""",
            (doc["document_id"],),
        )

    assert updated["status"] == "Forwarded"
    assert updated["assigned_to"] == users["hr"]["user_id"]
    assert updated["current_department_id"] == hr_department
    assert action["action"] == "Document forwarded"
    assert action["remarks"] == "Forwarded by stored procedure"


def test_rejection_requires_remarks_constraint(flask_app, make_document, users):
    doc = make_document(title="Reject constraint")
    with flask_app.app_context():
        with pytest.raises(Exception):
            with db.transaction() as cur:
                cur.execute(
                    """INSERT INTO approvals
                           (document_id, approver_id, decision, remarks, decision_timestamp)
                       VALUES (%s, %s, 'rejected', NULL, NOW())""",
                    (doc["document_id"], users["manager"]["user_id"]),
                )


def test_due_date_check_constraint(flask_app, make_document):
    with flask_app.app_context():
        doc = make_document(title="Date constraint")
        with pytest.raises(Exception):
            with db.transaction() as cur:
                cur.execute(
                    "UPDATE documents SET due_date = submission_date - INTERVAL 1 DAY WHERE document_id = %s",
                    (doc["document_id"],),
                )


def test_document_number_is_unique(flask_app, make_document):
    doc = make_document(title="Unique number")
    with flask_app.app_context():
        with pytest.raises(Exception):
            with db.transaction() as cur:
                cur.execute(
                    """INSERT INTO documents
                           (document_number, title, document_type_id, submitted_by,
                            submission_date, priority, status)
                       SELECT document_number, 'Duplicate number', document_type_id,
                              submitted_by, submission_date, priority, status
                         FROM documents WHERE document_id = %s""",
                    (doc["document_id"],),
                )


def test_useful_indexes_exist(flask_app):
    with flask_app.app_context():
        rows = db.fetch_all(
            """SELECT DISTINCT INDEX_NAME AS index_name
                 FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = 'documents'"""
        )
    indexes = {row["index_name"] for row in rows}
    for expected in (
        "idx_documents_status",
        "idx_documents_department_status",
        "idx_documents_submission_date",
        "idx_documents_assigned",
        "idx_documents_archived",
    ):
        assert expected in indexes, (expected, indexes)
