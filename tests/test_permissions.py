"""Role-based authorization and document visibility."""

from tests.conftest import form_token


def test_unauthenticated_requests_are_redirected(client):
    for path in ("/", "/documents", "/reports", "/admin/users", "/tracking"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path


def test_employee_cannot_open_admin_or_report_pages(logged_in):
    client = logged_in("employee")
    for path in ("/admin/users", "/admin/departments", "/admin/document-types",
                 "/reports", "/documents/register"):
        assert client.get(path).status_code == 403, path


def test_manager_cannot_open_admin_pages_but_can_review(logged_in):
    client = logged_in("manager")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/approvals").status_code == 200
    assert client.get("/reports").status_code == 403


def test_admin_can_open_administration_pages(logged_in):
    client = logged_in("admin")
    assert client.get("/admin/users").status_code == 200
    assert client.get("/admin/departments").status_code == 200
    assert client.get("/admin/document-types").status_code == 200
    assert client.get("/reports").status_code == 200


def test_document_is_hidden_from_unrelated_employee(logged_in, make_document, users):
    doc = make_document(title="HR confidential note", department="Human Resources",
                        assigned_to="hr")

    outsider = logged_in("employee")  # Finance, not involved in the document
    assert outsider.get(f"/documents/{doc['document_id']}").status_code == 403

    involved = logged_in("hr")
    assert involved.get(f"/documents/{doc['document_id']}").status_code == 200

    officer = logged_in("officer")  # records officers manage all documents
    assert officer.get(f"/documents/{doc['document_id']}").status_code == 200


def test_hidden_document_is_absent_from_list_and_search(logged_in, make_document):
    make_document(title="HR confidential note", department="Human Resources", assigned_to="hr")
    client = logged_in("employee")
    listing = client.get("/documents?q=confidential")
    assert listing.status_code == 200
    assert b"HR confidential note" not in listing.data


def test_employee_cannot_assign_documents(logged_in, make_document, users):
    doc = make_document(title="Assign attempt", assigned_to="employee")
    client = logged_in("employee")
    token = form_token(client)
    response = client.post(
        f"/documents/{doc['document_id']}/assign",
        data={"assigned_to": users["manager"]["user_id"], "_csrf_token": token},
    )
    assert response.status_code == 403


def test_manager_cannot_register_documents(logged_in):
    client = logged_in("manager")
    token = form_token(client)
    response = client.post(
        "/documents/register",
        data={"title": "Should not exist", "_csrf_token": token},
    )
    assert response.status_code == 403


def test_attachment_download_requires_document_access(logged_in, make_document, flask_app, users):
    import io

    officer = logged_in("officer")
    doc = make_document(title="Attachment access", department="Finance", assigned_to="employee")
    token = form_token(officer)
    upload = officer.post(
        f"/documents/{doc['document_id']}/attachments",
        data={
            "attachment": (io.BytesIO(b"%PDF-1.4 test"), "policy.pdf"),
            "_csrf_token": token,
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 302

    with flask_app.app_context():
        import db
        row = db.fetch_one(
            "SELECT attachment_id FROM attachments WHERE document_id = %s",
            (doc["document_id"],),
        )
    assert row

    # The HR employee has nothing to do with this Finance document.
    outsider = logged_in("hr")
    assert outsider.get(f"/attachments/{row['attachment_id']}/download").status_code == 403

    allowed = logged_in("employee")
    download = allowed.get(f"/attachments/{row['attachment_id']}/download")
    assert download.status_code == 200
    assert download.data.startswith(b"%PDF")
