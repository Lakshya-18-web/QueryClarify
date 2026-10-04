from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

FILES = [
    APP / "queryclarify.py",
    APP / "evaluate.py",
]

for path in FILES:
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")

    backup = path.with_suffix(path.suffix + ".gemini.bak")
    shutil.copy2(path, backup)

    text = path.read_text(encoding="utf-8")

    text = text.replace(
        "from langchain_google_genai import ChatGoogleGenerativeAI",
        "from langchain_groq import ChatGroq"
    )

    text = text.replace(
        'GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")',
        'GROQ_API_KEY = os.getenv("GROQ_API_KEY")\nGROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")'
    )

    text = text.replace(
        'if not GOOGLE_API_KEY:\n    raise ValueError("GOOGLE_API_KEY not found in .env")',
        'if not GROQ_API_KEY:\n    raise ValueError("GROQ_API_KEY not found in .env")'
    )

    text = text.replace(
        'if not GOOGLE_API_KEY:\n    raise ValueError("GOOGLE_API_KEY not found in app/.env")',
        'if not GROQ_API_KEY:\n    raise ValueError("GROQ_API_KEY not found in app/.env")'
    )

    text = re.sub(
        r'llm = ChatGoogleGenerativeAI\(\s*model="gemini-3\.6-flash",\s*google_api_key=GOOGLE_API_KEY(?:,\s*max_retries=0)?\s*\)',
        '''llm = ChatGroq(
    model=GROQ_MODEL,
    groq_api_key=GROQ_API_KEY,
    temperature=0
)''',
        text
    )

    text = text.replace(
        'llm = ChatGoogleGenerativeAI(\n    model="gemini-3.6-flash"\n)',
        '''llm = ChatGroq(
    model=GROQ_MODEL,
    groq_api_key=GROQ_API_KEY,
    temperature=0
)'''
    )

    if "ChatGoogleGenerativeAI" in text:
        raise RuntimeError(f"Gemini import/init still present in {path}")

    if "ChatGroq" not in text:
        raise RuntimeError(f"Groq init not found in {path}")

    path.write_text(text, encoding="utf-8")
    print(f"Updated: {path}")
    print(f"Backup:  {backup}")

env = APP / ".env"
env_text = env.read_text(encoding="utf-8") if env.exists() else ""

if "GROQ_API_KEY=" not in env_text:
    with env.open("a", encoding="utf-8") as f:
        f.write("\nGROQ_API_KEY=PASTE_YOUR_GROQ_KEY_HERE\n")
        f.write("GROQ_MODEL=openai/gpt-oss-120b\n")
    print(f"Added Groq settings to: {env}")
else:
    print(f"GROQ_API_KEY already exists in: {env}")

print("\nDONE")
print("Install: pip install langchain-groq")
print("Then set GROQ_API_KEY in app/.env")
