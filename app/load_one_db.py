import pandas as pd
import os
from sqlalchemy import create_engine, text

password = "kritika"

db_name = "activity_1"

base_url = f"mysql+pymysql://root:{password}@localhost:3306"
engine = create_engine(base_url)

with engine.connect() as conn:
    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))
    conn.commit()

db_engine = create_engine(f"{base_url}/{db_name}")

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