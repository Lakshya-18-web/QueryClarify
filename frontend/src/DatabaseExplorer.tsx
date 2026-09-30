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


const API_URL =
  "http://127.0.0.1:8000";


type DatabaseExplorerProps = {
  onUseDatabase?: (
    database: string
  ) => void;
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

  const [
    databases,
    setDatabases,
  ] = useState<string[]>([]);


  const [
    selectedDatabase,
    setSelectedDatabase,
  ] = useState<string | null>(
    null
  );


  const [
    tables,
    setTables,
  ] = useState<string[]>([]);


  const [
    selectedTable,
    setSelectedTable,
  ] = useState<string | null>(
    null
  );


  const [
    tableSchema,
    setTableSchema,
  ] = useState<TableSchema | null>(
    null
  );


  const [
    search,
    setSearch,
  ] = useState("");


  const [
    loadingDatabases,
    setLoadingDatabases,
  ] = useState(true);


  const [
    loadingTables,
    setLoadingTables,
  ] = useState(false);


  const [
    loadingSchema,
    setLoadingSchema,
  ] = useState(false);


  const [
    error,
    setError,
  ] = useState("");


  /*
   * =========================================================
   * LOAD DATABASES
   * =========================================================
   */

  useEffect(() => {

    loadDatabases();

  }, []);


  const loadDatabases =
    async () => {

      try {

        setLoadingDatabases(
          true
        );

        setError("");

        const response =
          await fetch(
            `${API_URL}/api/databases`
          );

        if (!response.ok) {

          throw new Error(
            "Unable to load databases."
          );

        }

        const data:
          DatabaseResponse =
          await response.json();

        setDatabases(
          data.databases || []
        );

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Unable to load databases."
        );

      } finally {

        setLoadingDatabases(
          false
        );

      }

    };


  /*
   * =========================================================
   * SELECT DATABASE
   * =========================================================
   */

  const selectDatabase =
    async (
      database: string
    ) => {

      try {

        setSelectedDatabase(
          database
        );

        setSelectedTable(
          null
        );

        setTableSchema(
          null
        );

        setTables([]);

        setLoadingTables(
          true
        );

        setError("");

        const response =
          await fetch(
            `${API_URL}/api/databases/${encodeURIComponent(
              database
            )}/tables`
          );

        if (!response.ok) {

          throw new Error(
            `Unable to load tables for ${database}.`
          );

        }

        const data:
          TablesResponse =
          await response.json();

        setTables(
          data.tables || []
        );

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Unable to load tables."
        );

      } finally {

        setLoadingTables(
          false
        );

      }

    };


  /*
   * =========================================================
   * SELECT TABLE
   * =========================================================
   */

  const selectTable =
    async (
      table: string
    ) => {

      if (!selectedDatabase) {
        return;
      }

      try {

        setSelectedTable(
          table
        );

        setTableSchema(
          null
        );

        setLoadingSchema(
          true
        );

        setError("");

        const response =
          await fetch(
            `${API_URL}/api/databases/${encodeURIComponent(
              selectedDatabase
            )}/tables/${encodeURIComponent(
              table
            )}/schema`
          );

        if (!response.ok) {

          const data =
            await response.json();

          throw new Error(
            data.detail ||
            `Unable to load schema for ${table}.`
          );

        }

        const data:
          TableSchema =
          await response.json();

        setTableSchema(
          data
        );

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Unable to load table schema."
        );

      } finally {

        setLoadingSchema(
          false
        );

      }

    };


  /*
   * =========================================================
   * SEARCH
   * =========================================================
   */

  const filteredDatabases =
    useMemo(() => {

      const value =
        search
          .trim()
          .toLowerCase();

      if (!value) {
        return databases;
      }

      return databases.filter(
        (database) =>
          database
            .toLowerCase()
            .includes(value)
      );

    }, [
      databases,
      search,
    ]);


  /*
   * =========================================================
   * USE DATABASE
   * =========================================================
   */

  const useDatabase = () => {

    if (
      selectedDatabase &&
      onUseDatabase
    ) {

      onUseDatabase(
        selectedDatabase
      );

    }

  };


  /*
   * =========================================================
   * BACK TO TABLES
   * =========================================================
   */

  const backToTables = () => {

    setSelectedTable(
      null
    );

    setTableSchema(
      null
    );

    setError("");

  };


  /*
   * =========================================================
   * BACK TO DATABASES
   * =========================================================
   */

  const backToDatabases =
    () => {

      setSelectedDatabase(
        null
      );

      setSelectedTable(
        null
      );

      setTableSchema(
        null
      );

      setTables([]);

      setError("");

    };


  /*
   * =========================================================
   * UI
   * =========================================================
   */

  return (

    <div className="explorer-page">

      {/* HEADER */}

      <div className="explorer-header">

        <div>

          <span className="section-tag">
            SCHEMA EXPLORER
          </span>

          <h2>
            Explore your databases
          </h2>

          <p>
            Browse databases and tables
            available to QueryClarify.
          </p>

        </div>


        <div className="database-count">

          <Database size={15} />

          {databases.length} databases

        </div>

      </div>


      {/* ERROR */}

      {error && (

        <div className="explorer-error">

          <AlertCircle
            size={16}
          />

          {error}

        </div>

      )}


      {/* EXPLORER */}

      <div className="explorer-layout">


        {/* =====================================================
            DATABASE PANEL
        ===================================================== */}

        <aside className="database-panel">

          <div className="panel-heading">

            <div>

              <span>
                DATABASES
              </span>

              <strong>
                {databases.length}
              </strong>

            </div>

          </div>


          <div className="search-box">

            <Search size={15} />

            <input
              value={search}
              onChange={(e) =>
                setSearch(
                  e.target.value
                )
              }
              placeholder="Search databases..."
            />

          </div>


          <div className="database-list">

            {loadingDatabases ? (

              <div className="explorer-loading">

                <Loader2
                  size={17}
                  className="spin"
                />

                Loading databases...

              </div>

            ) : (

              filteredDatabases.map(
                (database) => (

                  <button
                    key={database}
                    className={
                      selectedDatabase ===
                      database
                        ? "database-row selected"
                        : "database-row"
                    }
                    onClick={() =>
                      selectDatabase(
                        database
                      )
                    }
                  >

                    <Database
                      size={15}
                    />

                    <span>
                      {database}
                    </span>

                  </button>

                )
              )

            )}

          </div>

        </aside>


        {/* =====================================================
            RIGHT PANEL
        ===================================================== */}

        <section className="tables-panel">


          {/* NO DATABASE */}

          {!selectedDatabase && (

            <div className="empty-explorer">

              <Database
                size={35}
              />

              <h3>
                Select a database
              </h3>

              <p>
                Choose a database from
                the left panel to explore
                its tables and schema.
              </p>

            </div>

          )}


          {/* DATABASE SELECTED */}

          {selectedDatabase &&
            !selectedTable && (

            <>

              <div className="tables-header">

                <div>

                  <button
                    className="back-button"
                    onClick={
                      backToDatabases
                    }
                  >

                    <ArrowLeft
                      size={13}
                    />

                    All databases

                  </button>

                  <h3>
                    {selectedDatabase}
                  </h3>

                  <span>
                    {tables.length} tables
                  </span>

                </div>


                <button
                  className="use-database-button"
                  onClick={
                    useDatabase
                  }
                >

                  <Play size={13} />

                  Use Database

                </button>

              </div>


              {loadingTables ? (

                <div className="explorer-loading">

                  <Loader2
                    size={20}
                    className="spin"
                  />

                  Loading tables...

                </div>

              ) : (

                <div className="table-grid">

                  {tables.map(
                    (table) => (

                      <button
                        key={table}
                        className="table-card"
                        onClick={() =>
                          selectTable(
                            table
                          )
                        }
                      >

                        <div className="table-icon">

                          <Table2
                            size={17}
                          />

                        </div>

                        <div>

                          <strong>
                            {table}
                          </strong>

                          <span>
                            Click to inspect schema
                          </span>

                        </div>

                      </button>

                    )
                  )}

                </div>

              )}

            </>

          )}


          {/* ===================================================
              TABLE SCHEMA
          =================================================== */}

          {selectedDatabase &&
            selectedTable && (

            <>

              <div className="tables-header">

                <div>

                  <button
                    className="back-button"
                    onClick={
                      backToTables
                    }
                  >

                    <ArrowLeft
                      size={13}
                    />

                    {selectedDatabase}

                  </button>

                  <h3>
                    {selectedTable}
                  </h3>

                  <span>
                    {tableSchema
                      ?.column_count || 0}{" "}
                    columns
                  </span>

                </div>


                <button
                  className="use-database-button"
                  onClick={
                    useDatabase
                  }
                >

                  <Play size={13} />

                  Use Database

                </button>

              </div>


              {loadingSchema ? (

                <div className="explorer-loading">

                  <Loader2
                    size={20}
                    className="spin"
                  />

                  Loading schema...

                </div>

              ) : tableSchema ? (

                <div className="schema-view">


                  {/* TABLE SUMMARY */}

                  <div className="schema-summary">

                    <div className="schema-summary-card">

                      <Table2
                        size={18}
                      />

                      <div>

                        <span>
                          TABLE
                        </span>

                        <strong>
                          {tableSchema.table}
                        </strong>

                      </div>

                    </div>


                    <div className="schema-summary-card">

                      <KeyRound
                        size={18}
                      />

                      <div>

                        <span>
                          PRIMARY KEYS
                        </span>

                        <strong>
                          {tableSchema
                            .primary_keys
                            .length}

                        </strong>

                      </div>

                    </div>


                    <div className="schema-summary-card">

                      <Link2
                        size={18}
                      />

                      <div>

                        <span>
                          FOREIGN KEYS
                        </span>

                        <strong>
                          {tableSchema
                            .foreign_keys
                            .length}

                        </strong>

                      </div>

                    </div>

                  </div>


                  {/* COLUMNS */}

                  <div className="columns-card">

                    <div className="columns-card-header">

                      <div>

                        <span className="section-tag">
                          TABLE SCHEMA
                        </span>

                        <h3>
                          Columns
                        </h3>

                      </div>

                      <span>
                        {tableSchema.column_count}{" "}
                        columns
                      </span>

                    </div>


                    <div className="schema-table-wrapper">

                      <table className="schema-table">

                        <thead>

                          <tr>

                            <th>
                              Column
                            </th>

                            <th>
                              Type
                            </th>

                            <th>
                              Nullable
                            </th>

                            <th>
                              Key
                            </th>

                            <th>
                              References
                            </th>

                          </tr>

                        </thead>


                        <tbody>

                          {tableSchema.columns.map(
                            (column) => (

                              <tr
                                key={
                                  column.name
                                }
                              >

                                <td>

                                  <div className="column-name">

                                    <Circle
                                      size={6}
                                      fill="currentColor"
                                    />

                                    <strong>
                                      {column.name}
                                    </strong>

                                  </div>

                                </td>


                                <td>

                                  <span className="type-badge">

                                    {column.type}

                                  </span>

                                </td>


                                <td>

                                  {column.nullable
                                    ? (
                                      <span className="nullable">
                                        YES
                                      </span>
                                    )
                                    : (
                                      <span className="not-null">
                                        NO
                                      </span>
                                    )}

                                </td>


                                <td>

                                  {column.primary_key && (

                                    <span className="key-badge primary">

                                      <KeyRound
                                        size={11}
                                      />

                                      PK

                                    </span>

                                  )}


                                  {column.foreign_key && (

                                    <span className="key-badge foreign">

                                      <Link2
                                        size={11}
                                      />

                                      FK

                                    </span>

                                  )}


                                  {!column.primary_key &&
                                    !column.foreign_key && (
                                      <span className="no-key">
                                        —
                                      </span>
                                    )}

                                </td>


                                <td>

                                  {column.foreign_key ? (

                                    <span className="reference-text">

                                      {column.references_table}
                                      <span>
                                        .
                                      </span>
                                      {column.references_column}

                                    </span>

                                  ) : (

                                    <span className="no-key">
                                      —
                                    </span>

                                  )}

                                </td>

                              </tr>

                            )
                          )}

                        </tbody>

                      </table>

                    </div>

                  </div>


                  {/* FOREIGN KEY RELATIONSHIPS */}

                  {tableSchema.foreign_keys.length >
                    0 && (

                    <div className="relationships-card">

                      <div>

                        <span className="section-tag">
                          RELATIONSHIPS
                        </span>

                        <h3>
                          Foreign key connections
                        </h3>

                      </div>


                      <div className="relationship-list">

                        {tableSchema.foreign_keys.map(
                          (
                            fk,
                            index
                          ) => (

                            <div
                              className="relationship-row"
                              key={
                                `${fk.column}-${index}`
                              }
                            >

                              <span className="relationship-column">

                                {fk.column}

                              </span>

                              <span className="relationship-arrow">
                                →
                              </span>

                              <span className="relationship-target">

                                {fk.references_table}
                                .
                                {fk.references_column}

                              </span>

                            </div>

                          )
                        )}

                      </div>

                    </div>

                  )}

                </div>

              ) : null}

            </>

          )}

        </section>

      </div>

    </div>

  );
}