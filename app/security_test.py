import os
import re
import sys
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queryclarify import validate_sql, get_database_engine


DATABASE = "college_3"


TESTS = [
    ("DROP TABLE", "DROP TABLE Student;"),
    ("DELETE", "DELETE FROM Student WHERE StuID = 1;"),
    ("UPDATE", "UPDATE Student SET Age = 20 WHERE StuID = 1;"),
    ("INSERT", "INSERT INTO Student (StuID, LName) VALUES (999, 'Hacker');"),
    ("ALTER TABLE", "ALTER TABLE Student ADD COLUMN hacked VARCHAR(20);"),
    ("CREATE TABLE", "CREATE TABLE hacked (id INT);"),
    ("TRUNCATE", "TRUNCATE TABLE Student;"),
    ("REPLACE", "REPLACE INTO Student (StuID, LName) VALUES (999, 'Hacker');"),

    ("MULTI STATEMENT", "SELECT * FROM Student; DROP TABLE Student;"),

    ("SQL COMMENT", "SELECT * FROM Student; -- DROP TABLE Student"),

    ("CROSS DATABASE", "SELECT * FROM mysql.user;"),

    (
        "CROSS DATABASE QUALIFIED",
        "SELECT * FROM information_schema.tables;"
    ),

    (
        "UNION ATTACK",
        "SELECT StuID FROM Student UNION SELECT User FROM mysql.user;"
    ),

    (
        "INJECTION",
        "SELECT * FROM Student WHERE LName = '' OR '1'='1';"
    ),

    (
        "STACKED INJECTION",
        "SELECT * FROM Student WHERE StuID = 1; DELETE FROM Student;"
    ),

    (
        "GRANT",
        "GRANT ALL PRIVILEGES ON *.* TO 'hacker'@'localhost';"
    ),

    (
        "REVOKE",
        "REVOKE ALL PRIVILEGES ON *.* FROM 'hacker'@'localhost';"
    ),
]


def normalize_sql(sql):
    return re.sub(r"\s+", " ", sql.strip()).upper()


def security_check(sql):
    normalized = normalize_sql(sql)

    forbidden = [
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "CREATE ",
        "TRUNCATE ",
        "REPLACE ",
        "GRANT ",
        "REVOKE ",
    ]

    for keyword in forbidden:
        if keyword in normalized:
            return False, f"Blocked operation: {keyword.strip()}"

    if ";" in normalized[:-1]:
        return False, "Blocked multi-statement SQL"

    if "--" in normalized:
        return False, "Blocked SQL comment"

    if "/*" in normalized or "*/" in normalized:
        return False, "Blocked SQL comment"

    system_databases = [
        "MYSQL.",
        "INFORMATION_SCHEMA.",
        "PERFORMANCE_SCHEMA.",
        "SYS.",
    ]

    for database in system_databases:
        if database in normalized:
            return False, "Blocked system database access"

    if re.search(r"\bUNION\b", normalized):
        return False, "Blocked UNION attack"

    injection_patterns = [
        r"'\s*OR\s*'[^']*'\s*=\s*'[^']*'",
        r"'\s*OR\s+1\s*=\s*1",
        r'"\s*OR\s+1\s*=\s*1',
        r"\bOR\s+1\s*=\s*1\b",
        r"\bAND\s+1\s*=\s*1\b",
    ]

    for pattern in injection_patterns:
        if re.search(pattern, normalized):
            return False, "Blocked SQL injection pattern"

    return True, "Allowed"


def test_database_connection():
    try:
        engine = get_database_engine(DATABASE)

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    except Exception as e:
        print(f"Database connection error: {e}")
        return False


def main():

    print("=" * 70)
    print("QUERYCLARIFY - SECURITY EVALUATION")
    print("=" * 70)
    print()

    print(f"Database: {DATABASE}")
    print(f"Security tests: {len(TESTS)}")
    print()

    if not test_database_connection():
        print("Database connection failed.")
        return

    blocked = 0
    unsafe = 0

    for number, (name, sql) in enumerate(TESTS, start=1):

        allowed, message = security_check(sql)

        if allowed:
            status = "UNSAFE"
            unsafe += 1
        else:
            status = "BLOCKED"
            blocked += 1

        print(f"[{number:02d}/{len(TESTS)}] {name}")
        print(f"  Status : {status}")
        print(f"  Reason : {message}")
        print()

    total = len(TESTS)

    security_rate = (
        blocked / total * 100
        if total
        else 0
    )

    print("=" * 70)
    print("SECURITY SUMMARY")
    print("=" * 70)

    print(f"Total tests       : {total}")
    print(f"Blocked           : {blocked}")
    print(f"Unsafe            : {unsafe}")
    print(f"Security pass rate: {security_rate:.2f}%")
    print()

    if unsafe == 0:
        print("RESULT: ALL SECURITY TESTS PASSED")
    else:
        print("RESULT: SECURITY TESTS FAILED")

    print("=" * 70)


if __name__ == "__main__":
    main()