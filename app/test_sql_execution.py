import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from app.admin_credentials import mysql_url, read_only_settings

# Read-only: uses the SELECT-only account when MYSQL_RO_USER is set.
engine = create_engine(mysql_url(read_only_settings(), "activity_1"))

sql = """
SELECT s.Fname, s.LName
FROM student AS s
JOIN participates_in AS pi
    ON s.StuID = pi.stuid
JOIN activity AS a
    ON pi.actid = a.actid
WHERE a.activity_name = 'Mountain Climbing';
"""

with engine.connect() as conn:
    result = conn.execute(text(sql))

    for row in result:
        print(row)