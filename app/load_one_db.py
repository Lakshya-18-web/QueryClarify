import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text

from app.admin_credentials import admin_settings, mysql_url

# ADMIN tool: creates the database and loads data, so it needs a privileged
# account. Credentials come from MYSQL_ADMIN_USER / MYSQL_ADMIN_PASSWORD
# (or the legacy MYSQL_USER / MYSQL_PASSWORD) in app/.env. Nothing is hard-coded.
db_name = "activity_1"
settings = admin_settings()
engine = create_engine(mysql_url(settings))
with engine.connect() as conn:
    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))
    conn.commit()
db_engine = create_engine(mysql_url(settings, db_name))

schema_path = f"data/spiderman/databases/{db_name}/schema.sql"

with open(schema_path, "r", encoding="utf-8") as f:
    schema = f.read()

statements = schema.split("\n\n")[1:]

with db_engine.connect() as conn:
    for stmt in statements:
        conn.execute(text(stmt))
    conn.commit()

print("activity_1 schema loaded successfully")

data_path = f"data/spiderman/databases/{db_name}/data"

for file in [
    "Activity.csv",
    "Student.csv",
    "Faculty.csv",
    "Participates_in.csv",
    "Faculty_Participates_in.csv"
]:
    if file.endswith(".csv"):
        table_name = file[:-4]
        df = pd.read_csv(f"{data_path}/{file}", dtype=str)

        df.to_sql(
            table_name,
            db_engine,
            if_exists="append",
            index=False
        )

print("activity_1 data loaded successfully")