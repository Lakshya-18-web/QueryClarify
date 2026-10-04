/**
 * Shared API helpers.
 *
 * Every request carries an anonymous, random client id. The server stores
 * only a salted hash of it and uses it to decide which uploaded databases
 * and history entries belong to this browser. Once you add real user
 * accounts, replace getClientId() with the authenticated user's id/token.
 */

export const API_URL: string =
  (import.meta.env?.VITE_API_URL as string | undefined) ??
  "http://127.0.0.1:8000";

const CLIENT_ID_KEY = "queryclarify.clientId";

function randomId(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);

  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function getClientId(): string {
  try {
    const existing = localStorage.getItem(CLIENT_ID_KEY);

    if (existing && /^[A-Za-z0-9_-]{16,128}$/.test(existing)) {
      return existing;
    }

    const created = randomId();
    localStorage.setItem(CLIENT_ID_KEY, created);

    return created;
  } catch {
    // Storage blocked (private mode): fall back to a per-page-load id.
    return memoryId;
  }
}

const memoryId = randomId();

export function apiFetch(
  input: string,
  init: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Client-Id", getClientId());

  return fetch(input, { ...init, headers });
}

/* ---------- shared types ---------- */

export type UserDatabase = {
  id: string;
  display_name: string;
  dialect: string;
  status: string;
  source_type: string;
  table_count: number;
  row_count: number;
  size_bytes: number;
  created_at: string;
  expires_at: string | null;
};

export type UploadLimits = {
  max_upload_mb: number;
  max_files: number;
  max_tables: number;
  max_rows_per_table: number;
  max_databases_per_user: number;
  max_storage_mb_per_user: number;
  retention_days: number | null;
  accepted_extensions: string[];
};

export type UploadResult = {
  database: UserDatabase;
  tables: {
    name: string;
    original_name: string;
    columns: number;
    rows: number;
    relationships: { column: string; references: string; inferred: boolean }[];
  }[];
  warnings: string[];
};

export async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();

    if (typeof body?.detail === "string") {
      return body.detail;
    }

    if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
      return String(body.detail[0].msg);
    }
  } catch {
    /* not JSON */
  }

  return `Request failed (${res.status}).`;
}
