from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.query import router as query_router
from backend.api.history import router as history_router
from backend.api.database import router as database_router
from backend.api.schema import router as schema_router


app = FastAPI(
    title="QueryClarify API",
    description="RAG-Based Agentic Text-to-SQL API",
    version="1.0.0"
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# API ROUTERS
# ---------------------------------------------------------

app.include_router(
    query_router,
    prefix="/api"
)

app.include_router(
    history_router,
    prefix="/api"
)

app.include_router(
    database_router,
    prefix="/api"
)

app.include_router(
    schema_router,
    prefix="/api"
)


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------

@app.get("/")
def root():
    return {
        "name": "QueryClarify",
        "status": "running",
        "message": "Agentic RAG-Based Text-to-SQL API"
    }


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }