import { useEffect, useMemo, useState } from "react";

import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  Loader2,
  Play,
  Rows3,
  Search,
  Table2,
  Trash2,
  X,
  History,
  Sparkles,
} from "lucide-react";

import { API_URL, apiFetch } from "./api";

export type HistoryItem = {
  id: number;
  question: string;
  database?: string | null;
  sql?: string | null;
  result: Record<string, unknown>[];
  row_count: number;
  latency_ms?: number | null;
  validation_valid: boolean;
  retrieved_tables: string[];
  retries: number;
  timestamp: string;
};

type SQLHistoryProps = {
  onRerun: (item: HistoryItem) => void;
};

export default function SQLHistory({
  onRerun,
}: SQLHistoryProps) {
  const [history, setHistory] =
    useState<HistoryItem[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const [search, setSearch] =
    useState("");

  const [databaseFilter, setDatabaseFilter] =
    useState("all");

  const [expanded, setExpanded] =
    useState<number | null>(null);

  /* ==========================================================
     LOAD HISTORY
  ========================================================== */

  const loadHistory = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch(
        `${API_URL}/api/history`
      );

      if (!response.ok) {
        throw new Error(
          "Unable to load SQL history."
        );
      }

      const data = await response.json();

      setHistory(
        Array.isArray(data.history)
          ? data.history
          : []
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load SQL history."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadHistory();
  }, []);

  /* ==========================================================
     DATABASES
  ========================================================== */

  const databases = useMemo(() => {
    return Array.from(
      new Set(
        history
          .map((item) => item.database)
          .filter(
            (value): value is string =>
              Boolean(value)
          )
      )
    ).sort();
  }, [history]);

  /* ==========================================================
     FILTER
  ========================================================== */

  const filteredHistory = useMemo(() => {
    const value =
      search.trim().toLowerCase();

    return history.filter((item) => {
      const matchesSearch =
        !value ||
        item.question
          .toLowerCase()
          .includes(value) ||
        (item.sql || "")
          .toLowerCase()
          .includes(value) ||
        (item.database || "")
          .toLowerCase()
          .includes(value);

      const matchesDatabase =
        databaseFilter === "all" ||
        item.database === databaseFilter;

      return (
        matchesSearch &&
        matchesDatabase
      );
    });
  }, [
    history,
    search,
    databaseFilter,
  ]);

  /* ==========================================================
     DELETE
  ========================================================== */

  const deleteItem = async (
    id: number
  ) => {
    try {
      const response = await apiFetch(
        `${API_URL}/api/history/${id}`,
        {
          method: "DELETE",
        }
      );

      if (!response.ok) {
        throw new Error(
          "Unable to delete history item."
        );
      }

      setHistory((items) =>
        items.filter(
          (item) => item.id !== id
        )
      );

      if (expanded === id) {
        setExpanded(null);
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Delete failed."
      );
    }
  };

  /* ==========================================================
     CLEAR ALL
  ========================================================== */

  const clearHistory = async () => {
    if (!history.length) {
      return;
    }

    const confirmed = window.confirm(
      "Clear all saved query history?"
    );

    if (!confirmed) {
      return;
    }

    try {
      const response = await apiFetch(
        `${API_URL}/api/history`,
        {
          method: "DELETE",
        }
      );

      if (!response.ok) {
        throw new Error(
          "Unable to clear history."
        );
      }

      setHistory([]);
      setExpanded(null);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Clear failed."
      );
    }
  };

  /* ==========================================================
     STATS
  ========================================================== */

  const totalRows = history.reduce(
    (sum, item) =>
      sum + (item.row_count || 0),
    0
  );

  const averageLatency =
    history.length > 0
      ? Math.round(
          history.reduce(
            (sum, item) =>
              sum +
              (item.latency_ms || 0),
            0
          ) / history.length
        )
      : 0;

  /* ==========================================================
     UI
  ========================================================== */

  return (
    <div className="history-page">

      {/* ======================================================
          TOP BAR
      ====================================================== */}

      <header className="topbar history-topbar">

        <div className="history-title-group">

          <div className="history-title-icon">
            <History size={21} />
          </div>

          <div>
            <p className="eyebrow">
              QUERY MEMORY
            </p>

            <h2>
              SQL History
            </h2>
          </div>

        </div>

        <button
          className="history-clear-button"
          onClick={() =>
            void clearHistory()
          }
          disabled={!history.length}
        >
          <Trash2 size={16} />
          Clear All
        </button>

      </header>


      {/* ======================================================
          CONTENT
      ====================================================== */}

      <section className="history-content">

        {/* ====================================================
            HERO
        ==================================================== */}

        <div className="history-hero">

          <div className="history-hero-left">

            <div className="history-hero-icon">
              <Sparkles size={21} />
            </div>

            <div>

              <span className="history-kicker">
                QUERY ARCHIVE
              </span>

              <h3>
                Your database activity
              </h3>

              <p>
                Search, inspect and rerun
                previously executed queries.
                Every successful execution is
                automatically stored here.
              </p>

            </div>

          </div>


          <div className="history-live-indicator">

            <span className="history-live-dot" />

            Live memory

          </div>

        </div>


        {/* ====================================================
            STAT CARDS
        ==================================================== */}

        <div className="history-stats">

          <div className="history-stat-card">

            <div className="history-stat-icon blue">
              <History size={18} />
            </div>

            <div>
              <span>
                Saved Queries
              </span>

              <strong>
                {history.length}
              </strong>
            </div>

          </div>


          <div className="history-stat-card">

            <div className="history-stat-icon purple">
              <Database size={18} />
            </div>

            <div>
              <span>
                Databases
              </span>

              <strong>
                {databases.length}
              </strong>
            </div>

          </div>


          <div className="history-stat-card">

            <div className="history-stat-icon green">
              <Rows3 size={18} />
            </div>

            <div>
              <span>
                Rows Returned
              </span>

              <strong>
                {totalRows.toLocaleString()}
              </strong>
            </div>

          </div>


          <div className="history-stat-card">

            <div className="history-stat-icon orange">
              <Clock3 size={18} />
            </div>

            <div>
              <span>
                Avg. Latency
              </span>

              <strong>
                {averageLatency
                  ? `${averageLatency} ms`
                  : "—"}
              </strong>
            </div>

          </div>

        </div>


        {/* ====================================================
            TOOLBAR
        ==================================================== */}

        <div className="history-toolbar">

          <div className="history-search">

            <Search size={17} />

            <input
              value={search}
              onChange={(e) =>
                setSearch(e.target.value)
              }
              placeholder="Search questions, SQL or databases..."
            />

            {search && (
              <button
                className="history-search-clear"
                onClick={() =>
                  setSearch("")
                }
              >
                <X size={15} />
              </button>
            )}

          </div>


          <select
            className="history-filter"
            value={databaseFilter}
            onChange={(e) =>
              setDatabaseFilter(
                e.target.value
              )
            }
          >
            <option value="all">
              All databases
            </option>

            {databases.map((db) => (
              <option
                key={db}
                value={db}
              >
                {db}
              </option>
            ))}
          </select>

        </div>


        {/* ====================================================
            ERROR
        ==================================================== */}

        {error && (
          <div className="history-error">

            <AlertCircle size={19} />

            <div>
              <strong>
                History error
              </strong>

              <p>
                {error}
              </p>
            </div>

            <button
              onClick={() =>
                setError("")
              }
            >
              <X size={16} />
            </button>

          </div>
        )}


        {/* ====================================================
            LOADING
        ==================================================== */}

        {loading ? (

          <div className="history-empty-state">

            <div className="history-empty-icon loading">
              <Loader2
                className="spin"
                size={28}
              />
            </div>

            <h3>
              Loading query history
            </h3>

            <p>
              Fetching your previous executions...
            </p>

          </div>

        ) : filteredHistory.length === 0 ? (

          /* ==================================================
             EMPTY STATE
          ================================================== */

          <div className="history-empty-state">

            <div className="history-empty-icon">
              <Code2 size={31} />
            </div>

            <h3>
              {history.length
                ? "No matching queries"
                : "No queries saved yet"}
            </h3>

            <p>
              {history.length
                ? "Try changing your search or database filter."
                : "Run a successful query in the Query Workspace and your execution will automatically appear here."}
            </p>

            {!history.length && (
              <div className="history-empty-hint">
                <Database size={15} />
                Query Workspace → Run Query
              </div>
            )}

          </div>

        ) : (

          /* ==================================================
             HISTORY LIST
          ================================================== */

          <div className="history-list">

            <div className="history-list-header">

              <div>
                <span className="history-list-title">
                  Recent executions
                </span>

                <span className="history-list-count">
                  {filteredHistory.length}
                </span>
              </div>

              <span className="history-list-caption">
                Click a query to inspect details
              </span>

            </div>


            {filteredHistory.map(
              (item) => {

                const isOpen =
                  expanded === item.id;

                return (

                  <article
                    className={
                      isOpen
                        ? "history-card open"
                        : "history-card"
                    }
                    key={item.id}
                  >

                    {/* ======================================
                        SUMMARY
                    ====================================== */}

                    <button
                      className="history-main"
                      onClick={() =>
                        setExpanded(
                          isOpen
                            ? null
                            : item.id
                        )
                      }
                    >

                      <div className="history-expand">

                        {isOpen ? (
                          <ChevronDown
                            size={17}
                          />
                        ) : (
                          <ChevronRight
                            size={17}
                          />
                        )}

                      </div>


                      <div className="history-main-icon">
                        <Code2 size={17} />
                      </div>


                      <div className="history-question">

                        <strong>
                          {item.question}
                        </strong>

                        <div className="history-meta">

                          <span>
                            <Database
                              size={13}
                            />

                            {item.database ||
                              "Unknown database"}
                          </span>

                          <span>
                            <Rows3
                              size={13}
                            />

                            {item.row_count} rows
                          </span>

                          <span>
                            <Clock3
                              size={13}
                            />

                            {formatDate(
                              item.timestamp
                            )}
                          </span>

                        </div>

                      </div>


                      <div className="history-card-right">

                        <span className="history-status">
                          <CheckCircle2
                            size={14}
                          />
                          Validated
                        </span>

                        {item.latency_ms != null && (
                          <span className="history-latency">
                            {Math.round(
                              item.latency_ms
                            )} ms
                          </span>
                        )}

                      </div>

                    </button>


                    {/* ======================================
                        DETAILS
                    ====================================== */}

                    {isOpen && (

                      <div className="history-details">

                        {/* METRICS */}

                        <div className="history-detail-grid">

                          <Metric
                            label="Rows Returned"
                            value={String(
                              item.row_count
                            )}
                          />

                          <Metric
                            label="Latency"
                            value={
                              item.latency_ms !=
                              null
                                ? `${Math.round(
                                    item.latency_ms
                                  )} ms`
                                : "—"
                            }
                          />

                          <Metric
                            label="RAG Tables"
                            value={String(
                              item
                                .retrieved_tables
                                ?.length || 0
                            )}
                          />

                          <Metric
                            label="SQL Retries"
                            value={String(
                              item.retries || 0
                            )}
                          />

                        </div>


                        {/* SQL */}

                        <div className="history-detail-section">

                          <div className="history-section-heading">

                            <div>
                              <Code2 size={16} />

                              <span>
                                Generated SQL
                              </span>
                            </div>

                            <span className="history-section-badge">
                              READ ONLY
                            </span>

                          </div>


                          <pre className="history-sql-block">
                            {item.sql ||
                              "No SQL stored."}
                          </pre>

                        </div>


                        {/* RAG */}

                        {item.retrieved_tables?.length > 0 && (

                          <div className="history-detail-section">

                            <div className="history-section-heading">

                              <div>
                                <Table2 size={16} />

                                <span>
                                  Retrieved Schema
                                </span>
                              </div>

                            </div>


                            <div className="history-tags">

                              {item.retrieved_tables.map(
                                (table) => (

                                  <span
                                    className="history-tag"
                                    key={table}
                                  >
                                    <Table2
                                      size={13}
                                    />
                                    {table}
                                  </span>

                                )
                              )}

                            </div>

                          </div>

                        )}


                        {/* RESULT */}

                        {item.result?.length > 0 && (

                          <div className="history-detail-section">

                            <div className="history-section-heading">

                              <div>
                                <Rows3 size={16} />

                                <span>
                                  Result Preview
                                </span>
                              </div>

                              <span className="history-section-badge">
                                TOP 10 ROWS
                              </span>

                            </div>


                            <ResultTable
                              rows={item.result.slice(
                                0,
                                10
                              )}
                            />

                          </div>

                        )}


                        {/* ACTIONS */}

                        <div className="history-actions">

                          <button
                            className="history-rerun-button"
                            onClick={() =>
                              onRerun(item)
                            }
                          >

                            <Play size={16} />

                            Rerun Query

                          </button>


                          <button
                            className="history-delete-button"
                            onClick={() =>
                              void deleteItem(
                                item.id
                              )
                            }
                          >

                            <Trash2 size={16} />

                            Delete

                          </button>

                        </div>

                      </div>

                    )}

                  </article>
                );
              }
            )}

          </div>

        )}

      </section>
    </div>
  );
}


/* ============================================================
   METRIC
============================================================ */

function Metric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="history-metric">

      <span>
        {label}
      </span>

      <strong>
        {value}
      </strong>

    </div>
  );
}


/* ============================================================
   RESULT TABLE
============================================================ */

function ResultTable({
  rows,
}: {
  rows: Record<string, unknown>[];
}) {

  if (!rows.length) {
    return (
      <div className="history-no-result">
        No rows returned.
      </div>
    );
  }

  const columns = Object.keys(
    rows[0] || {}
  );

  return (
    <div className="history-result-table">

      <table>

        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>
                {column}
              </th>
            ))}
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


/* ============================================================
   DATE
============================================================ */

function formatDate(
  timestamp: string
) {

  const date =
    new Date(timestamp);

  if (
    Number.isNaN(
      date.getTime()
    )
  ) {
    return "Unknown time";
  }

  return date.toLocaleString(
    undefined,
    {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }
  );
}