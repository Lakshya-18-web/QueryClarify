import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

password = os.getenv("MYSQL_PASSWORD")

engine = create_engine(
    f"mysql+pymysql://root:{password}@localhost:3306/activity_1"
)

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