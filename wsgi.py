"""WSGI entry point for development and Gunicorn deployment."""

from dotenv import load_dotenv

load_dotenv()

import os
from datetime import datetime, timezone

from app import auto_seed_database, create_app
from app.extensions import db


def initialize_app(flask_app):
    """Initialize runtime dependencies that must run on every startup."""

    if flask_app.extensions.get("startup_initialized"):
        return

    with flask_app.app_context():
        try:
            db.create_all()
            flask_app.logger.info("Database tables created/verified.")
        except Exception as exc:  # pylint: disable=broad-except
            flask_app.logger.error(
                "Database not available at startup. Check DATABASE_URL. Details: %s", exc
            )
            raise RuntimeError(
                "Database not available at startup. Check DATABASE_URL."
            ) from exc

        upload_folder = flask_app.config.get("UPLOAD_FOLDER", "uploads")
        upload_dirs = [
            os.path.join(upload_folder, "avatars"),
            os.path.join(upload_folder, "outputs"),
            os.path.join(upload_folder, "knowledge"),
        ]
        for upload_dir in upload_dirs:
            os.makedirs(upload_dir, exist_ok=True)
        flask_app.logger.info("Upload directories verified/created.")

        auto_seed_database()
        flask_app.logger.info("Seed data verified/inserted.")

        redis_client = flask_app.extensions.get("redis_client")
        if redis_client is not None:
            try:
                redis_client.set("app:start_time", datetime.now(timezone.utc).isoformat())
            except Exception:  # pylint: disable=broad-except
                pass

        flask_app.logger.info(
            "AgentFlow platform ready. Environment: %s",
            flask_app.config.get("FLASK_ENV", os.environ.get("FLASK_ENV", "development")),
        )

    flask_app.extensions["startup_initialized"] = True


app = create_app(os.environ.get("FLASK_ENV", "production"))
initialize_app(app)


if __name__ == "__main__":
    initialize_app(app)
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
