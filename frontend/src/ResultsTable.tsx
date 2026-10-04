import { useState } from "react";

import { ChevronLeft, ChevronRight } from "lucide-react";

type ResultsTableProps = {
  rows: Record<string, unknown>[];
};

const pageSize = 20;

export default function ResultsTable({ rows }: ResultsTableProps) {
  const [page, setPage] = useState(0);

  const columns = Object.keys(rows[0] || {});
  const pageCount = Math.ceil(rows.length / pageSize);
  const visible = rows.slice(page * pageSize, page * pageSize + pageSize);

  const copyValue = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      return;
    }
  };

  return (
    <div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>

          <tbody>
            {visible.map((row, index) => (
              <tr key={page * pageSize + index}>
                {columns.map((column) => {
                  const text = String(row[column] ?? "NULL");

                  return (
                    <td
                      key={column}
                      title="Click to copy"
                      onClick={() => void copyValue(text)}
                    >
                      {text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        <span>
          {rows.length.toLocaleString()} {rows.length === 1 ? "row" : "rows"}
        </span>

        {pageCount > 1 && (
          <div className="pager">
            <button
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
              aria-label="Previous page"
            >
              <ChevronLeft size={15} />
            </button>

            <span>
              Page {page + 1} of {pageCount}
            </span>

            <button
              disabled={page >= pageCount - 1}
              onClick={() => setPage(page + 1)}
              aria-label="Next page"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}