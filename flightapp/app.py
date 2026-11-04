import os
import sys
from flask import Flask
from dotenv import load_dotenv

# Load .env locally (does nothing on Cloud Run)
load_dotenv()

# Import models AFTER env vars are available
from .models import init_db, utc_to_est


def create_app():
    app = Flask(__name__)

    # --- SECRET KEY ---
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

    # --- DEBUG ENV CHECK (prints to Cloud Run logs) ---
    print("---- ENVIRONMENT CHECK ----")
    print("FLASK_SECRET_KEY:", os.getenv("FLASK_SECRET_KEY"))
    print("OPENSKY_CLIENT_ID:", os.getenv("OPENSKY_CLIENT_ID"))
    print("OPENSKY_AUTH_MODE:", os.getenv("OPENSKY_AUTH_MODE"))
    print("DEFAULT_AIRPORT:", os.getenv("DEFAULT_AIRPORT"))
    print("---------------------------")

    try:
        # --- DATABASE INIT ---
        print("APP STARTUP: Initializing database...")
        init_db()
        print("APP STARTUP: Database initialized successfully.")

        # --- JINJA FILTERS ---
        app.jinja_env.filters["utc_to_est"] = utc_to_est

        # --- BLUEPRINTS ---
        from .routes import bp
        app.register_blueprint(bp)

        print("APP STARTUP: Blueprint registered.")
        print("APP STARTUP: Application ready.")

    except Exception as e:
        print(
            f"FATAL ERROR DURING APP STARTUP: {type(e).__name__}: {e}",
            file=sys.stderr
        )
        raise

    return app