import { useState } from "react";

import DatabaseExplorer from "./DatabaseExplorer";
import SQLHistory from "./SQLHistory";
import type { HistoryItem } from "./SQLHistory";

import {
  Database,
  Send,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Table2,
  Code2,
  ShieldCheck,
  RotateCcw,
  History,
} from "lucide-react";

import "./index.css";

const API_URL =
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

type Page = "query" | "explorer" | "history";

type QueryResult = {
  status: string;

  question: string;

  database?: string | null;

  clarification_required?: boolean;

  clarification_type?: string | null;

  clarification_question?: string | null;

  database_candidates?: string[];

  sql?: string | null;

  result?: Record<string, unknown>[] | null;

  retrieved_tables?: string[];

  validation_valid?: boolean;

  validation_message?: string | null;

  execution_error?: string | null;

  retries?: number;

  message?: string | null;
};

function App() {
  const [activePage, setActivePage] =
    useState<Page>("query");

  const [question, setQuestion] =
    useState("");

  const [database, setDatabase] =
    useState("");

  const [clarification, setClarification] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [response, setResponse] =
    useState<QueryResult | null>(null);

  const [error, setError] =
    useState("");

  /*
   * =========================================================
   * RUN QUERY
   * =========================================================
   */

  const runQuery = async (
    overrideQuestion?: string,
    overrideDatabase?: string
  ) => {
    const activeQuestion =
      overrideQuestion !== undefined
        ? overrideQuestion
        : question;

    const activeDatabase =
      overrideDatabase !== undefined
        ? overrideDatabase
        : database;

    if (!activeQuestion.trim()) {
      setError("Please enter a question.");
      return;
    }

    setLoading(true);
    setError("");

    const startTime = performance.now();

    try {
      const body: {
        question: string;
        database?: string;
        clarification?: string;
      } = {
        question: activeQuestion.trim(),
      };

      if (activeDatabase.trim()) {
        body.database = activeDatabase.trim();
      }

      if (clarification.trim()) {
        body.clarification =
          clarification.trim();
      }

      const res = await fetch(
        `${API_URL}/api/query`,
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify(body),
        }
      );

      const data = await res.json();

      if (!res.ok) {
        throw new Error(
          data.detail ||
            "Something went wrong while processing the query."
        );
      }

      setResponse(data);

      /*
       * =====================================================
       * SAVE SUCCESSFUL QUERY TO HISTORY
       * =====================================================
       */

      if (data.status === "success") {
        const latencyMs =
          performance.now() - startTime;

        try {
          await fetch(
            `${API_URL}/api/history`,
            {
              method: "POST",

              headers: {
                "Content-Type":
                  "application/json",
              },

              body: JSON.stringify({
                question:
                  data.question ||
                  activeQuestion,

                database:
                  data.database ||
                  activeDatabase ||
                  null,

                sql:
                  data.sql ||
                  null,

                result:
                  data.result ||
                  [],

                row_count:
                  Array.isArray(data.result)
                    ? data.result.length
                    : 0,

                latency_ms:
                  Number(
                    latencyMs.toFixed(2)
                  ),

                validation_valid:
                  Boolean(
                    data.validation_valid
                  ),

                retrieved_tables:
                  data.retrieved_tables ||
                  [],

                retries:
                  data.retries || 0,
              }),
            }
          );
        } catch (historyError) {
          /*
           * History failure should never
           * make a successful query fail.
           */
          console.warn(
            "Could not save query history:",
            historyError
          );
        }
      }

      /*
       * Clear clarification after the
       * clarification request has been used.
       */
      if (clarification.trim()) {
        setClarification("");
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to connect to QueryClarify."
      );
    } finally {
      setLoading(false);
    }
  };

  /*
   * =========================================================
   * RESET QUERY
   * =========================================================
   */

  const resetQuery = () => {
    setQuestion("");
    setDatabase("");
    setClarification("");
    setResponse(null);
    setError("");
    setActivePage("query");
  };

  /*
   * =========================================================
   * DATABASE SELECT
   * =========================================================
   */

  const handleDatabaseSelect = (
    db: string
  ) => {
    setDatabase(db);
  };

  /*
   * =========================================================
   * USE DATABASE FROM EXPLORER
   * =========================================================
   */

  const handleUseDatabase = (
    db: string
  ) => {
    setDatabase(db);
    setActivePage("query");
  };

  /*
   * =========================================================
   * RERUN FROM HISTORY
   * =========================================================
   */

  const handleRerunHistory = (
    item: HistoryItem
  ) => {
    setQuestion(item.question);
    setDatabase(item.database || "");
    setClarification("");
    setResponse(null);
    setError("");

    setActivePage("query");

    /*
     * Pass values directly so we don't depend
     * on React state updating first.
     */
    void runQuery(
      item.question,
      item.database || ""
    );
  };

  /*
   * =========================================================
   * SIDEBAR NAVIGATION
   * =========================================================
   */

  const openQueryWorkspace = () => {
    setActivePage("query");
    setError("");
  };

  const openSchemaExplorer = () => {
    setActivePage("explorer");
    setError("");
  };

  const openSQLHistory = () => {
    setActivePage("history");
    setError("");
  };

  /*
   * =========================================================
   * APP
   * =========================================================
   */

  return (
    <div className="app-shell">

      {/* =====================================================
          SIDEBAR
      ===================================================== */}

      <aside className="sidebar">

        {/* BRAND */}

        <div className="brand">

          <div className="brand-icon">
            <Database size={22} />
          </div>

          <div>
            <h1>QueryClarify</h1>

            <p>
              Agentic Text-to-SQL
            </p>
          </div>

        </div>


        {/* SYSTEM NAVIGATION */}

        <div className="sidebar-section">

          <p className="sidebar-label">
            SYSTEM
          </p>


          {/* QUERY WORKSPACE */}

          <div
            className={
              activePage === "query"
                ? "sidebar-item active"
                : "sidebar-item"
            }
            onClick={
              openQueryWorkspace
            }
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" ||
                e.key === " "
              ) {
                openQueryWorkspace();
              }
            }}
          >

            <Database size={17} />

            <span>
              Query Workspace
            </span>

          </div>


          {/* SCHEMA EXPLORER */}

          <div
            className={
              activePage === "explorer"
                ? "sidebar-item active"
                : "sidebar-item"
            }
            onClick={
              openSchemaExplorer
            }
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" ||
                e.key === " "
              ) {
                openSchemaExplorer();
              }
            }}
          >

            <Table2 size={17} />

            <span>
              Schema Explorer
            </span>

          </div>


          {/* SQL HISTORY */}

          <div
            className={
              activePage === "history"
                ? "sidebar-item active"
                : "sidebar-item"
            }
            onClick={
              openSQLHistory
            }
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" ||
                e.key === " "
              ) {
                openSQLHistory();
              }
            }}
          >

            <History size={17} />

            <span>
              SQL History
            </span>

          </div>

        </div>


        {/* SIDEBAR STATUS */}

        <div className="sidebar-bottom">

          <div className="system-status">

            <span className="status-dot"></span>

            <div>

              <strong>
                System Online
              </strong>

              <span>
                FastAPI connected
              </span>

            </div>

          </div>

        </div>

      </aside>


      {/* =====================================================
          MAIN CONTENT
      ===================================================== */}

      <main className="main-content">


        {/* ===================================================
            SCHEMA EXPLORER
        =================================================== */}

        {activePage === "explorer" && (

          <DatabaseExplorer
            onUseDatabase={
              handleUseDatabase
            }
          />

        )}


        {/* ===================================================
            SQL HISTORY
        =================================================== */}

        {activePage === "history" && (

          <SQLHistory
            onRerun={
              handleRerunHistory
            }
          />

        )}


        {/* ===================================================
            QUERY WORKSPACE
        =================================================== */}

        {activePage === "query" && (

          <>

            {/* TOP BAR */}

            <header className="topbar">

              <div>

                <p className="eyebrow">
                  AI DATABASE ASSISTANT
                </p>

                <h2>
                  Query Workspace
                </h2>

              </div>


              <button
                className="reset-button"
                onClick={
                  resetQuery
                }
              >

                <RotateCcw
                  size={16}
                />

                New Query

              </button>

            </header>


            {/* WORKSPACE */}

            <section className="workspace">


              {/* =================================================
                  QUERY CARD
              ================================================= */}

              <div className="query-card">

                <div className="card-heading">

                  <div>

                    <h3>
                      Ask your database
                    </h3>

                    <p>
                      Describe what you need in
                      natural language. QueryClarify
                      handles routing, retrieval,
                      SQL generation and validation.
                    </p>

                  </div>

                </div>


                {/* QUESTION */}

                <textarea
                  className="query-input"
                  placeholder="e.g. How many students are there?"
                  value={question}
                  onChange={(e) =>
                    setQuestion(
                      e.target.value
                    )
                  }
                  onKeyDown={(e) => {

                    if (
                      e.key === "Enter" &&
                      (e.ctrlKey ||
                        e.metaKey)
                    ) {
                      void runQuery();
                    }

                  }}
                />


                {/* CONTROLS */}

                <div className="query-controls">

                  <div className="database-input-wrapper">

                    <Database size={17} />

                    <input
                      value={database}
                      onChange={(e) =>
                        setDatabase(
                          e.target.value
                        )
                      }
                      placeholder="Database (optional)"
                    />

                  </div>


                  <button
                    className="run-button"
                    onClick={() =>
                      void runQuery()
                    }
                    disabled={loading}
                  >

                    {loading ? (

                      <>
                        <Loader2
                          className="spin"
                          size={18}
                        />

                        Running...
                      </>

                    ) : (

                      <>
                        <Send size={18} />

                        Run Query
                      </>

                    )}

                  </button>

                </div>


                {/* SHORTCUT */}

                <div className="shortcut">

                  <span>
                    Tip
                  </span>

                  Press{" "}

                  <kbd>
                    Ctrl
                  </kbd>

                  {" + "}

                  <kbd>
                    Enter
                  </kbd>

                  {" "}to run

                </div>

              </div>


              {/* =================================================
                  ERROR
              ================================================= */}

              {error && (

                <div className="error-box">

                  <AlertCircle size={20} />

                  <div>

                    <strong>
                      Request failed
                    </strong>

                    <p>
                      {error}
                    </p>

                  </div>

                </div>

              )}


              {/* =================================================
                  CLARIFICATION
              ================================================= */}

              {response?.clarification_required && (

                <div className="clarification-card">

                  <div className="clarification-icon">

                    <AlertCircle size={22} />

                  </div>


                  <div className="clarification-content">

                    <span className="section-tag">

                      {response.clarification_type ===
                      "database"
                        ? "DATABASE CLARIFICATION"
                        : "CLARIFICATION REQUIRED"}

                    </span>


                    <h3>
                      {
                        response.clarification_question
                      }
                    </h3>


                    {/* DATABASE CANDIDATES */}

                    {response.clarification_type ===
                      "database" &&
                      response.database_candidates &&
                      response.database_candidates
                        .length > 0 && (

                        <div className="candidate-list">

                          {response.database_candidates.map(
                            (db) => (

                              <button
                                key={db}
                                onClick={() =>
                                  handleDatabaseSelect(
                                    db
                                  )
                                }
                                className={
                                  database === db
                                    ? "candidate selected"
                                    : "candidate"
                                }
                              >

                                <Database
                                  size={16}
                                />

                                {db}

                              </button>

                            )
                          )}

                        </div>

                      )}


                    {/* NORMAL CLARIFICATION */}

                    {response.clarification_type !==
                      "database" && (

                      <div className="clarification-input-row">

                        <input
                          value={
                            clarification
                          }
                          onChange={(e) =>
                            setClarification(
                              e.target.value
                            )
                          }
                          placeholder="Type your clarification..."
                        />

                        <button
                          className="run-button"
                          onClick={() =>
                            void runQuery()
                          }
                          disabled={loading}
                        >
                          Continue
                        </button>

                      </div>

                    )}


                    {/* DATABASE CONTINUE */}

                    {response.clarification_type ===
                      "database" &&
                      database && (

                        <button
                          className="continue-button"
                          onClick={() =>
                            void runQuery()
                          }
                          disabled={loading}
                        >

                          Continue with{" "}
                          {database}

                        </button>

                      )}

                  </div>

                </div>

              )}


              {/* =================================================
                  SUCCESS RESULT
              ================================================= */}

              {response?.status ===
                "success" && (

                <>

                  {/* PIPELINE */}

                  <div className="pipeline-card">

                    <div className="pipeline-header">

                      <div>

                        <span className="section-tag">
                          PIPELINE
                        </span>

                        <h3>
                          Query execution flow
                        </h3>

                      </div>


                      <span className="success-badge">

                        <CheckCircle2
                          size={15}
                        />

                        Completed

                      </span>

                    </div>


                    <div className="pipeline">

                      <PipelineStep
                        label="Question"
                      />

                      <PipelineLine />

                      <PipelineStep
                        label="DB Routing"
                      />

                      <PipelineLine />

                      <PipelineStep
                        label="RAG"
                      />

                      <PipelineLine />

                      <PipelineStep
                        label="SQL"
                      />

                      <PipelineLine />

                      <PipelineStep
                        label="Validation"
                      />

                      <PipelineLine />

                      <PipelineStep
                        label="Execution"
                      />

                    </div>

                  </div>


                  {/* METRICS */}

                  <div className="metrics-grid">

                    <Metric
                      label="Database"
                      value={
                        response.database ||
                        "—"
                      }
                    />

                    <Metric
                      label="Retrieved Tables"
                      value={
                        response
                          .retrieved_tables
                          ?.length
                          ?.toString() ||
                        "0"
                      }
                    />

                    <Metric
                      label="SQL Retries"
                      value={
                        response.retries?.toString() ||
                        "0"
                      }
                    />

                    <Metric
                      label="Validation"
                      value={
                        response.validation_valid
                          ? "Passed"
                          : "Failed"
                      }
                      success={
                        response.validation_valid
                      }
                    />

                  </div>


                  {/* GENERATED SQL */}

                  <div className="result-card">

                    <div className="result-header">

                      <div>

                        <span className="section-tag">
                          GENERATED SQL
                        </span>

                        <h3>
                          Query generated by the agent
                        </h3>

                      </div>

                      <Code2 size={20} />

                    </div>


                    <pre className="sql-block">

                      {response.sql ||
                        "No SQL generated."}

                    </pre>

                  </div>


                  {/* SCHEMA RAG */}

                  <div className="result-card">

                    <div className="result-header">

                      <div>

                        <span className="section-tag">
                          SCHEMA RAG
                        </span>

                        <h3>
                          Retrieved tables
                        </h3>

                      </div>

                      <Table2 size={20} />

                    </div>


                    <div className="table-tags">

                      {response.retrieved_tables
                        ?.length ? (

                        response.retrieved_tables.map(
                          (table) => (

                            <span
                              className="table-tag"
                              key={table}
                            >

                              <Table2
                                size={14}
                              />

                              {table}

                            </span>

                          )
                        )

                      ) : (

                        <span className="muted">
                          No tables reported.
                        </span>

                      )}

                    </div>

                  </div>


                  {/* DATABASE RESULT */}

                  <div className="result-card">

                    <div className="result-header">

                      <div>

                        <span className="section-tag">
                          RESULT
                        </span>

                        <h3>
                          Database response
                        </h3>

                      </div>


                      <span className="success-badge">

                        <ShieldCheck
                          size={15}
                        />

                        SQL Validated

                      </span>

                    </div>


                    {response.result &&
                    response.result.length >
                      0 ? (

                      <ResultTable
                        rows={
                          response.result
                        }
                      />

                    ) : (

                      <div className="empty-result">

                        Query executed
                        successfully but
                        returned no rows.

                      </div>

                    )}

                  </div>

                </>

              )}

            </section>

          </>

        )}

      </main>

    </div>
  );
}


