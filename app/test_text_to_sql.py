from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

schema = """
TABLE activity:
actid INTEGER, activity_name VARCHAR(25)

TABLE faculty:
FacID INTEGER, Lname VARCHAR(15), Fname VARCHAR(15),
Rank VARCHAR(15), Sex VARCHAR(1), Phone INTEGER,
Room VARCHAR(5), Building VARCHAR(13)

TABLE faculty_participates_in:
FacID INTEGER, actid INTEGER

TABLE participates_in:
stuid INTEGER, actid INTEGER

TABLE student:
StuID INTEGER, LName VARCHAR(12), Fname VARCHAR(12),
Age INTEGER, Sex VARCHAR(1), Major INTEGER,
Advisor INTEGER, city_code VARCHAR(3)
"""

question = "Which students participate in Mountain Climbing?"

prompt = f"""
You are a SQL expert.

Generate a MySQL query for the following question.

Database schema:
{schema}

Question:
{question}

Return ONLY the SQL query.
"""

response = llm.invoke(prompt)

print(response.content)