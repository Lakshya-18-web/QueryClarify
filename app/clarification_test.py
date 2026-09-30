import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

question = "Which students participate in Mountain Climbing?"

prompt = f"""
You are a query-understanding assistant for a Text-to-SQL system.

Determine whether the user's question is sufficiently specific
to generate a correct SQL query.

User question:
{question}

If the question is ambiguous, identify what information is missing
and ask ONE concise clarification question.

If the question is clear, respond exactly:
CLEAR

Do not generate SQL.
"""

response = llm.invoke(prompt)

print(response.content)