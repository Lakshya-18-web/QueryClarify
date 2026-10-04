import { useCallback, useEffect, useRef, useState } from "react";

import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Database,
  FileUp,
  Library,
  Link2,
  Loader2,
  Play,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  apiFetch,
  API_URL,
  readError,
  type UploadLimits,
  type UploadResult,
  type UserDatabase,
} from "./api";

import "./upload.css";

type Props = {
  onUseDatabase: (id: string, label: string) => void;
  onOpenLibrary: () => void;
  onChanged?: () => void;
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function daysLeft(expires: string | null): string | null {
  if (!expires) return null;

  const ms = new Date(expires).getTime() - Date.now();

  if (ms <= 0) return "expiring";

  const days = Math.ceil(ms / 86_400_000);

  return days === 1 ? "1 day left" : `${days} days left`;
}

export default function DatabaseUpload({
  onUseDatabase,
  onOpenLibrary,
  onChanged,
}: Props) {
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [databases, setDatabases] = useState<UserDatabase[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [builtinCount, setBuiltinCount] = useState<number | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [limitRes, listRes] = await Promise.all([
        apiFetch(`${API_URL}/api/databases/limits`),
        apiFetch(`${API_URL}/api/databases`),
      ]);

      if (limitRes.ok) setLimits(await limitRes.json());

      if (listRes.ok) {
        const data = await listRes.json();
        setDatabases(data.user_databases ?? []);

        if (typeof data.count === "number") {
          setBuiltinCount(data.count);
        } else if (Array.isArray(data.databases)) {
          setBuiltinCount(data.databases.length);
        }
      }
    } catch {
      setError("Unable to reach the server.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const accepted = limits?.accepted_extensions ?? [
    ".sqlite",
    ".sqlite3",
    ".db",
    ".csv",
    ".tsv",
    ".xlsx",
  ];

  const addFiles = (incoming: FileList | File[]) => {
    setError("");
    setResult(null);

    const list = Array.from(incoming);
    const bad = list.find(
      (f) => !accepted.some((ext) => f.name.toLowerCase().endsWith(ext))
    );

    if (bad) {
      setError(
        `"${bad.name}" is not supported. Accepted: ${accepted.join(", ")}`
      );
      return;
    }

    const maxBytes = (limits?.max_upload_mb ?? 50) * 1024 * 1024;
    const total = list.reduce((sum, f) => sum + f.size, 0);

    if (total > maxBytes) {
      setError(`Files are too large (maximum ${limits?.max_upload_mb ?? 50} MB).`);
      return;
    }

    setFiles(list);

    if (!name && list.length === 1) {
      setName(list[0].name.replace(/\.[^.]+$/, ""));
    }
  };

  const upload = async () => {
    if (!files.length || uploading) return;

    setUploading(true);
    setError("");
    setResult(null);

    try {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));

      if (name.trim()) form.append("name", name.trim());

      const res = await apiFetch(`${API_URL}/api/databases/upload`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        throw new Error(await readError(res));
      }

      const data: UploadResult = await res.json();

      setResult(data);
      setFiles([]);
      setName("");

      if (inputRef.current) inputRef.current.value = "";

      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (db: UserDatabase) => {
    if (
      !window.confirm(
        `Delete "${db.display_name}"? This permanently removes the data.`
      )
    ) {
      return;
    }

    setDeleting(db.id);
    setError("");

    try {
      const res = await apiFetch(
        `${API_URL}/api/databases/${encodeURIComponent(db.id)}`,
        { method: "DELETE" }
      );

      if (!res.ok) throw new Error(await readError(res));

      if (result?.database.id === db.id) setResult(null);

      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <section className="upload-page">
      <div className="landing-hero">
        <span className="hero-tag">
          <Sparkles size={14} />
          AI DATABASE ASSISTANT
        </span>

        <h1>
          Ask your data <span>anything.</span>
        </h1>

        <p>
          Connect a database. Ask questions in natural language. Get validated
          SQL and clear answers.
        </p>
      </div>

      <div className="choice-grid">
        <div className="choice-card">
          <div className="choice-head">
            <div className="choice-icon">
              <FileUp size={22} />
            </div>

            <div>
              <h3>Upload Your Database</h3>
              <p>Bring your own data into QueryClarify.</p>
            </div>
          </div>

          <div
            className={`dropzone${dragging ? " dragging" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              addFiles(e.dataTransfer.files);
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
            }}
          >
            <FileUp size={24} />

            <strong>
              {files.length
                ? files.map((f) => f.name).join(", ")
                : "Drop files here or click to browse"}
            </strong>

            <span>
              {accepted.join("  ")}
              {limits
                ? ` · up to ${limits.max_upload_mb} MB · ${limits.max_databases_per_user} databases`
                : ""}
            </span>

            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              accept={accepted.join(",")}
              onChange={(e) => e.target.files && addFiles(e.target.files)}
            />
          </div>

          <div className="upload-row">
            <input
              className="upload-name"
              placeholder="Database name (optional)"
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
            />

            <button
              className="run-button"
              disabled={!files.length || uploading}
              onClick={() => void upload()}
            >
              {uploading ? (
                <>
                  <Loader2 className="spin" size={18} />
                  Processing…
                </>
              ) : (
                <>
                  <FileUp size={18} />
                  Upload &amp; index
                </>
              )}
            </button>
          </div>

          <p className="upload-hint">
            Several CSV/Excel files become one database with one table per file
            or sheet; relationships between them are detected automatically.
            Your databases are private to this browser
            {limits?.retention_days
              ? ` and deleted automatically after ${limits.retention_days} days`
              : ""}
            .
          </p>

          {error && (
            <div className="upload-alert error" role="alert">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="choice-card library-choice">
          <div className="choice-head">
            <div className="choice-icon alt">
              <Library size={22} />
            </div>

            <div>
              <h3>QueryClarify Database Library</h3>
              <p>Don&apos;t have a database? Start with ours.</p>
            </div>
          </div>

          <div className="library-count">
            <strong>{builtinCount ?? "—"}</strong>
            <span>built-in databases ready to query</span>
          </div>

          <p className="upload-hint">
            Browse the collection, pick a database and start asking questions
            right away. No upload needed.
          </p>

          <button className="run-button wide" onClick={onOpenLibrary}>
            Explore Databases
            <ArrowRight size={17} />
          </button>
        </div>
      </div>

      {result && (
        <div className="upload-card success">
          <div className="upload-alert ok">
            <CheckCircle2 size={16} />
            <span>
              <strong>{result.database.display_name}</strong> is ready:{" "}
              {result.tables.length} tables,{" "}
              {result.database.row_count.toLocaleString()} rows.
            </span>
          </div>

          <div className="upload-table-wrap">
            <table className="upload-table">
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Columns</th>
                  <th>Rows</th>
                  <th>Relationships</th>
                </tr>
              </thead>
              <tbody>
                {result.tables.map((t) => (
                  <tr key={t.name}>
                    <td>
                      {t.name}
                      {t.original_name !== t.name && (
                        <small> (was “{t.original_name}”)</small>
                      )}
                    </td>
                    <td>{t.columns}</td>
                    <td>{t.rows.toLocaleString()}</td>
                    <td>
                      {t.relationships.length === 0
                        ? "—"
                        : t.relationships.map((r) => (
                            <span className="rel" key={r.column}>
                              <Link2 size={11} />
                              {r.column} → {r.references}
                              {r.inferred && <em> detected</em>}
                            </span>
                          ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.warnings.map((w) => (
            <div className="upload-alert warn" key={w}>
              <AlertCircle size={16} />
              <span>{w}</span>
            </div>
          ))}

          <button
            className="run-button use-now"
            onClick={() =>
              onUseDatabase(result.database.id, result.database.display_name)
            }
          >
            <Play size={16} />
            Start asking questions
          </button>
        </div>
      )}

      <div className="my-databases">
        <div className="section-row">
          <h3 className="upload-subtitle">My Databases</h3>
          <span className="count-chip">{databases.length}</span>
        </div>

        {databases.length === 0 ? (
          <div className="empty-databases">
            <div className="empty-logo">
              <Database size={26} />
            </div>

            <h4>No databases yet.</h4>

            <p>
              Upload your first database or explore the QueryClarify library.
            </p>

            <div className="empty-actions">
              <button
                className="run-button"
                onClick={() => inputRef.current?.click()}
              >
                <FileUp size={16} />
                Upload Database
              </button>

              <button className="ghost-button" onClick={onOpenLibrary}>
                <Library size={16} />
                Explore Library
              </button>
            </div>
          </div>
        ) : (
          <div className="db-grid">
            {databases.map((db) => (
              <div className="db-card" key={db.id}>
                <div className="db-card-top">
                  <div className="db-card-icon">
                    <Database size={18} />
                  </div>

                  <button
                    className="db-delete"
                    disabled={deleting === db.id}
                    onClick={() => void remove(db)}
                    aria-label={`Delete ${db.display_name}`}
                  >
                    {deleting === db.id ? (
                      <Loader2 className="spin" size={15} />
                    ) : (
                      <Trash2 size={15} />
                    )}
                  </button>
                </div>

                <strong title={db.display_name}>{db.display_name}</strong>

                <span className="db-type">{db.source_type}</span>

                <span className="db-stats">
                  {db.table_count} tables • {db.row_count.toLocaleString()} rows
                  • {formatBytes(db.size_bytes)}
                </span>

                {daysLeft(db.expires_at) && (
                  <span className="db-expiry">{daysLeft(db.expires_at)}</span>
                )}

                <button
                  className="db-use"
                  onClick={() => onUseDatabase(db.id, db.display_name)}
                >
                  <Play size={14} />
                  Open Database
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}