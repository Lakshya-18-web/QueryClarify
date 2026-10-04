import { useEffect, useState } from "react";

import {
  Activity,
  Bell,
  Clock3,
  Cloud,
  Database,
  HeartPulse,
  Loader2,
  RefreshCw,
  Rows3,
} from "lucide-react";

import { API_URL, apiFetch } from "./api";

type Entry = {
  id: number;
  database?: string | null;
  latency_ms?: number | null;
  row_count: number;
  retries: number;
  timestamp: string;
};

const planned = [
  {
    title: "Database health checks",
    text: "Connectivity and availability of your databases.",
    icon: HeartPulse,
  },
  {
    title: "Live query monitoring",
    text: "Streaming query activity across sessions.",
    icon: Activity,
  },
  {
    title: "Alerts",
    text: "Notifications for slow or failing queries.",
    icon: Bell,
  },
];

export default function CloudWatch() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");

    try {
      const res = await apiFetch(`${API_URL}/api/history`);

      if (!res.ok) {
        throw new Error("Unable to load query activity.");
      }

      const data = await res.json();

      setEntries(Array.isArray(data.history) ? data.history : []);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load query activity."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const totalRows = entries.reduce((sum, e) => sum + (e.row_count || 0), 0);

  const withLatency = entries.filter((e) => e.latency_ms != null);

  const averageLatency = withLatency.length
    ? Math.round(
        withLatency.reduce((sum, e) => sum + (e.latency_ms || 0), 0) /
          withLatency.length
      )
    : 0;

  const databases = new Set(
    entries.map((e) => e.database).filter((d): d is string => Boolean(d))
  );

  const recent = entries.slice(0, 12).reverse();

  const maxLatency = Math.max(1, ...recent.map((e) => e.latency_ms || 0));

  return (
    <div className="cw-page">
      <header className="topbar">
        <div>
          <p className="eyebrow">MONITORING</p>
          <h2>Cloud Watch</h2>
        </div>

        <button className="reset-button" onClick={() => void load()}>
          <RefreshCw size={15} />
          Refresh
        </button>
      </header>

      <section className="cw-content">
        <div className="cw-hero">
          <div className="cw-hero-icon">
            <Cloud size={24} />
          </div>

          <div>
            <h3>Query activity</h3>
            <p>
              Figures below are calculated from your saved query history.
              Infrastructure monitoring is not connected to a backend yet.
            </p>
          </div>

          <span className="live-badge">From query history</span>
        </div>

        {error && (
          <div className="error-box">
            <div>
              <strong>Could not load activity</strong>
              <p>{error}</p>
            </div>
          </div>
        )}

        {loading ? (
          <div className="explorer-loading">
            <Loader2 className="spin" size={20} />
            Loading activity...
          </div>
        ) : (
          <>
            <div className="cw-stats">
              <div className="cw-stat">
                <Activity size={18} />
                <span>Saved queries</span>
                <strong>{entries.length}</strong>
              </div>

              <div className="cw-stat">
                <Database size={18} />
                <span>Databases used</span>
                <strong>{databases.size}</strong>
              </div>

              <div className="cw-stat">
                <Rows3 size={18} />
                <span>Rows returned</span>
                <strong>{totalRows.toLocaleString()}</strong>
              </div>

              <div className="cw-stat">
                <Clock3 size={18} />
                <span>Avg. latency</span>
                <strong>{averageLatency ? `${averageLatency} ms` : "—"}</strong>
              </div>
            </div>

            <div className="result-card">
              <div className="result-header">
                <div>
                  <span className="section-tag">LATENCY</span>
                  <h3>Most recent queries</h3>
                </div>
              </div>

              {recent.length === 0 ? (
                <div className="empty-result">
                  No queries recorded yet. Run a query in the Query Workspace
                  and it will appear here.
                </div>
              ) : (
                <div className="cw-bars">
                  {recent.map((entry) => (
                    <div
                      className="cw-bar"
                      key={entry.id}
                      title={`${entry.database || "Unknown"} · ${Math.round(
                        entry.latency_ms || 0
                      )} ms`}
                    >
                      <div
                        className="cw-bar-fill"
                        style={{
                          height: `${Math.max(
                            6,
                            ((entry.latency_ms || 0) / maxLatency) * 100
                          )}%`,
                        }}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        <div className="cw-planned-head">
          <span className="section-tag">PLANNED</span>
          <p>These features are not available yet.</p>
        </div>

        <div className="cw-planned">
          {planned.map((item) => {
            const Icon = item.icon;

            return (
              <div className="cw-planned-card" key={item.title}>
                <Icon size={19} />
                <strong>{item.title}</strong>
                <p>{item.text}</p>
                <span>Coming soon</span>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}