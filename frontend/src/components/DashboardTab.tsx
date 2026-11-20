import type { ApiHealth, MetricsSummary, SystemMetrics, WorkerStatus } from "../types/api";
import { formatDuration, formatKst } from "../services/format";

interface DashboardTabProps {
  apiOnline: boolean;
  health: ApiHealth | null;
  worker: WorkerStatus | null;
  metrics: SystemMetrics | null;
  summary: MetricsSummary | null;
}

function livenessBadgeClass(status: string | undefined): string {
  if (status === "HEALTHY") return "badge badge-ok";
  if (status === "UNRESPONSIVE") return "badge badge-danger";
  return "badge badge-warn";
}

function opHealthBadgeClass(status: string | undefined): string {
  if (status === "OK") return "badge badge-ok";
  if (status === "DEGRADED") return "badge badge-danger";
  return "badge badge-neutral";
}

export function DashboardTab({
  apiOnline,
  health,
  worker,
  metrics,
  summary,
}: DashboardTabProps) {
  const showLivenessVsOpWarning =
    worker?.liveness_status === "HEALTHY" && worker?.backup_operation_health === "DEGRADED";

  return (
    <div className="stack-lg">
      {showLivenessVsOpWarning && (
        <div className="alert-banner alert-warn">
          <strong>Liveness vs. Backup Health Notice:</strong> Backup Worker UDP heartbeats are{" "}
          <code>HEALTHY</code>, but the latest TCP backup failed (
          <code>{worker.last_transfer_error_code || "ERROR"}</code>). Check the File Backup or
          Activity tab for details.
        </div>
      )}

      {/* Top Service Health Row */}
      <section className="grid-3">
        <div className="card">
          <div className="card-header">
            <span className="card-label">Backup Worker (UDP)</span>
            <span className={livenessBadgeClass(worker?.liveness_status)}>
              {worker?.liveness_status ?? "UNKNOWN"}
            </span>
          </div>
          <div className="kv-list">
            <div className="kv-row">
              <span>Last Heartbeat</span>
              <strong>{formatKst(worker?.last_heartbeat_at)}</strong>
            </div>
            <div className="kv-row">
              <span>Worker Uptime</span>
              <strong>{formatDuration(worker?.uptime_seconds)}</strong>
            </div>
            <div className="kv-row">
              <span>Seq / Interval</span>
              <strong>
                #{worker?.last_sequence_number ?? 0}
                {worker?.last_heartbeat_interval_ms !== null &&
                worker?.last_heartbeat_interval_ms !== undefined
                  ? ` (${worker.last_heartbeat_interval_ms} ms)`
                  : ""}
              </strong>
            </div>
            <div className="kv-row">
              <span>Suspected Missed</span>
              <strong>{worker?.suspected_missed_heartbeats ?? 0}</strong>
            </div>
            <div className="kv-row">
              <span>Session ID</span>
              <code className="mono-sm">
                {worker?.session_id ? `${worker.session_id.slice(0, 13)}…` : "—"}
              </code>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <span className="card-label">Backup Pipeline (TCP)</span>
            <span className={opHealthBadgeClass(worker?.backup_operation_health)}>
              {worker?.backup_operation_health ?? "UNKNOWN"}
            </span>
          </div>
          <div className="kv-list">
            <div className="kv-row">
              <span>Verified Backups</span>
              <strong className="text-ok">{summary?.verified_backups ?? 0}</strong>
            </div>
            <div className="kv-row">
              <span>Failed Backups</span>
              <strong className={(summary?.failed_backups ?? 0) > 0 ? "text-danger" : ""}>
                {summary?.failed_backups ?? 0}
              </strong>
            </div>
            <div className="kv-row">
              <span>Attempt Success Rate</span>
              <strong>
                {summary?.transfer_success_rate !== null &&
                summary?.transfer_success_rate !== undefined
                  ? `${summary.transfer_success_rate}%`
                  : "—"}
              </strong>
            </div>
            <div className="kv-row">
              <span>Last TCP Throughput</span>
              <strong>
                {summary?.last_throughput_mb_per_sec !== null &&
                summary?.last_throughput_mb_per_sec !== undefined
                  ? `${summary.last_throughput_mb_per_sec} MB/s`
                  : "—"}
              </strong>
            </div>
            <div className="kv-row">
              <span>Last Verified</span>
              <strong>{formatKst(summary?.last_successful_backup_at)}</strong>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <span className="card-label">API Server (HTTP)</span>
            <span className={apiOnline ? "badge badge-ok" : "badge badge-danger"}>
              {apiOnline ? "ONLINE" : "OFFLINE"}
            </span>
          </div>
          <div className="kv-list">
            <div className="kv-row">
              <span>TCP Worker Target</span>
              <code className="mono-sm">{health?.tcp_target ?? "—"}</code>
            </div>
            <div className="kv-row">
              <span>UDP Monitor Socket</span>
              <code className="mono-sm">{health?.udp_listen ?? "—"}</code>
            </div>
            <div className="kv-row">
              <span>Total Jobs</span>
              <strong>{summary?.total_backups ?? 0}</strong>
            </div>
            <div className="kv-row">
              <span>In Progress</span>
              <strong>{summary?.in_progress_backups ?? 0}</strong>
            </div>
            <div className="kv-row">
              <span>Last Incident</span>
              <strong>{formatKst(summary?.last_incident_at)}</strong>
            </div>
          </div>
        </div>
      </section>

      {/* Local Host Metrics */}
      <section className="card">
        <div className="card-header">
          <div>
            <h2 className="card-title">Host Resource Utilization</h2>
            <p className="card-subtitle">
              Measured on the local Windows host via <code>psutil</code> (single-host metrics).
            </p>
          </div>
          <span className="muted-sm">{formatKst(metrics?.collected_at)}</span>
        </div>

        <div className="grid-3">
          <div className="metric-box">
            <div className="metric-top">
              <span>CPU Usage</span>
              <strong>{metrics ? `${metrics.cpu_percent.toFixed(1)}%` : "—"}</strong>
            </div>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.min(100, metrics?.cpu_percent ?? 0)}%` }}
              />
            </div>
          </div>

          <div className="metric-box">
            <div className="metric-top">
              <span>Memory Usage</span>
              <strong>{metrics ? `${metrics.memory_percent.toFixed(1)}%` : "—"}</strong>
            </div>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.min(100, metrics?.memory_percent ?? 0)}%` }}
              />
            </div>
            <span className="muted-xs">
              {metrics
                ? `${metrics.memory_used_mb.toFixed(0)} / ${metrics.memory_total_mb.toFixed(0)} MB`
                : "—"}
            </span>
          </div>

          <div className="metric-box">
            <div className="metric-top">
              <span>Disk Usage</span>
              <strong>{metrics ? `${metrics.disk_percent.toFixed(1)}%` : "—"}</strong>
            </div>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.min(100, metrics?.disk_percent ?? 0)}%` }}
              />
            </div>
            <span className="muted-xs">
              {metrics
                ? `${metrics.disk_used_gb.toFixed(1)} / ${metrics.disk_total_gb.toFixed(1)} GB`
                : "—"}
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}
