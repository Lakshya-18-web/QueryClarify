import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect

from app.admin_credentials import mysql_url, read_only_settings

# Read-only diagnostic: uses the SELECT-only account when MYSQL_RO_USER is set.
engine = create_engine(mysql_url(read_only_settings()))
inspector = inspect(engine)

dbs = [
    "world_1",
    "flight_2",
    "cre_Doc_Template_Mgt",
    "dog_kennels",
    "student_transcripts_tracking",
    "wta_1",
    "tvshow",
    "pets_1",
    "poker_player",
    "concert_singer",
    "employee_hire_evaluation",
    "singer",
    "network_1",
    "course_teach",
    "orchestra",
    "museum_visit",
    "voter_1",
    "real_estate_properties",
]

for db in dbs:
    try:
        print(f"\n{db}:")
        print(inspector.get_table_names(schema=db))
    except Exception as e:
        print(f"ERROR: {e}")
