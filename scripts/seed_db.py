"""Create development accounts and a small set of demonstration documents.

Usage (after ``python scripts/init_db.py``):

    python scripts/seed_db.py

All accounts below use a documented development password. Never reuse these
credentials in a real deployment. The script is safe to re-run: accounts are
updated in place and demo documents are only created when the table is empty.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from werkzeug.security import generate_password_hash  # noqa: E402

from config import Config  # noqa: E402
import db  # noqa: E402

# This is a development-only password shared by the demo accounts.
DEV_PASSWORD = "Password@123"

ACCOUNTS = [
    ("System Administrator", "admin@doctrack.local", "admin", "Administration"),
    ("Rita Sharma", "officer@doctrack.local", "records_officer", "Administration"),
    ("Meera Nair", "manager@doctrack.local", "manager", "Finance"),
    ("Arjun Patel", "employee@doctrack.local", "employee", "Finance"),
    ("Priya Das", "hr@doctrack.local", "employee", "Human Resources"),
]

DEMO_DOCUMENTS = [
    {
        "title": "Annual Budget Allocation 2026",
        "description": "Department-wise budget proposal for the next financial year.",
        "type": "Report",
        "department": "Finance",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": "employee@doctrack.local",
        "priority": "High",
        "days_ago": 9,
        "due_in": 5,
        "status": "In Progress",
    },
    {
        "title": "Lab Equipment Purchase Request",
        "description": "Request to purchase ten new desktop machines for the computer lab.",
        "type": "Purchase Request",
        "department": "IT Services",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": None,
        "priority": "Urgent",
        "days_ago": 4,
        "due_in": 2,
        "status": "Submitted",
    },
    {
        "title": "Staff Recruitment Approval - Lab Assistant",
        "description": "Approval sought to fill the vacant lab assistant position.",
        "type": "Application",
        "department": "Human Resources",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": "hr@doctrack.local",
        "priority": "Normal",
        "days_ago": 7,
        "due_in": None,
        "status": "Forwarded",
    },
    {
        "title": "Vendor Invoice - Stationery Supplies",
        "description": "Invoice from the approved stationery vendor for Q3 supplies.",
        "type": "Invoice",
        "department": "Finance",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": "employee@doctrack.local",
        "priority": "Normal",
        "days_ago": 3,
        "due_in": 6,
        "status": "Pending",
        "approver": "manager@doctrack.local",
    },
    {
        "title": "Semester Examination Schedule",
        "description": "Draft examination timetable for the autumn semester.",
        "type": "Report",
        "department": "Academic Office",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": "employee@doctrack.local",
        "priority": "Low",
        "days_ago": 15,
        "due_in": -2,
        "status": "Approved",
        "approver": "manager@doctrack.local",
    },
    {
        "title": "Outdated Software Licence Renewal",
        "description": "Renewal request for software that is no longer in use.",
        "type": "Purchase Request",
        "department": "IT Services",
        "submitted_by": "officer@doctrack.local",
        "assigned_to": "employee@doctrack.local",
        "priority": "Low",
        "days_ago": 21,
        "due_in": -10,
        "status": "Rejected",
        "approver": "manager@doctrack.local",
        "remarks": "The software is being replaced, so renewal is not required.",
    },
    {
        "title": "Official Circular - Office Timings",
        "description": "Circular announcing revised office timings for the new term.",
        "type": "Official Letter",
        "department": "Administration",
        "submitted_by": "admin@doctrack.local",
        "assigned_to": "hr@doctrack.local",
        "priority": "Normal",
        "days_ago": 30,
        "due_in": -20,
        "status": "Closed",
    },
]


def lookup(cur, table, id_column, name_column, name):
    cur.execute(f"SELECT {id_column} AS id FROM {table} WHERE {name_column} = %s", (name,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"Missing {table} row: {name!r}. Run sql/seed.sql first.")
    return row["id"]


def set_tracking(cur, actor_id, action, remarks=None):
    cur.execute(
        "SET @tracking_actor_id = %s, @tracking_action = %s, @tracking_remarks = %s",
        (actor_id, action, remarks),
    )


def clear_tracking(cur):
    cur.execute("SET @tracking_actor_id = NULL, @tracking_action = NULL, @tracking_remarks = NULL")


def main():
    connection = db.create_connection(Config)
    cur = connection.cursor(dictionary=True)

    # ---- accounts ---------------------------------------------------------
    password_hash = generate_password_hash(DEV_PASSWORD)
    user_ids = {}
    for full_name, email, role, department in ACCOUNTS:
        department_id = lookup(cur, "departments", "department_id", "department_name", department)
        cur.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, department_id, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON DUPLICATE KEY UPDATE
                full_name = VALUES(full_name),
                password_hash = VALUES(password_hash),
                role = VALUES(role),
                department_id = VALUES(department_id),
                is_active = TRUE
            """,
            (full_name, email, password_hash, role, department_id),
        )
        user_ids[email] = lookup(cur, "users", "user_id", "email", email)
    connection.commit()
    print(f"Created/updated {len(ACCOUNTS)} development accounts (password: {DEV_PASSWORD}).")

    # ---- demo documents ---------------------------------------------------
    cur.execute("SELECT COUNT(*) AS total FROM documents")
    if cur.fetchone()["total"] > 0:
        print("Documents already exist - skipping demo documents.")
        connection.commit()
        cur.close()
        connection.close()
        return

    today = date.today()
    for spec in DEMO_DOCUMENTS:
        type_id = lookup(cur, "document_types", "document_type_id", "type_name", spec["type"])
        department_id = lookup(cur, "departments", "department_id", "department_name", spec["department"])
        submitter_id = user_ids[spec["submitted_by"]]
        assignee_id = user_ids[spec["assigned_to"]] if spec["assigned_to"] else None
        submission_date = today - timedelta(days=spec["days_ago"])
        due_date = today + timedelta(days=spec["due_in"]) if spec["due_in"] is not None else None

        cur.execute(
            "CALL sp_register_document(%s, %s, %s, %s, %s, %s, %s, %s, %s, @new_document_id)",
            (spec["title"], spec["description"], type_id, submitter_id, department_id,
             assignee_id, submission_date, due_date, spec["priority"]),
        )
        cur.execute("SELECT @new_document_id AS document_id")
        document_id = cur.fetchone()["document_id"]

        status = spec["status"]
        if assignee_id and status in {"In Progress", "Forwarded", "Pending", "Approved",
                                     "Rejected", "Closed"}:
            if status == "Forwarded":
                set_tracking(cur, submitter_id, "Document forwarded",
                             "Forwarded to another department for review.")
                new_status = "Forwarded"
            else:
                set_tracking(cur, submitter_id, "Document assigned",
                             "Assigned to the responsible employee.")
                new_status = "In Progress"
            cur.execute(
                "UPDATE documents SET assigned_to = %s, status = %s WHERE document_id = %s",
                (assignee_id, new_status, document_id),
            )
            clear_tracking(cur)

        if status in {"Pending", "Approved", "Rejected"}:
            approver_id = user_ids[spec["approver"]]
            set_tracking(cur, submitter_id, "Sent for approval",
                         "Submitted to the department manager for a decision.")
            cur.execute(
                "UPDATE documents SET status = 'Pending' WHERE document_id = %s", (document_id,)
            )
            clear_tracking(cur)
            cur.execute(
                "INSERT INTO approvals (document_id, approver_id, decision) VALUES (%s, %s, 'pending')",
                (document_id, approver_id),
            )

            if status in {"Approved", "Rejected"}:
                decision = "approved" if status == "Approved" else "rejected"
                remarks = spec.get("remarks") or "Approved after review."
                cur.execute(
                    """
                    INSERT INTO approvals (document_id, approver_id, decision, remarks,
                                           decision_timestamp)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (document_id, approver_id, decision, remarks),
                )
                # Remove the placeholder pending row created above.
                cur.execute(
                    """DELETE FROM approvals
                        WHERE document_id = %s AND decision = 'pending'""",
                    (document_id,),
                )
                action = "Document approved" if decision == "approved" else "Document rejected"
                set_tracking(cur, approver_id, action, remarks)
                cur.execute(
                    "UPDATE documents SET status = %s WHERE document_id = %s", (status, document_id)
                )
                clear_tracking(cur)

        if status == "Closed":
            set_tracking(cur, user_ids["officer@doctrack.local"], "Document closed",
                         "Work completed and filed.")
            cur.execute(
                "UPDATE documents SET status = 'Closed' WHERE document_id = %s", (document_id,)
            )
            clear_tracking(cur)

        connection.commit()

    print(f"Created {len(DEMO_DOCUMENTS)} demo documents with tracking history.")
    print("\nDevelopment logins (password for all: %s):" % DEV_PASSWORD)
    for _, email, role, department in ACCOUNTS:
        print(f"  {email:<28} {role:<16} {department}")

    cur.close()
    connection.close()


if __name__ == "__main__":
    main()
