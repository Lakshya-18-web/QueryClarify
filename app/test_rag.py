from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

text = "student table has StuID, Fname, LName, Age, Sex, Major, Advisor"

vector = embeddings.embed_query(text)

print("Embedding created")
print("Vector size:", len(vector))


