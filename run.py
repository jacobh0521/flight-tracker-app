import os
import sys

# Keep your path logic
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()  
print(f"DEBUG: OpenSky ID present: {bool(os.getenv('OPENSKY_CLIENT_ID'))}")
print(f"DEBUG: AviationStack Key present: {bool(os.getenv('AVIATIONSTACK_KEY'))}")

from flightapp.app import create_app

# --- FIX 1: Move app creation OUTSIDE the if block ---
# This allows the production server to "see" your website.
app = create_app()

if __name__ == "__main__":
    # --- FIX 2: Dynamic Port ---
    # Cloud Run assigns a port. If we don't find it, we use 8080.
    port = int(os.environ.get("PORT", 8080))
    
    # Listen on 0.0.0.0 (required for Cloud Run)
    app.run(host="0.0.0.0", port=port, debug=True)