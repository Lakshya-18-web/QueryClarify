import pathlib
import sys

# Make `app` importable when run as `python app/<script>.py` from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from app.admin_credentials import mysql_url, read_only_settings

# Read-only: uses the SELECT-only account when MYSQL_RO_USER is set.
engine = create_engine(mysql_url(read_only_settings(), "activity_1"))

sql = """
SELECT
  T1.Fname,
  T1.LName
FROM student AS T1
INNER JOIN participates_in AS T2
  ON T1.StuID = T2.stuid
INNER JOIN activity AS T3
  ON T2.actid = T3.actid
WHERE
  T3.activity_name = 'Mountain Climbing';
"""

with engine.connect() as conn:
    result = conn.execute(text(sql))

    print("RESULT:")
    for row in result:
        print(row)