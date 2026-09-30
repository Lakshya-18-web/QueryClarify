import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

load_dotenv()

password = os.getenv("MYSQL_PASSWORD")

db_name = "activity_1"

engine = create_engine(
    f"mysql+pymysql://root:{password}@localhost:3306/{db_name}"
)

inspector = inspect(engine)

tables = inspector.get_table_names()

for table in tables:
    print(f"\nTABLE: {table}")

    columns = inspector.get_columns(table)

    for column in columns:
        print(f"  {column['name']} : {column['type']}")