"""Office Document Tracking System - application entry point.

Run locally with:
    python app.py
"""

import logging
import os

from flask import Flask, render_template

import db
from auth import auth_bp, init_csrf, load_logged_in_user
from config import Config
from routes.admin import admin_bp
from routes.approvals import approvals_bp
from routes.common import register_template_helpers
from routes.dashboard import dashboard_bp
from routes.documents import documents_bp
from routes.reports import reports_bp
from routes.tracking import tracking_bp

log = logging.getLogger(__name__)


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    init_csrf(app)
    app.before_request(load_logged_in_user)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(tracking_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    register_template_helpers(app)

    register_error_handlers(app)
    return app


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("errors/400.html", message=getattr(error, "description", None)), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        # Technical details go to the console only, never to the browser.
        log.exception("Unhandled server error: %s", error)
        return render_template("errors/500.html"), 500


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])