/* =============================================================
   PIPELINE STEP
============================================================= */

function PipelineStep({
  label,
}: {
  label: string;
}) {

  return (

    <div className="pipeline-step">

      <div className="pipeline-check">

        <CheckCircle2 size={16} />

      </div>

      <span>
        {label}
      </span>

    </div>

  );
}


/* =============================================================
   PIPELINE LINE
============================================================= */

function PipelineLine() {

  return (
    <div className="pipeline-line"></div>
  );

}


/* =============================================================
   METRIC
============================================================= */

function Metric({
  label,
  value,
  success = false,
}: {
  label: string;
  value: string;
  success?: boolean;
}) {

  return (

    <div className="metric-card">

      <span>
        {label}
      </span>

      <strong
        className={
          success
            ? "metric-success"
            : ""
        }
      >
        {value}
      </strong>

    </div>

  );

}


/* =============================================================
   RESULT TABLE
============================================================= */

function ResultTable({
  rows,
}: {
  rows: Record<string, unknown>[];
}) {

  const columns = Object.keys(
    rows[0] || {}
  );

  return (

    <div className="table-wrapper">

      <table>

        <thead>

          <tr>

            {columns.map(
              (column) => (

                <th key={column}>
                  {column}
                </th>

              )
            )}

          </tr>

        </thead>


        <tbody>

          {rows.map(
            (row, index) => (

              <tr key={index}>

                {columns.map(
                  (column) => (

                    <td key={column}>

                      {String(
                        row[column] ??
                          "NULL"
                      )}

                    </td>

                  )
                )}

              </tr>

            )
          )}

        </tbody>

      </table>

    </div>

  );

}


export default App;