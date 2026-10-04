import { useEffect, useState } from "react";

import DatabaseExplorer from "./DatabaseExplorer";
import SQLHistory from "./SQLHistory";
import DatabaseUpload from "./DatabaseUpload";
import Navbar from "./Navbar";
import Pipeline from "./Pipeline";
import SqlViewer from "./SqlViewer";
import RetrievedTables from "./RetrievedTables";
import ResultsTable from "./ResultsTable";
import CloudWatch from "./CloudWatch";
import type { Page } from "./Navbar";
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
  RefreshCw,
  Sparkles,
} from "lucide-react";

import "./index.css";
import "./upload.css";

import { API_URL, apiFetch } from "./api";

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

const stageMessages = [
  "Understanding question...",
  "Finding relevant database...",
  "Retrieving schema...",
  "Generating SQL...",
  "Validating query...",
  "Executing...",
];

function App() {
  const [activePage, setActivePage] = useState<Page>("databases");

  const [question, setQuestion] = useState("");

  const [database, setDatabase] = useState("");

  const [databaseLabels, setDatabaseLabels] = useState<Record<string, string>>(
    {}
  );

  const [clarification, setClarification] = useState("");

  const [loading, setLoading] = useState(false);

  const [stage, setStage] = useState(0);

  const [response, setResponse] = useState<QueryResult | null>(null);

  const [error, setError] = useState("");

  useEffect(() => {
    if (!loading) {
      setStage(0);
      return;
    }

    const timer = setInterval(() => {
      setStage((current) => Math.min(current + 1, 5));
    }, 1500);

    return () => clearInterval(timer);
  }, [loading]);

  const activeLabel = database ? databaseLabels[database] || database : "";

  const runQuery = async (
    overrideQuestion?: string,
    overrideDatabase?: string
  ) => {
    const activeQuestion =
      overrideQuestion !== undefined ? overrideQuestion : question;

    const activeDatabase =
      overrideDatabase !== undefined ? overrideDatabase : database;

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
        body.clarification = clarification.trim();
      }

      const res = await apiFetch(`${API_URL}/api/query`, {
        method: "POST",

        headers: {
          "Content-Type": "application/json",
        },

        body: JSON.stringify(body),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(
          data.detail || "Something went wrong while processing the query."
        );
      }

      setResponse(data);

      if (data.status === "success") {
        const latencyMs = performance.now() - startTime;

        try {
          await apiFetch(`${API_URL}/api/history`, {
            method: "POST",

            headers: {
              "Content-Type": "application/json",
            },

            body: JSON.stringify({
              question: data.question || activeQuestion,

              database: data.database || activeDatabase || null,

              sql: data.sql || null,

              result: data.result || [],

              row_count: Array.isArray(data.result) ? data.result.length : 0,

              latency_ms: Number(latencyMs.toFixed(2)),

              validation_valid: Boolean(data.validation_valid),

              retrieved_tables: data.retrieved_tables || [],

              retries: data.retries || 0,
            }),
          });
        } catch (historyError) {
          console.warn("Could not save query history:", historyError);
        }
      }

      if (clarification.trim()) {
        setClarification("");
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to connect to QueryClarify."
      );
    } finally {
      setLoading(false);
    }
  };

  const resetQuery = () => {
    setQuestion("");
    setDatabase("");
    setClarification("");
    setResponse(null);
    setError("");
    setActivePage("query");
  };

  const handleDatabaseSelect = (db: string) => {
    setDatabase(db);
  };

  const handleUseDatabase = (db: string) => {
    setDatabase(db);
    setResponse(null);
    setError("");
    setActivePage("query");
  };

  const handleRerunHistory = (item: HistoryItem) => {
    setQuestion(item.question);
    setDatabase(item.database || "");
    setClarification("");
    setResponse(null);
    setError("");

    setActivePage("query");

    void runQuery(item.question, item.database || "");
  };

  const handleUseUserDatabase = (id: string, label: string) => {
    setDatabaseLabels((prev) => ({
      ...prev,
      [id]: label,
    }));
    setDatabase(id);
    setResponse(null);
    setError("");
    setActivePage("query");
  };

  const navigate = (page: Page) => {
    setActivePage(page);
    setError("");
  };

  return (
    <div className="app-shell">
      <Navbar
        active={activePage}
        activeDatabase={activeLabel}
        onNavigate={navigate}
      />

      <main className="main-content">
        {activePage === "databases" && (
          <DatabaseUpload
            onUseDatabase={handleUseUserDatabase}
            onOpenLibrary={() => navigate("library")}
          />
        )}

        {activePage === "library" && (
          <DatabaseExplorer onUseDatabase={handleUseDatabase} />
        )}

        {activePage === "cloud" && <CloudWatch />}

        {activePage === "history" && <SQLHistory onRerun={handleRerunHistory} />}

        {activePage === "query" && (
          <>
            <header className="topbar">
              <div>
                <p className="eyebrow">AI DATABASE ASSISTANT</p>

                <h2>Query Workspace</h2>
              </div>

              <button className="reset-button" onClick={resetQuery}>
                <RotateCcw size={16} />
                New Query
              </button>
            </header>

            <section className="workspace">
              <div className="active-db">
                <span className={database ? "status-pill on" : "status-pill"}>
                  <span className="status-dot"></span>
                  {database ? "Active Database" : "Auto routing"}
                </span>

                <div className="active-db-info">
                  <small>Currently querying</small>

                  <strong>
                    {activeLabel || "Automatic routing across the built-in library"}
                  </strong>
                </div>

                <div className="active-db-actions">
                  <button onClick={() => navigate("databases")}>Change</button>

                  {database && (
                    <button onClick={() => setDatabase("")}>Clear</button>
                  )}
                </div>
              </div>

              <div className="query-card">
                <div className="card-heading">
                  <div>
                    <h3>Ask your database</h3>

                    <p>
                      Describe what you need in natural language. QueryClarify
                      handles routing, retrieval, SQL generation and
                      validation.
                    </p>
                  </div>
                </div>

                <textarea
                  className="query-input"
                  placeholder="What would you like to know?  e.g. How many students are there?"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                      void runQuery();
                    }
                  }}
                />

                <div className="query-controls">
                  <div className="database-input-wrapper">
                    <Database size={17} />

                    <input
                      value={database}
                      onChange={(e) => setDatabase(e.target.value)}
                      placeholder="Database (optional)"
                    />
                  </div>

                  <button
                    className="run-button"
                    onClick={() => void runQuery()}
                    disabled={loading}
                  >
                    {loading ? (
                      <>
                        <Loader2 className="spin" size={18} />
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

                <div className="shortcut">
                  <span>Tip</span>
                  Press <kbd>Ctrl</kbd>
                  {" + "}
                  <kbd>Enter</kbd> to run
                </div>
              </div>

              {loading && (
                <div className="pipeline-card">
                  <div className="pipeline-header">
                    <div>
                      <span className="section-tag">PIPELINE</span>

                      <h3 className="thinking">
                        <Sparkles size={15} />
                        {stageMessages[stage]}
                      </h3>
                    </div>

                    <span className="running-badge">
                      <Loader2 className="spin" size={14} />
                      Running
                    </span>
                  </div>

                  <Pipeline activeIndex={stage} finished={false} />
                </div>
              )}

              {error && (
                <div className="error-box">
                  <AlertCircle size={20} />

                  <div>
                    <strong>Something went wrong while running the query.</strong>

                    <p>Check your question and try again.</p>

                    <details>
                      <summary>Technical details</summary>

                      <code>{error}</code>
                    </details>

                    <button
                      className="retry-button"
                      onClick={() => void runQuery()}
                      disabled={loading}
                    >
                      <RefreshCw size={13} />
                      Try Again
                    </button>
                  </div>
                </div>
              )}

              {response?.clarification_required && !loading && (
                <div className="clarification-card">
                  <div className="clarification-icon">
                    <AlertCircle size={22} />
                  </div>

                  <div className="clarification-content">
                    <span className="section-tag">
                      {response.clarification_type === "database"
                        ? "DATABASE CLARIFICATION"
                        : "CLARIFICATION REQUIRED"}
                    </span>

                    <h3>{response.clarification_question}</h3>

                    {response.clarification_type === "database" &&
                      response.database_candidates &&
                      response.database_candidates.length > 0 && (
                        <div className="candidate-list">
                          {response.database_candidates.map((db) => (
                            <button
                              key={db}
                              onClick={() => handleDatabaseSelect(db)}
                              className={
                                database === db
                                  ? "candidate selected"
                                  : "candidate"
                              }
                            >
                              <Database size={16} />

                              {db}
                            </button>
                          ))}
                        </div>
                      )}

                    {response.clarification_type !== "database" && (
                      <div className="clarification-input-row">
                        <input
                          value={clarification}
                          onChange={(e) => setClarification(e.target.value)}
                          placeholder="Type your clarification..."
                        />

                        <button
                          className="run-button"
                          onClick={() => void runQuery()}
                          disabled={loading}
                        >
                          Continue
                        </button>
                      </div>
                    )}

                    {response.clarification_type === "database" && database && (
                      <button
                        className="continue-button"
                        onClick={() => void runQuery()}
                        disabled={loading}
                      >
                        Continue with {database}
                      </button>
                    )}
                  </div>
                </div>
              )}

              {response?.status === "success" && !loading && (
                <>
                  <div className="pipeline-card">
                    <div className="pipeline-header">
                      <div>
                        <span className="section-tag">PIPELINE</span>

                        <h3>Query execution flow</h3>
                      </div>

                      <span className="success-badge">
                        <CheckCircle2 size={15} />
                        Completed
                      </span>
                    </div>

                    <Pipeline activeIndex={6} finished={true} />
                  </div>

                  <div className="metrics-grid">
                    <Metric label="Database" value={response.database || "—"} />

                    <Metric
                      label="Retrieved Tables"
                      value={response.retrieved_tables?.length?.toString() || "0"}
                    />

                    <Metric
                      label="SQL Retries"
                      value={response.retries?.toString() || "0"}
                    />

                    <Metric
                      label="Validation"
                      value={response.validation_valid ? "Passed" : "Failed"}
                      success={response.validation_valid}
                    />
                  </div>

                  <div className="result-card">
                    <div className="result-header">
                      <div>
                        <span className="section-tag">SCHEMA RAG</span>

                        <h3>Retrieved tables</h3>
                      </div>

                      <Table2 size={20} />
                    </div>

                    <RetrievedTables
                      database={response.database}
                      tables={response.retrieved_tables || []}
                    />
                  </div>

                  <div className="result-card">
                    <div className="result-header">
                      <div>
                        <span className="section-tag">GENERATED SQL</span>

                        <h3>Query generated by the agent</h3>
                      </div>

                      <Code2 size={20} />
                    </div>

                    <SqlViewer sql={response.sql || "No SQL generated."} />
                  </div>

                  <div className="result-card">
                    <div className="result-header">
                      <div>
                        <span className="section-tag">VALIDATION</span>

                        <h3>Agent checks</h3>
                      </div>

                      <ShieldCheck size={20} />
                    </div>

                    <div
                      className={
                        response.validation_valid
                          ? "validation-row ok"
                          : "validation-row bad"
                      }
                    >
                      {response.retries && response.retries > 0 ? (
                        <>
                          <RefreshCw size={16} />
                          <span>
                            SQL corrected · Retry {response.retries}
                            {response.validation_valid
                              ? " · Validation passed"
                              : " · Validation failed"}
                          </span>
                        </>
                      ) : (
                        <>
                          <CheckCircle2 size={16} />
                          <span>
                            {response.validation_valid
                              ? "SQL validated · 0 retries"
                              : "Validation failed"}
                          </span>
                        </>
                      )}
                    </div>

                    {response.validation_message && (
                      <p className="validation-note">
                        {response.validation_message}
                      </p>
                    )}
                  </div>

                  <div className="result-card">
                    <div className="result-header">
                      <div>
                        <span className="section-tag">RESULT</span>

                        <h3>Database response</h3>
                      </div>

                      <span className="success-badge">
                        <ShieldCheck size={15} />
                        SQL Validated
                      </span>
                    </div>

                    {response.result && response.result.length > 0 ? (
                      <ResultsTable rows={response.result} />
                    ) : (
                      <div className="empty-result">
                        <strong>Query executed successfully.</strong>

                        <span>The query returned no matching records.</span>
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
      <span>{label}</span>

      <strong className={success ? "metric-success" : ""}>{value}</strong>
    </div>
  );
}

export default App;