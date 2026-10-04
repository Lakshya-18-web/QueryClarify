import os
import csv
import json
import argparse
import asyncio
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from ragas.llms import llm_factory
from ragas.embeddings.base import embedding_factory

from ragas.metrics.collections import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

DATA_DIR = PROJECT_DIR / "data"
CHROMA_DIR = DATA_DIR / "chroma"

ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)


# ============================================================
# ENVIRONMENT
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

if not GROQ_API_KEY:
    raise ValueError(
        "GROQ_API_KEY not found in app/.env"
    )


# ============================================================
# CSV LARGE FIELD SUPPORT
# ============================================================

csv_limit = 131072

while True:
    try:
        csv.field_size_limit(csv_limit)
        break
    except OverflowError:
        csv_limit //= 10


# ============================================================
# GROQ CLIENT
# ============================================================

client = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


# ============================================================
# RAGAS LLM
# ============================================================

evaluator_llm = llm_factory(
    GROQ_MODEL,
    provider="openai",
    client=client,
    max_tokens=4096,
)


# ============================================================
# RAGAS MODERN EMBEDDINGS
# ============================================================

ragas_embeddings = embedding_factory(
    "huggingface",
    "sentence-transformers/all-MiniLM-L6-v2"
)


# ============================================================
# CHROMA EMBEDDINGS
# ============================================================

chroma_embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)


# ============================================================
# CHROMA COLLECTION
# ============================================================

schema_vectorstore = Chroma(
    collection_name="spiderman_schema",
    persist_directory=str(CHROMA_DIR),
    embedding_function=chroma_embeddings,
)


# ============================================================
# RAGAS METRICS
# ============================================================

faithfulness_metric = Faithfulness(
    llm=evaluator_llm
)

answer_relevancy_metric = AnswerRelevancy(
    llm=evaluator_llm,
    embeddings=ragas_embeddings
)

context_precision_metric = ContextPrecision(
    llm=evaluator_llm
)

context_recall_metric = ContextRecall(
    llm=evaluator_llm
)


# ============================================================
# HELPERS
# ============================================================

def parse_json(value):

    if value is None:
        return []

    if isinstance(value, list):
        return value

    value = str(value).strip()

    if not value:
        return []

    try:

        parsed = json.loads(value)

        if isinstance(parsed, list):
            return parsed

        return [str(parsed)]

    except Exception:

        return [value]


def parse_table_names(value):

    if value is None:
        return []

    value = str(value).strip()

    if not value:
        return []

    # JSON list
    try:

        parsed = json.loads(value)

        if isinstance(parsed, list):

            return [
                str(x).strip()
                for x in parsed
                if str(x).strip()
            ]

    except Exception:
        pass

    # Pipe-separated format used by benchmark
    if "|" in value:

        return [
            x.strip()
            for x in value.split("|")
            if x.strip()
        ]

    # Comma-separated fallback
    if "," in value:

        return [
            x.strip()
            for x in value.split(",")
            if x.strip()
        ]

    return [value]


def extract_score(value):

    if value is None:
        return None

    if hasattr(value, "value"):

        try:
            return float(value.value)
        except Exception:
            pass

    try:
        return float(value)

    except Exception:

        return None


# ============================================================
# RETRIEVE ACTUAL CHROMA SCHEMA DOCUMENTS
# ============================================================

def retrieve_schema_context(
    database,
    table_names,
    question,
    top_k=5,
):

    contexts = []

    # --------------------------------------------------------
    # First: use the tables recorded by the benchmark
    # --------------------------------------------------------

    if table_names:

        ids = []

        for table in table_names:

            table = str(table).strip()

            if not table:
                continue

            # Normal stable ID used by ingest_chroma.py
            ids.append(
                f"{database}::{table}"
            )

        if ids:

            try:

                data = schema_vectorstore.get(
                    ids=ids,
                    include=[
                        "documents",
                        "metadatas"
                    ],
                )

                documents = data.get(
                    "documents",
                    []
                )

                metadatas = data.get(
                    "metadatas",
                    []
                )

                for i, document in enumerate(
                    documents
                ):

                    if not document:
                        continue

                    metadata = {}

                    if i < len(metadatas):
                        metadata = (
                            metadatas[i] or {}
                        )

                    contexts.append(
                        str(document)
                    )

            except Exception as e:

                print(
                    f"  Chroma ID retrieval warning: "
                    f"{e}"
                )


    # --------------------------------------------------------
    # Fallback: perform actual semantic retrieval
    # --------------------------------------------------------

    if not contexts:

        try:

            docs = (
                schema_vectorstore
                .similarity_search(
                    question,
                    k=top_k,
                    filter={
                        "database": database
                    },
                )
            )

            for doc in docs:

                if doc.page_content:

                    contexts.append(
                        str(doc.page_content)
                    )

        except Exception as e:

            print(
                f"  Chroma similarity retrieval error: "
                f"{e}"
            )


    # --------------------------------------------------------
    # Final fallback
    # --------------------------------------------------------

    if not contexts:

        contexts = [
            (
                f"DATABASE: {database}\n"
                f"No schema context retrieved."
            )
        ]

    return contexts


