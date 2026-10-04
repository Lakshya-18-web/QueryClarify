import { useEffect, useMemo, useState } from "react";

import {
  Database,
  Search,
  Table2,
  ArrowLeft,
  Play,
  Loader2,
  AlertCircle,
  KeyRound,
  Link2,
  Circle,
} from "lucide-react";

import { API_URL, apiFetch } from "./api";

type DatabaseExplorerProps = {
  onUseDatabase?: (database: string) => void;
};

type DatabaseResponse = {
  count: number;
  databases: string[];
};

type TablesResponse = {
  database: string;
  count: number;
  tables: string[];
};

type Column = {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  foreign_key: boolean;
  references_table: string | null;
  references_column: string | null;
};

type TableSchema = {
  database: string;
  table: string;
  column_count: number;
  columns: Column[];
  primary_keys: string[];
  foreign_keys: {
    column: string;
    references_table: string | null;
    references_column: string | null;
  }[];
};

export default function DatabaseExplorer({
  onUseDatabase,
}: DatabaseExplorerProps) {
  const [databases, setDatabases] = useState<string[]>([]);
  const [selectedDatabase, setSelectedDatabase] = useState<string | null>(null);
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [tableSchema, setTableSchema] = useState<TableSchema | null>(null);
  const [search, setSearch] = useState("");
  const [loadingDatabases, setLoadingDatabases] = useState(true);
  const [loadingTables, setLoadingTables] = useState(false);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadDatabases();
  }, []);

  const loadDatabases = async () => {
    try {
      setLoadingDatabases(true);
      setError("");

      const response = await apiFetch(`${API_URL}/api/databases`);

      if (!response.ok) {
        throw new Error("Unable to load databases.");
      }

      const data: DatabaseResponse = await response.json();

      setDatabases(data.databases || []);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load databases."
      );
    } finally {
      setLoadingDatabases(false);
    }
  };

  const selectDatabase = async (database: string) => {
    try {
      setSelectedDatabase(database);
      setSelectedTable(null);
      setTableSchema(null);
      setTables([]);
      setLoadingTables(true);
      setError("");

      const response = await apiFetch(
        `${API_URL}/api/databases/${encodeURIComponent(database)}/tables`
      );

      if (!response.ok) {
        throw new Error(`Unable to load tables for ${database}.`);
      }

      const data: TablesResponse = await response.json();

      setTables(data.tables || []);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load tables."
      );
    } finally {
      setLoadingTables(false);
    }
  };

  const selectTable = async (table: string) => {
    if (!selectedDatabase) {
      return;
    }

    try {
      setSelectedTable(table);
      setTableSchema(null);
      setLoadingSchema(true);
      setError("");

      const response = await apiFetch(
        `${API_URL}/api/databases/${encodeURIComponent(
          selectedDatabase
        )}/tables/${encodeURIComponent(table)}/schema`
      );

      if (!response.ok) {
        const data = await response.json();

        throw new Error(data.detail || `Unable to load schema for ${table}.`);
      }

      const data: TableSchema = await response.json();

      setTableSchema(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load table schema."
      );
    } finally {
      setLoadingSchema(false);
    }
  };

  const filteredDatabases = useMemo(() => {
    const value = search.trim().toLowerCase();

    if (!value) {
      return databases;
    }

    return databases.filter((database) =>
      database.toLowerCase().includes(value)
    );
  }, [databases, search]);

  const useDatabase = () => {
    if (selectedDatabase && onUseDatabase) {
      onUseDatabase(selectedDatabase);
    }
  };

  const backToTables = () => {
    setSelectedTable(null);
    setTableSchema(null);
    setError("");
  };

  const backToDatabases = () => {
    setSelectedDatabase(null);
    setSelectedTable(null);
    setTableSchema(null);
    setTables([]);
    setError("");
  };

  return (
    <div className="explorer-page">
      <div className="explorer-header">
        <div>
          <span className="section-tag">DATABASE LIBRARY</span>

          <h2>QueryClarify Database Library</h2>

          <p>Browse the built-in databases and pick one to start querying.</p>
        </div>

        <div className="database-count">
          <Database size={15} />
          {databases.length} databases
        </div>
      </div>

      {error && (
        <div className="explorer-error">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      <div className="explorer-layout">
        {!selectedDatabase && (
          <div className="database-panel">
            <div className="search-box">
              <Search size={16} />

              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${databases.length || ""} databases...`}
              />
            </div>

            {loadingDatabases ? (
              <div className="explorer-loading">
                <Loader2 size={17} className="spin" />
                Loading databases...
              </div>
            ) : filteredDatabases.length === 0 ? (
              <div className="explorer-loading">
                No databases match your search.
              </div>
            ) : (
              <div className="database-list">
                {filteredDatabases.map((database) => (
                  <button
                    key={database}
                    className="database-row"
                    onClick={() => selectDatabase(database)}
                  >
                    <Database size={15} />
                    <span>{database}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {selectedDatabase && !selectedTable && (
          <section className="tables-panel">
            <div className="tables-header">
              <div>
                <button className="back-button" onClick={backToDatabases}>
                  <ArrowLeft size={13} />
                  All databases
                </button>

                <h3>{selectedDatabase}</h3>

                <span>{tables.length} tables</span>
              </div>

              <button className="use-database-button" onClick={useDatabase}>
                <Play size={13} />
                Use This Database
              </button>
            </div>

            {loadingTables ? (
              <div className="explorer-loading">
                <Loader2 size={20} className="spin" />
                Loading tables...
              </div>
            ) : (
              <div className="table-grid">
                {tables.map((table) => (
                  <button
                    key={table}
                    className="table-card"
                    onClick={() => selectTable(table)}
                  >
                    <div className="table-icon">
                      <Table2 size={17} />
                    </div>

                    <div>
                      <strong>{table}</strong>

                      <span>Click to inspect schema</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        {selectedDatabase && selectedTable && (
          <section className="tables-panel">
            <div className="tables-header">
              <div>
                <button className="back-button" onClick={backToTables}>
                  <ArrowLeft size={13} />
                  {selectedDatabase}
                </button>

                <h3>{selectedTable}</h3>

                <span>{tableSchema?.column_count || 0} columns</span>
              </div>

              <button className="use-database-button" onClick={useDatabase}>
                <Play size={13} />
                Use This Database
              </button>
            </div>

            {loadingSchema ? (
              <div className="explorer-loading">
                <Loader2 size={20} className="spin" />
                Loading schema...
              </div>
            ) : tableSchema ? (
              <div className="schema-view">
                <div className="schema-summary">
                  <div className="schema-summary-card">
                    <Table2 size={18} />

                    <div>
                      <span>TABLE</span>
                      <strong>{tableSchema.table}</strong>
                    </div>
                  </div>

                  <div className="schema-summary-card">
                    <KeyRound size={18} />

                    <div>
                      <span>PRIMARY KEYS</span>
                      <strong>{tableSchema.primary_keys.length}</strong>
                    </div>
                  </div>

                  <div className="schema-summary-card">
                    <Link2 size={18} />

                    <div>
                      <span>FOREIGN KEYS</span>
                      <strong>{tableSchema.foreign_keys.length}</strong>
                    </div>
                  </div>
                </div>

                <div className="columns-card">
                  <div className="columns-card-header">
                    <div>
                      <span className="section-tag">TABLE SCHEMA</span>
                      <h3>Columns</h3>
                    </div>

                    <span>{tableSchema.column_count} columns</span>
                  </div>

                  <div className="schema-table-wrapper">
                    <table className="schema-table">
                      <thead>
                        <tr>
                          <th>Column</th>
                          <th>Type</th>
                          <th>Nullable</th>
                          <th>Key</th>
                          <th>References</th>
                        </tr>
                      </thead>

                      <tbody>
                        {tableSchema.columns.map((column) => (
                          <tr key={column.name}>
                            <td>
                              <div className="column-name">
                                <Circle size={6} fill="currentColor" />
                                <strong>{column.name}</strong>
                              </div>
                            </td>

                            <td>
                              <span className="type-badge">{column.type}</span>
                            </td>

                            <td>
                              {column.nullable ? (
                                <span className="nullable">YES</span>
                              ) : (
                                <span className="not-null">NO</span>
                              )}
                            </td>

                            <td>
                              {column.primary_key && (
                                <span className="key-badge primary">
                                  <KeyRound size={11} />
                                  PK
                                </span>
                              )}

                              {column.foreign_key && (
                                <span className="key-badge foreign">
                                  <Link2 size={11} />
                                  FK
                                </span>
                              )}

                              {!column.primary_key && !column.foreign_key && (
                                <span className="no-key">—</span>
                              )}
                            </td>

                            <td>
                              {column.foreign_key ? (
                                <span className="reference-text">
                                  {column.references_table}.
                                  {column.references_column}
                                </span>
                              ) : (
                                <span className="no-key">—</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {tableSchema.foreign_keys.length > 0 && (
                  <div className="relationships-card">
                    <div>
                      <span className="section-tag">RELATIONSHIPS</span>
                      <h3>Foreign key connections</h3>
                    </div>

                    <div className="relationship-list">
                      {tableSchema.foreign_keys.map((fk, index) => (
                        <div
                          className="relationship-row"
                          key={`${fk.column}-${index}`}
                        >
                          <span className="relationship-column">
                            {fk.column}
                          </span>

                          <span className="relationship-arrow">→</span>

                          <span className="relationship-target">
                            {fk.references_table}.{fk.references_column}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : null}
          </section>
        )}
      </div>
    </div>
  );
}