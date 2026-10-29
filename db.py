"""Small MySQL helper layer.

Every SQL statement in the application goes through the helpers below so that
all queries are parameterized and connections are reused per request.

``transaction()`` is the only place where multi-table writes are committed, so
a failure anywhere inside the block rolls everything back.
"""

from contextlib import contextmanager

import mysql.connector
from flask import current_app, g


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def _config_value(cfg, name):
    """Read a setting from a Flask config object or a plain settings object."""
    try:
        return cfg[name]
    except (TypeError, KeyError):
        return getattr(cfg, name)


def create_connection(config=None, database=None):
    """Open a new MySQL connection (used by this app and by the seed scripts)."""
    cfg = config or current_app.config
    return mysql.connector.connect(
        host=_config_value(cfg, "MYSQL_HOST"),
        port=_config_value(cfg, "MYSQL_PORT"),
        user=_config_value(cfg, "MYSQL_USER"),
        password=_config_value(cfg, "MYSQL_PASSWORD"),
        database=database if database is not None else _config_value(cfg, "MYSQL_DATABASE"),
        charset="utf8mb4",
        autocommit=False,
        use_pure=True,
        connection_timeout=10,
    )


def get_db():
    """One connection per request, reused by every query in that request."""
    if "db" not in g:
        g.db = create_connection()
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            if db.is_connected():
                db.rollback()  # discard anything not explicitly committed
                db.close()
        except mysql.connector.Error:
            pass


def init_app(app):
    app.teardown_appcontext(close_db)


# ---------------------------------------------------------------------------
# Query helpers (always parameterized - never build SQL with f-strings)
# ---------------------------------------------------------------------------
def fetch_all(sql, params=(), cursor=None):
    own_cursor = cursor is None
    cur = cursor or get_db().cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        if own_cursor:
            cur.close()


def fetch_one(sql, params=(), cursor=None):
    own_cursor = cursor is None
    cur = cursor or get_db().cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        if own_cursor:
            cur.close()


def execute(sql, params=(), cursor=None):
    """Run an INSERT/UPDATE/DELETE. Returns the new auto-increment id (or 0)."""
    own_cursor = cursor is None
    cur = cursor or get_db().cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        if own_cursor:
            get_db().commit()
        return cur.lastrowid or 0
    except Exception:
        if own_cursor:
            get_db().rollback()
        raise
    finally:
        if own_cursor:
            cur.close()


@contextmanager
def transaction():
    """All-or-nothing block used by every workflow operation."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Tracking context
# ---------------------------------------------------------------------------
# The trigger ``trg_documents_after_update`` writes the status-change audit row.
# These session variables let the application supply the friendly action name,
# the actor and the remarks. They are cleared immediately after the UPDATE.
def set_tracking_context(cur, actor_id, action, remarks=None):
    cur.execute(
        "SET @tracking_actor_id = %s, @tracking_action = %s, @tracking_remarks = %s",
        (actor_id, action, remarks),
    )


def clear_tracking_context(cur):
    cur.execute(
        "SET @tracking_actor_id = NULL, @tracking_action = NULL, @tracking_remarks = NULL"
    )


def add_tracking(cur, document_id, action, actor_id, remarks=None,
                 from_department_id=None, to_department_id=None,
                 from_user_id=None, to_user_id=None):
    """Insert a tracking row for events that do not change ``documents.status``."""
    cur.execute(
        """
        INSERT INTO document_tracking
            (document_id, action, from_department_id, to_department_id,
             from_user_id, to_user_id, actor_id, remarks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (document_id, action, from_department_id, to_department_id,
         from_user_id, to_user_id, actor_id, remarks),
    )


def generate_document_number(cur, document_id, year):
    """DOC-YYYY-0001 - derived from the auto-increment id, so it is unique."""
    cur.execute(
        """
        UPDATE documents
           SET document_number = CONCAT('DOC-', %s, '-', LPAD(%s, 4, '0'))
         WHERE document_id = %s
        """,
        (year, document_id, document_id),
    )
    cur.execute("SELECT document_number FROM documents WHERE document_id = %s", (document_id,))
    return cur.fetchone()["document_number"]
