import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()


class ClarificationResult(BaseModel):
    clear: bool = Field(
        description="True if the question is specific enough to generate SQL."
    )
    clarification_question: str = Field(
        description="One concise clarification question. Empty if clear."
    )


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

structured_llm = llm.with_structured_output(ClarificationResult)

question = "Which students participate in Mountain Climbing?"

prompt = f"""
You are a query-understanding assistant for a Text-to-SQL system.

Determine whether this question is specific enough to generate
a correct SQL query.

If it is ambiguous:
- clear = false
- ask ONE concise clarification question.

If it is clear:
- clear = true
- clarification_question = ""

User question:
{question}
"""

result = structured_llm.invoke(prompt)

print("Clear:", result.clear)
print("Clarification:", result.clarification_question)