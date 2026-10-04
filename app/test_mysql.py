import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine

from app.admin_credentials import mysql_url, read_only_settings

# Connectivity check only (read-only account preferred). Credentials come from
# the environment (app/.env); nothing is hard-coded and nothing is printed.
settings = read_only_settings()
engine = create_engine(mysql_url(settings))
with engine.connect() as conn:
    print(f"MySQL connection successful (account source: {settings.source})")
