from sqlalchemy import create_engine

password = "kritika"

engine = create_engine(
    f"mysql+pymysql://root:{password}@localhost:3306"
)

with engine.connect() as conn:
    print("MySQL connection successful")