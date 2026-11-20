import type {
  ApiHealth,
  HealthEvent,
  MetricsSummary,
  SystemMetrics,
  TransferRecord,
  WorkerStatus,
} from "../types/api";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // ignore non-JSON error body
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  getHealth: () => fetchJson<ApiHealth>("/api/health"),
  getWorkerStatus: () => fetchJson<WorkerStatus>("/api/worker/status"),
  getSystemMetrics: () => fetchJson<SystemMetrics>("/api/system/metrics"),
  getSummary: () => fetchJson<MetricsSummary>("/api/metrics/summary"),
  getTransfers: () => fetchJson<TransferRecord[]>("/api/transfers?limit=50"),
  getEvents: () => fetchJson<HealthEvent[]>("/api/events?limit=60"),
  retryTransfer: (id: string) =>
    fetchJson<TransferRecord>(`/api/transfers/${encodeURIComponent(id)}/retry`, {
      method: "POST",
    }),
  downloadUrl: (id: string) => `/api/transfers/${encodeURIComponent(id)}/download`,
};

/**
 * Upload a file using XMLHttpRequest so we can display real browser-to-API HTTP upload
 * progress separately from the backend API-to-Worker TCP backup progress.
 */
export function uploadFileWithProgress(
  file: File,
  onProgress: (loaded: number, total: number, computable: boolean) => void
): Promise<TransferRecord> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/transfers");

    xhr.upload.onprogress = (event) => {
      onProgress(event.loaded, event.total || file.size, event.lengthComputable);
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as TransferRecord);
        } catch {
          reject(new Error("Invalid JSON response from server"));
        }
      } else {
        let message = `Upload failed (HTTP ${xhr.status})`;
        try {
          const parsed = JSON.parse(xhr.responseText);
          if (parsed?.detail) message = String(parsed.detail);
        } catch {
          // ignore
        }
        reject(new Error(message));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during HTTP upload"));
    xhr.onabort = () => reject(new Error("Upload aborted"));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}
