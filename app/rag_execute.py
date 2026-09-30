import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

password = os.getenv("MYSQL_PASSWORD")

engine = create_engine(
    f"mysql+pymysql://root:{password}@localhost:3306/activity_1"
)

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