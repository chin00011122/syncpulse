import { useCallback, useEffect, useState } from "react";
import { ActivityTab } from "./components/ActivityTab";
import { BackupTab } from "./components/BackupTab";
import { DashboardTab } from "./components/DashboardTab";
import { api } from "./services/api";
import type {
  ApiHealth,
  HealthEvent,
  MetricsSummary,
  SystemMetrics,
  TransferRecord,
  WorkerStatus,
} from "./types/api";

type TabId = "dashboard" | "backup" | "activity";

const POLL_INTERVAL_MS = 1500;

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const [apiOnline, setApiOnline] = useState(false);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [transfers, setTransfers] = useState<TransferRecord[]>([]);
  const [events, setEvents] = useState<HealthEvent[]>([]);

  const refreshAll = useCallback(async () => {
    try {
      const [h, w, m, s, t, e] = await Promise.all([
        api.getHealth(),
        api.getWorkerStatus(),
        api.getSystemMetrics(),
        api.getSummary(),
        api.getTransfers(),
        api.getEvents(),
      ]);
      setApiOnline(true);
      setHealth(h);
      setWorker(w);
      setMetrics(m);
      setSummary(s);
      setTransfers(t);
      setEvents(e);
    } catch {
      setApiOnline(false);
    }
  }, []);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => {
      void refreshAll();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  const workerStatus = worker?.liveness_status ?? "STARTING";
  const pillClass =
    !apiOnline || workerStatus === "UNRESPONSIVE"
      ? "status-pill status-pill-danger"
      : workerStatus === "HEALTHY"
        ? "status-pill status-pill-ok"
        : "status-pill status-pill-warn";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-dot" />
            <div>
              <h1 className="brand-title">SyncPulse</h1>
              <p className="brand-sub">TCP Backup · UDP Heartbeat Monitor</p>
            </div>
          </div>
          <div className={pillClass}>
            {!apiOnline ? "API OFFLINE" : `Worker: ${workerStatus}`}
          </div>
        </div>

        <nav className="tabs" aria-label="Primary navigation">
          <button
            type="button"
            className={activeTab === "dashboard" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("dashboard")}
          >
            Dashboard
          </button>
          <button
            type="button"
            className={activeTab === "backup" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("backup")}
          >
            File Backup ({transfers.length})
          </button>
          <button
            type="button"
            className={activeTab === "activity" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("activity")}
          >
            Activity ({events.length})
          </button>
        </nav>
      </header>

      <main className="main-content">
        {!apiOnline && (
          <div className="alert-banner alert-danger">
            Cannot reach SyncPulse API server. Ensure the FastAPI process is running on your host.
          </div>
        )}

        {activeTab === "dashboard" && (
          <DashboardTab
            apiOnline={apiOnline}
            health={health}
            worker={worker}
            metrics={metrics}
            summary={summary}
          />
        )}

        {activeTab === "backup" && (
          <BackupTab
            transfers={transfers}
            maxFileSizeBytes={health?.max_file_size_bytes ?? 50 * 1024 * 1024}
            onRefresh={refreshAll}
          />
        )}

        {activeTab === "activity" && <ActivityTab events={events} />}
      </main>

      <footer className="footer">
        <span>SyncPulse · Local LAN Unidirectional Backup Console (No Auth / HTTP Demo)</span>
      </footer>
    </div>
  );
}
