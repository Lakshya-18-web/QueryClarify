
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "spiderman"

train_path = DATA_DIR / "train_queries.csv"
test_path = DATA_DIR / "test_queries.csv"
database_path = DATA_DIR / "databases"

train_df = pd.read_csv(train_path)
test_df = pd.read_csv(test_path)

databases = [x for x in database_path.iterdir() if x.is_dir()]

print("=" * 50)
print("          SPIDERMAN DATASET INSPECTOR")
print("=" * 50)

print(f"\nNumber of databases : {len(databases)}")
print(f"Training questions  : {len(train_df)}")
print(f"Test questions      : {len(test_df)}")

print("\nTrain columns:")
print(train_df.columns.tolist())

print("\nTest columns:")
print(test_df.columns.tolist())

print("\nSample training question:")
print(train_df.iloc[0])

print("\nSample test question:")
print(test_df.iloc[0])

print("\nDatabase examples:")
for db in databases[:10]:
    print(db.name)

print("\n" + "=" * 50)
print("DATASET SUMMARY")
print("=" * 50)

print(f"Total databases : {len(databases)}")
print(f"Train questions : {len(train_df)}")
print(f"Test questions  : {len(test_df)}")

print("\nTrain database distribution:")
print(train_df["database"].nunique())

print("\nTest database distribution:")
print(test_df["database"].nunique())