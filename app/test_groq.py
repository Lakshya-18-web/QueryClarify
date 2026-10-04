import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv("app/.env")

api_key = os.getenv("GROQ_API_KEY")
model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

if not api_key:
    raise ValueError("GROQ_API_KEY not found in app/.env")

llm = ChatGroq(
    model=model,
    groq_api_key=api_key,
    temperature=0
)

response = llm.invoke(
    "Return exactly: GROQ_OK"
)

print(response.content)
