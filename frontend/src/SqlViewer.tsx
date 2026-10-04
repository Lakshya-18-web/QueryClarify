import { useState } from "react";

import { Check, Copy } from "lucide-react";

type SqlViewerProps = {
  sql: string;
};

const keywords = new Set([
  "SELECT",
  "FROM",
  "WHERE",
  "JOIN",
  "INNER",
  "LEFT",
  "RIGHT",
  "OUTER",
  "ON",
  "GROUP",
  "BY",
  "ORDER",
  "HAVING",
  "LIMIT",
  "OFFSET",
  "AS",
  "AND",
  "OR",
  "NOT",
  "IN",
  "IS",
  "NULL",
  "LIKE",
  "BETWEEN",
  "DISTINCT",
  "UNION",
  "ALL",
  "CASE",
  "WHEN",
  "THEN",
  "ELSE",
  "END",
  "ASC",
  "DESC",
  "COUNT",
  "SUM",
  "AVG",
  "MIN",
  "MAX",
  "EXISTS",
  "WITH",
]);

function highlight(sql: string) {
  const parts = sql.split(/(\s+|[(),;.]|'[^']*')/);

  return parts.map((part, index) => {
    if (keywords.has(part.toUpperCase())) {
      return (
        <span key={index} className="sql-keyword">
          {part}
        </span>
      );
    }

    if (part.startsWith("'")) {
      return (
        <span key={index} className="sql-string">
          {part}
        </span>
      );
    }

    if (/^\d+(\.\d+)?$/.test(part)) {
      return (
        <span key={index} className="sql-number">
          {part}
        </span>
      );
    }

    return part;
  });
}

export default function SqlViewer({ sql }: SqlViewerProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="sql-viewer">
      <button className="sql-copy" onClick={() => void copy()}>
        {copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? "Copied" : "Copy"}
      </button>

      <pre className="sql-block">
        <code>{highlight(sql)}</code>
      </pre>
    </div>
  );
}