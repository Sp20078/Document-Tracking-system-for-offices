"""Document workflow rules.

Every permission check in the templates *and* in the routes uses these
functions, so the UI and the backend can never disagree.

Workflow (the only allowed status changes):

    Submitted ──assign──▶ In Progress ──forward──▶ Forwarded ──forward──▶ ...
        │                    │                          │
        └────────────── submit for approval ────────────┘
                             ▼
                          Pending
                        ╱        ╲
                  approve          reject (remarks required)
                     ▼                ▼
                  Approved          Rejected
                     ╲                ╱
                       close ────────┘
                             ▼
                          Closed  ──archive──▶ archived (read-only)

Any other status change is rejected by the backend.
"""

STATUSES = ["Submitted", "Pending", "In Progress", "Forwarded", "Approved", "Rejected", "Closed"]
PRIORITIES = ["Low", "Normal", "High", "Urgent"]

#: Statuses that still need somebody to act on the document.
OPEN_STATUSES = {"Submitted", "Pending", "In Progress", "Forwarded"}

#: Statuses allowed to be closed.
CLOSABLE_STATUSES = {"Approved", "Rejected"}

#: Statuses allowed to be archived.
ARCHIVABLE_STATUSES = {"Approved", "Rejected", "Closed"}

#: Statuses from which a document may be (re)assigned.
ASSIGNABLE_STATUSES = {"Submitted", "In Progress", "Forwarded", "Rejected"}

#: Statuses from which a document may be forwarded or sent for approval.
TRANSFERABLE_STATUSES = {"Submitted", "In Progress", "Forwarded"}

STATUS_BADGES = {
    "Submitted": "badge-soft-gray",
    "Pending": "badge-soft-yellow",
    "In Progress": "badge-soft-blue",
    "Forwarded": "badge-soft-purple",
    "Approved": "badge-soft-green",
    "Rejected": "badge-soft-red",
    "Closed": "badge-soft-dark",
}

PRIORITY_BADGES = {
    "Low": "badge-soft-gray",
    "Normal": "badge-soft-blue",
    "High": "badge-soft-yellow",
    "Urgent": "badge-soft-red",
}


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------
def is_admin(user):
    return bool(user) and user["role"] == "admin"


def is_officer(user):
    return bool(user) and user["role"] == "records_officer"


def can_manage_records(user):
    """Administrators and records officers share the document back office."""
    return bool(user) and user["role"] in {"admin", "records_officer"}


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def can_view_document(user, document, has_approval=False):
    """Who may read a document, its attachments and its tracking history."""
    if not user or not document:
        return False
    if can_manage_records(user):
        return True
    if document["submitted_by"] == user["user_id"]:
        return True
    if document["assigned_to"] == user["user_id"]:
        return True
    if document["current_department_id"] == user["department_id"]:
        return True
    # A manager can see a document that was sent to them for a decision.
    return user["role"] == "manager" and has_approval


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def can_assign(user, document):
    return (
        can_manage_records(user)
        and not document["archived"]
        and document["status"] in ASSIGNABLE_STATUSES
    )


def can_forward(user, document):
    if not user or document["archived"] or document["status"] not in TRANSFERABLE_STATUSES:
        return False
    if can_manage_records(user):
        return True
    return user["role"] == "employee" and document["assigned_to"] == user["user_id"]


def can_submit_for_approval(user, document):
    if not user or document["archived"] or document["status"] not in TRANSFERABLE_STATUSES:
        return False
    if can_manage_records(user):
        return True
    return document["assigned_to"] == user["user_id"]


def can_decide_approval(user, document, approval):
    """A pending approval may only be decided by its approver, and never by the
    person who submitted or currently holds the document."""
    if not user or not document or not approval:
        return False
    if approval["decision"] != "pending" or document["status"] != "Pending" or document["archived"]:
        return False
    if user["role"] not in {"admin", "manager"}:
        return False
    if approval["approver_id"] != user["user_id"]:
        return False
    return user["user_id"] not in {document["submitted_by"], document["assigned_to"]}


def can_close(user, document):
    return (
        can_manage_records(user)
        and not document["archived"]
        and document["status"] in CLOSABLE_STATUSES
    )


def can_archive(user, document):
    return (
        can_manage_records(user)
        and not document["archived"]
        and document["status"] in ARCHIVABLE_STATUSES
    )


def can_add_remarks(user, document):
    return can_view_document(user, document) and not document["archived"]


def can_attach(user, document):
    return can_view_document(user, document) and not document["archived"]


def available_actions(user, document, approval=None):
    """Used by templates to decide which buttons to render."""
    return {
        "assign": can_assign(user, document),
        "forward": can_forward(user, document),
        "submit_for_approval": can_submit_for_approval(user, document),
        "decide": can_decide_approval(user, document, approval),
        "close": can_close(user, document),
        "archive": can_archive(user, document),
        "remarks": can_add_remarks(user, document),
        "attach": can_attach(user, document),
    }


def is_valid_transition(current, new):
    """Guard used before any status write (the DB trigger audits the change)."""
    if current == new:
        return True
    if current == "Submitted":
        return new in {"In Progress", "Forwarded", "Pending"}
    if current == "In Progress":
        return new in {"Forwarded", "Pending", "Submitted"}
    if current == "Forwarded":
        return new in {"In Progress", "Forwarded", "Pending"}
    if current == "Pending":
        return new in {"Approved", "Rejected"}
    if current == "Approved":
        return new in {"Closed"}
    if current == "Rejected":
        return new in {"Closed", "In Progress"}
    if current == "Closed":
        return False
    return False
