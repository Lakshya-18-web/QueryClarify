import os

from dotenv import load_dotenv


load_dotenv()


APP_NAME = "QueryClarify"

APP_VERSION = "1.0.0"

MYSQL_HOST = os.getenv(
    "MYSQL_HOST",
    "localhost"
)

MYSQL_PORT = os.getenv(
    "MYSQL_PORT",
    "3306"
)

LANGSMITH_PROJECT = os.getenv(
    "LANGSMITH_PROJECT",
    "QueryClarify"
)