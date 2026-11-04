from dotenv import load_dotenv

# IMPORTANT: Load environment variables from .env file first.
# This ensures that the configuration values required by create_app()
# (like API keys or SECRET_KEY) are present in the environment
# before the Flask application instance is initialized.
load_dotenv()

# Import the application factory function
from flightapp.app import create_app

# The 'application' variable is the standard WSGI callable name Gunicorn looks for.
# Gunicorn will call this variable to get the Flask application instance.
application = create_app()

# We do not include the if __name__ == "__main__" block here, 
# as this file is only designed to be loaded by Gunicorn.
