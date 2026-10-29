"""Application configuration.

All values come from environment variables (see ``.env.example``).
Nothing secret is hard-coded: the fallbacks below only exist so that a fresh
development checkout can start and show a helpful error instead of crashing.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Read .env from the project root (if it exists).
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Flask -------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    DEBUG = _env_bool("FLASK_DEBUG", True)

    # --- MySQL -------------------------------------------------------------
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "office_document_tracking")

    # --- Uploads -----------------------------------------------------------
    UPLOAD_FOLDER = str(BASE_DIR / "uploads")
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "docx"}
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_SIZE", str(5 * 1024 * 1024)))

    # --- Session -----------------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours
    JSON_SORT_KEYS = False

    ITEMS_PER_PAGE = 10