# ============================================================
# GENERATE NATURAL-LANGUAGE RESPONSE
# ============================================================

async def generate_natural_answer(
    question,
    predicted_sql,
    predicted_result,
):

    prompt = f"""
You are evaluating a Text-to-SQL system.

Convert the SQL execution result into a concise natural-language
answer to the user's question.

User question:
{question}

Generated SQL:
{predicted_sql}

SQL execution result:
{predicted_result}

Return ONLY the natural-language answer.
Do not mention SQL.
Do not explain the evaluation.
"""

    try:

        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You convert database query results "
                        "into concise natural-language answers."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
            max_tokens=512,
        )

        return (
            response.choices[0]
            .message
            .content
            .strip()
        )

    except Exception as e:

        print(
            f"  Natural answer generation error: {e}"
        )

        return predicted_result


# ============================================================
# EVALUATE ONE QUERY
# ============================================================

async def evaluate_one(
    row,
    index,
    top_k,
):

    question = str(
        row.get(
            "question",
            ""
        )
    ).strip()

    database = str(
        row.get(
            "database",
            ""
        )
    ).strip()

    predicted_sql = str(
        row.get(
            "predicted_sql",
            ""
        )
    ).strip()

    predicted_result = str(
        row.get(
            "predicted_result",
            ""
        )
    ).strip()

    gold_sql = str(
        row.get(
            "gold_sql",
            ""
        )
    ).strip()

    table_names = parse_table_names(
        row.get(
            "rag_tables",
            ""
        )
    )


    # ========================================================
    # ACTUAL RETRIEVED SCHEMA
    # ========================================================

    print(
        "  Retrieving actual Chroma schema..."
    )

    schema_contexts = retrieve_schema_context(
        database=database,
        table_names=table_names,
        question=question,
        top_k=top_k,
    )

    print(
        f"  Retrieved contexts: "
        f"{len(schema_contexts)}"
    )


    # ========================================================
    # NATURAL-LANGUAGE ANSWER
    # ========================================================

    print(
        "  Generating evaluation response..."
    )

    natural_answer = (
        await generate_natural_answer(
            question=question,
            predicted_sql=predicted_sql,
            predicted_result=predicted_result,
        )
    )


    result = {

        "index": index,

        "database": database,

        "question": question,

        "retrieved_tables": "|".join(
            table_names
        ),

        "retrieved_context_count": len(
            schema_contexts
        ),

        "faithfulness": None,

        "answer_relevancy": None,

        "context_precision": None,

        "context_recall": None,

        "error": "",
    }


    # ========================================================
    # 1. FAITHFULNESS
    # ========================================================

    print(
        "  Running Faithfulness..."
    )

    try:

        score = await faithfulness_metric.ascore(
            user_input=question,
            response=natural_answer,
            retrieved_contexts=schema_contexts,
        )

        result["faithfulness"] = extract_score(
            score
        )

    except Exception as e:

        error = (
            f"Faithfulness: "
            f"{type(e).__name__}: {e}"
        )

        print(
            f"  ERROR: {error}"
        )

        result["error"] += (
            error + " | "
        )


    # ========================================================
    # 2. ANSWER RELEVANCY
    # ========================================================

    print(
        "  Running Answer Relevancy..."
    )

    try:

        score = await answer_relevancy_metric.ascore(
            user_input=question,
            response=natural_answer,
        )

        result["answer_relevancy"] = extract_score(
            score
        )

    except Exception as e:

        error = (
            f"AnswerRelevancy: "
            f"{type(e).__name__}: {e}"
        )

        print(
            f"  ERROR: {error}"
        )

        result["error"] += (
            error + " | "
        )


    # ========================================================
    # 3. CONTEXT PRECISION
    # ========================================================

    print(
        "  Running Context Precision..."
    )

    try:

        score = await context_precision_metric.ascore(
            user_input=question,
            reference=gold_sql,
            retrieved_contexts=schema_contexts,
        )

        result["context_precision"] = extract_score(
            score
        )

    except Exception as e:

        error = (
            f"ContextPrecision: "
            f"{type(e).__name__}: {e}"
        )

        print(
            f"  ERROR: {error}"
        )

        result["error"] += (
            error + " | "
        )


    # ========================================================
    # 4. CONTEXT RECALL
    # ========================================================

    print(
        "  Running Context Recall..."
    )

    try:

        score = await context_recall_metric.ascore(
            user_input=question,
            retrieved_contexts=schema_contexts,
            reference=gold_sql,
        )

        result["context_recall"] = extract_score(
            score
        )

    except Exception as e:

        error = (
            f"ContextRecall: "
            f"{type(e).__name__}: {e}"
        )

        print(
            f"  ERROR: {error}"
        )

        result["error"] += (
            error + " | "
        )


    return result


