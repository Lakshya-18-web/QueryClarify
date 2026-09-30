from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from dotenv import load_dotenv
import os

load_dotenv("app/.env")

url = URL.create(
    "mysql+pymysql",
    username="root",
    password=os.getenv("MYSQL_PASSWORD"),
    host="localhost",
    port=3306
)

engine = create_engine(url)
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
