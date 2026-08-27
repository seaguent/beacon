"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";

const API_BASE =
  typeof window !== "undefined"
    ? `http://${window.location.hostname}:8000`
    : "http://localhost:8000";

type EventRow = {
  id: string;
  target_url: string;
  status: string;
  created_at: string;
};

type Stats = {
  total_events: number;
  events_today: number;
  success_rate: number;
  avg_latency_ms: number;
};

const STATUS_COLOR: Record<string, string> = {
  delivered: "var(--status-delivered)",
  retrying: "var(--status-retrying)",
  failed: "var(--status-failed)",
  pending: "var(--status-pending)",
};

export default function Dashboard() {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [eventsRes, statsRes] = await Promise.all([
          fetch(`${API_BASE}/events?limit=25`),
          fetch(`${API_BASE}/stats`),
        ]);
        setEvents(await eventsRes.json());
        setStats(await statsRes.json());
      } catch {
        // API not reachable - keep last known state on screen
      }
    }

    load();
    const interval = setInterval(load, 4000);
    return () => clearInterval(interval);
  }, []);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.wordmark}>BEACON</h1>
        <div className={styles.live}>
          <span className={styles.pulseDot} />
          live
        </div>
      </header>

      <section className={styles.stats}>
        <div className={styles.stat}>
          <p className={styles.statLabel}>Events today</p>
          <p className={styles.statValue}>{stats?.events_today ?? "—"}</p>
        </div>
        <div className={styles.stat}>
          <p className={styles.statLabel}>Success rate</p>
          <p className={styles.statValue}>
            {stats ? `${stats.success_rate}%` : "—"}
          </p>
        </div>
        <div className={styles.stat}>
          <p className={styles.statLabel}>Avg latency</p>
          <p className={styles.statValue}>
            {stats ? `${stats.avg_latency_ms}ms` : "—"}
          </p>
        </div>
      </section>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Status</th>
              <th>Target</th>
              <th>Event</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td>
                  <span className={styles.statusCell}>
                    <span
                      className={styles.statusDot}
                      style={{
                        background: STATUS_COLOR[event.status] ?? STATUS_COLOR.pending,
                      }}
                    />
                    <span style={{ color: STATUS_COLOR[event.status] ?? STATUS_COLOR.pending }}>
                      {event.status}
                    </span>
                  </span>
                </td>
                <td className={styles.target}>{event.target_url}</td>
                <td className={styles.mono}>{event.id.slice(0, 8)}</td>
                <td className={styles.mono}>
                  {new Date(event.created_at).toLocaleTimeString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {events.length === 0 && <p className={styles.empty}>No events yet.</p>}
    </main>
  );
}
