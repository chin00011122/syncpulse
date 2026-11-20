export interface ApiHealth {
  status: string;
  service: string;
  timestamp_utc: string;
  max_file_size_bytes: number;
  tcp_target: string;
  udp_listen: string;
}

export interface WorkerStatus {
  worker_id: string;
  liveness_status: "STARTING" | "HEALTHY" | "UNRESPONSIVE" | string;
  is_fresh: boolean;
  session_id: string | null;
  last_sequence_number: number;
  last_heartbeat_at: string | null;
  started_at: string | null;
  uptime_seconds: number | null;
  last_heartbeat_interval_ms: number | null;
  suspected_missed_heartbeats: number;
  total_heartbeats_received: number;
  last_transition_event: string | null;
  backup_operation_health: "OK" | "DEGRADED" | "UNKNOWN" | string;
  last_transfer_status: string | null;
  last_transfer_error_code: string | null;
}

export interface SystemMetrics {
  scope: string;
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  disk_percent: number;
  disk_used_gb: number;
  disk_total_gb: number;
  collected_at: string;
}

export interface TransferRecord {
  id: string;
  original_filename: string;
  file_size: number;
  original_sha256: string;
  stored_sha256: string | null;
  status: "STAGED" | "QUEUED" | "TRANSFERRING" | "VERIFIED" | "FAILED" | string;
  bytes_transferred: number;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  upload_duration_ms: number | null;
  tcp_connect_ms: number | null;
  transfer_duration_ms: number | null;
  total_duration_ms: number | null;
  throughput_bytes_per_sec: number | null;
  throughput_mb_per_sec: number | null;
  can_retry: boolean;
  can_download: boolean;
}

export interface HealthEvent {
  id: number;
  worker_id: string;
  session_id: string | null;
  event_type: string;
  previous_status: string | null;
  new_status: string | null;
  details: string | null;
  created_at: string;
}

export interface MetricsSummary {
  total_backups: number;
  verified_backups: number;
  failed_backups: number;
  in_progress_backups: number;
  transfer_success_rate: number | null;
  last_throughput_bytes_per_sec: number | null;
  last_throughput_mb_per_sec: number | null;
  last_successful_backup_at: string | null;
  worker_status: string;
  backup_operation_health: string;
  last_incident_at: string | null;
}
