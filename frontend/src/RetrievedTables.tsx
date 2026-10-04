import { useState } from "react";

import { ChevronDown, ChevronRight, KeyRound, Link2, Loader2, Table2 } from "lucide-react";

import { API_URL, apiFetch } from "./api";

type ColumnInfo = {
  name: string;
  type: string;
  primary_key: boolean;
  foreign_key: boolean;
};

type RetrievedTablesProps = {
  database?: string | null;
  tables: string[];
};

export default function RetrievedTables({
  database,
  tables,
}: RetrievedTablesProps) {
  const [open, setOpen] = useState<string | null>(null);
  const [columns, setColumns] = useState<Record<string, ColumnInfo[]>>({});
  const [loadingTable, setLoadingTable] = useState<string | null>(null);
  const [failed, setFailed] = useState("");

  const toggle = async (table: string) => {
    if (open === table) {
      setOpen(null);
      return;
    }

    setOpen(table);
    setFailed("");

    if (columns[table] || !database) {
      return;
    }

    setLoadingTable(table);

    try {
      const res = await apiFetch(
        `${API_URL}/api/databases/${encodeURIComponent(
          database
        )}/tables/${encodeURIComponent(table)}/schema`
      );

      if (!res.ok) {
        throw new Error("failed");
      }

      const data = await res.json();

      setColumns((prev) => ({
        ...prev,
        [table]: data.columns || [],
      }));
    } catch {
      setFailed("Could not load the columns for this table.");
    } finally {
      setLoadingTable(null);
    }
  };

  if (!tables.length) {
    return <span className="muted">No tables reported.</span>;
  }

  return (
    <div>
      <div className="table-tags">
        {tables.map((table) => (
          <button
            key={table}
            className={open === table ? "table-tag open" : "table-tag"}
            onClick={() => void toggle(table)}
          >
            <Table2 size={14} />
            {table}
            {open === table ? (
              <ChevronDown size={13} />
            ) : (
              <ChevronRight size={13} />
            )}
          </button>
        ))}
      </div>

      {open && (
        <div className="schema-drawer">
          <strong>{open}</strong>

          {loadingTable === open && (
            <div className="drawer-note">
              <Loader2 className="spin" size={14} />
              Loading columns...
            </div>
          )}

          {failed && <div className="drawer-note">{failed}</div>}

          {columns[open] && (
            <ul>
              {columns[open].map((column) => (
                <li key={column.name}>
                  <span>{column.name}</span>
                  <small>{column.type}</small>
                  {column.primary_key && (
                    <em className="key-badge primary">
                      <KeyRound size={10} />
                      PK
                    </em>
                  )}
                  {column.foreign_key && (
                    <em className="key-badge foreign">
                      <Link2 size={10} />
                      FK
                    </em>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}