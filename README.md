# DocTrack — Office Document Tracking System

DocTrack is a Flask web application for registering office documents and tracking their ownership, status, approvals, and history across departments. It provides role-based access for administrators, records officers, employees, and managers.

## Features

- Register documents with a type, department, assignee, priority, submission date, due date, description, and optional attachment.
- Search and filter documents by number or title, status, department, type, priority, and date; browse paginated results and archived records.
- Assign and forward documents between employees and departments, request manager approval, record approval or rejection decisions, close completed work, and archive records.
- View a per-document audit trail and searchable tracking history.
- Monitor workload, overdue items, pending work, and department summaries from the dashboard and reports; export document data as CSV.
- Manage users, departments, and document types through the administrator pages.
- Restrict document visibility and workflow actions according to the user's role and relationship to each document.

### Roles

| Role | Main capabilities |
| --- | --- |
| Administrator | Manage users, departments, and document types; register and manage documents; view reports; review approvals. |
| Records Officer | Register and manage documents, view reports, and export CSV data. |
| Employee | View documents available to them, manage their assignments, and perform permitted workflow actions. |
| Manager | View department and approval-related documents and decide approvals assigned to them. |

## Workflow

Documents generally move through `Submitted`, `In Progress`, and `Forwarded` as they are assigned or routed. A document can be sent for approval (`Pending`) and then approved or rejected. Approved and rejected documents may be closed; eligible documents may then be archived. Archived documents are read-only.

The application checks allowed transitions and permissions on the server. MySQL triggers record registration and status/archive changes in the audit trail; other workflow events are recorded by the application.

## Technology

- Python with Flask and Jinja templates
- MySQL 8.0+ (the schema notes testing with MySQL 8.0 / 8.4)
- `mysql-connector-python` for database access
- Bootstrap 5.3 and Bootstrap Icons, loaded from jsDelivr

## Requirements

- Python 3.8 or newer
- MySQL 8.0 or newer, with permission to create the application database, tables, triggers, functions, procedures, and views
- The MySQL command-line client for database setup

## Setup

Run these commands from the repository root in PowerShell.

1. Create and activate a virtual environment, then install dependencies:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Create the local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   Edit `.env` and set your MySQL connection values and a long, random `SECRET_KEY`. Keep `.env` private; it is excluded from Git. The example defaults to a local MySQL server and the database name `office_document_tracking`.

3. Initialize the database. From the repository root, open the MySQL client:

   ```powershell
   mysql -u root -p
   ```

   At the MySQL prompt, run the scripts in this order:

   ```sql
   SOURCE sql/schema.sql;
   SOURCE sql/advanced_features.sql;
   SOURCE sql/seed.sql;
   EXIT;
   ```

   `schema.sql` creates the database and core tables. **It drops and recreates existing application tables**, so do not rerun it on a database containing data you need to keep. `advanced_features.sql` creates the views, triggers, function, and stored procedures used by the application. `seed.sql` inserts example departments and document types; it does not create login accounts.

4. Create the first administrator account. Generate a password hash locally:

   ```powershell
   python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Initial admin password: ')))"
   ```

   Copy the generated hash, then connect to MySQL and insert the account (replace the example name, email, and hash):

   ```sql
   USE office_document_tracking;
   INSERT INTO users (full_name, email, password_hash, role, is_active)
   VALUES ('System Administrator', 'admin@example.com', 'PASTE_GENERATED_HASH_HERE', 'admin', TRUE);
   ```

   Use a strong password and keep it private. The application stores password hashes, not plaintext passwords.

5. Start the development server:

   ```powershell
   python app.py
   ```

   Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in with the administrator account created above.

## Configuration

The application reads `.env` from the project root. Available settings are documented in `.env.example`:

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Flask session signing key; set a unique random value. | Development fallback |
| `FLASK_DEBUG` | Enables Flask debug mode. | `1` |
| `MYSQL_HOST` | MySQL server hostname. | `localhost` in `.env.example` |
| `MYSQL_PORT` | MySQL server port. | `3306` |
| `MYSQL_USER` / `MYSQL_PASSWORD` | MySQL credentials. | `root` / empty |
| `MYSQL_DATABASE` | Application database name. | `office_document_tracking` |
| `SESSION_COOKIE_SECURE` | Set to `1` when serving over HTTPS. | `0` |
| `MAX_UPLOAD_SIZE` | Maximum request/upload size in bytes. | `5242880` (5 MiB) |

Attachments are stored locally under `uploads/`, not in MySQL. The application accepts PDF, PNG, JPG, JPEG, and DOCX extensions. Keep this directory private and back it up according to your organization's retention policy.

For production, use a production WSGI server and HTTPS, disable debug mode, set a strong secret key and secure session cookies, and configure database credentials and uploaded-file storage appropriately. Do not expose the Flask development server to the public internet.

## Project layout

```text
app.py                  Flask application factory and development entry point
auth.py                 Login, sessions, CSRF protection, and role checks
config.py               Environment-based application configuration
db.py                   MySQL connection, query, and transaction helpers
workflow.py             Document states, permissions, and workflow rules
routes/                 Dashboard, documents, approvals, tracking, reports, admin
templates/              Jinja pages and error screens
static/                 CSS and JavaScript assets
sql/schema.sql          Database and core table definitions
sql/advanced_features.sql
                        Database views, triggers, function, and procedures
sql/seed.sql            Example departments and document types
uploads/                Local attachment storage
```
