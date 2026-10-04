import os
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

# 1. Load the embedding model
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

# 2. Connect to our schema vector store
vectorstore = Chroma(
    collection_name="schema_test_v2",
    persist_directory="data/chroma",
    embedding_function=embeddings
)

# 3. User's natural-language question
question = "Which students are participating in Mountain Climbing?"

# 4. Retrieve relevant schema
results = vectorstore.similarity_search(question, k=3)

schema = "\n\n".join(
    result.page_content for result in results
)

print("RETRIEVED SCHEMA:")
print(schema)

# 5. Send only retrieved schema to Gemini
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

prompt = f"""
You are a MySQL Text-to-SQL expert.

Generate a SQL query that answers the user's question.

Use ONLY the tables and columns provided in the retrieved schema.

Retrieved schema:
{schema}

User question:
{question}

Return ONLY the SQL query.
"""

response = llm.invoke(prompt)

print("\nGENERATED SQL:")
print(response.content)