# ============================================================
# MAIN
# ============================================================

async def main():

    parser = argparse.ArgumentParser(
        description=(
            "RAGAS evaluation for QueryClarify"
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Input benchmark CSV"
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Output RAGAS CSV"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of queries. 0 = all"
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Fallback Chroma retrieval count"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing output"
    )

    args = parser.parse_args()


    input_path = Path(
        args.input
    )

    output_path = Path(
        args.output
    )


    # ========================================================
    # HEADER
    # ========================================================

    print()
    print("=" * 70)
    print("QUERYCLARIFY - RAGAS EVALUATION")
    print("=" * 70)
    print()

    print(
        f"Input : {input_path}"
    )

    print(
        f"Output: {output_path}"
    )

    print(
        f"Limit : {args.limit}"
    )

    print(
        f"Top-K : {args.top_k}"
    )

    print()


    # ========================================================
    # INPUT CHECK
    # ========================================================

    if not input_path.exists():

        raise FileNotFoundError(
            f"Input file not found: "
            f"{input_path}"
        )


    # ========================================================
    # LOAD DATA
    # ========================================================

    df = pd.read_csv(
        input_path,
        dtype=str
    )

    print(
        f"Loaded benchmark rows: "
        f"{len(df)}"
    )


    # ========================================================
    # RESUME
    # ========================================================

    existing = {}

    if args.resume and output_path.exists():

        old_df = pd.read_csv(
            output_path,
            dtype=str
        )

        for _, old_row in old_df.iterrows():

            try:

                idx = int(
                    old_row["index"]
                )

                existing[idx] = (
                    old_row.to_dict()
                )

            except Exception:

                pass

        print(
            f"Existing results: "
            f"{len(existing)}"
        )


    # ========================================================
    # SELECT ROWS
    # ========================================================

    rows = df.to_dict(
        orient="records"
    )

    if args.limit > 0:

        rows = rows[:args.limit]


    print(
        f"Queries to evaluate: "
        f"{len(rows)}"
    )

    print()


    results = dict(existing)


    # ========================================================
    # RUN
    # ========================================================

    for position, row in enumerate(
        rows,
        start=1
    ):

        try:

            index = int(
                row.get(
                    "index",
                    position - 1
                )
            )

        except Exception:

            index = position - 1


        if (
            args.resume
            and index in existing
        ):

            print(
                f"[{position}/{len(rows)}] "
                f"Skipping query {index}"
            )

            continue


        print(
            "=" * 70
        )

        print(
            f"[{position}/{len(rows)}] "
            f"Query {index}"
        )

        print(
            f"Question: "
            f"{row.get('question', '')}"
        )

        print(
            f"Database: "
            f"{row.get('database', '')}"
        )

        print()


        result = await evaluate_one(
            row=row,
            index=index,
            top_k=args.top_k,
        )


        results[index] = result


        # ====================================================
        # QUERY SUMMARY
        # ====================================================

        print()

        print(
            f"  Faithfulness      : "
            f"{result['faithfulness']}"
        )

        print(
            f"  Answer Relevancy  : "
            f"{result['answer_relevancy']}"
        )

        print(
            f"  Context Precision : "
            f"{result['context_precision']}"
        )

        print(
            f"  Context Recall    : "
            f"{result['context_recall']}"
        )

        print()


        # ====================================================
        # SAVE AFTER EVERY QUERY
        # ====================================================

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        pd.DataFrame(
            list(results.values())
        ).to_csv(
            output_path,
            index=False
        )


    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    result_df = pd.DataFrame(
        list(results.values())
    )


    print()
    print("=" * 70)
    print("RAGAS SUMMARY")
    print("=" * 70)

    print(
        f"Total evaluated: "
        f"{len(result_df)}"
    )

    print()


    metrics = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]


    for metric in metrics:

        values = pd.to_numeric(
            result_df[metric],
            errors="coerce"
        ).dropna()


        if len(values) == 0:

            print(
                f"{metric:20s}: N/A"
            )

        else:

            print(
                f"{metric:20s}: "
                f"{values.mean():.4f}"
            )


    print()

    print(
        f"Saved to: "
        f"{output_path}"
    )

    print()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())