from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

documents = [
    """
TABLE student
Stores information about students.
Students can participate in activities through the participates_in table.
Use this table when asking about student names, student details,
or which students participate in an activity.
Columns: StuID, LName, Fname, Age, Sex, Major, Advisor, city_code.
Primary key: StuID.
""",

    """
TABLE activity
Stores student activities.
Contains activity names such as Mountain Climbing.
Use this table when asking about activities or finding
students participating in a specific activity.
Columns: actid, activity_name.
Primary key: actid.
""",

    """
    TABLE participates_in
    Connects students with activities.
    Columns: stuid, actid.
    stuid references student.StuID.
    actid references activity.actid.
    Use this table when asking which students participate in activities.
    """,

    """
    TABLE faculty
    Stores faculty information.
    Columns: FacID, Lname, Fname, Rank, Sex, Phone, Room, Building.
    Primary key: FacID.
    """,

    """
    TABLE faculty_participates_in
    Connects faculty members with activities.
    Columns: FacID, actid.
    FacID references faculty.FacID.
    actid references activity.actid.
    Use this table for faculty participation in activities.
    """
]

vectorstore = Chroma.from_texts(
    texts=documents,
    embedding=embeddings,
    collection_name="schema_test_v2",
    persist_directory="data/chroma"
)

results = vectorstore.similarity_search(
    "Which students are participating in Mountain Climbing?",
    k=3
)

for result in results:
    print(result.page_content)