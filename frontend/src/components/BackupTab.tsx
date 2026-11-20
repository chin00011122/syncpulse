import { useRef, useState } from "react";
import { api, uploadFileWithProgress } from "../services/api";
import { formatBytes, formatKst, shortHash } from "../services/format";
import type { TransferRecord } from "../types/api";

interface BackupTabProps {
  transfers: TransferRecord[];
  maxFileSizeBytes: number;
  onRefresh: () => Promise<void>;
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "VERIFIED":
      return "badge badge-ok";
    case "FAILED":
      return "badge badge-danger";
    case "TRANSFERRING":
    case "QUEUED":
    case "STAGED":
      return "badge badge-info";
    default:
      return "badge badge-neutral";
  }
}

export function BackupTab({ transfers, maxFileSizeBytes, onRefresh }: BackupTabProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [httpProgress, setHttpProgress] = useState<number | null>(null);
  const [httpComputable, setHttpComputable] = useState(true);
  const [activeTransferId, setActiveTransferId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const activeTransfer = activeTransferId
    ? transfers.find((t) => t.id === activeTransferId) ?? null
    : null;

  async function handleStartUpload() {
    if (!selectedFile || isUploading) return;
    setUploadError(null);

    if (selectedFile.size > maxFileSizeBytes) {
      setUploadError(
        `File size (${formatBytes(selectedFile.size)}) exceeds the maximum limit of ${formatBytes(maxFileSizeBytes)}.`
      );
      return;
    }

    setIsUploading(true);
    setHttpProgress(0);
    setHttpComputable(true);

    try {
      const created = await uploadFileWithProgress(selectedFile, (loaded, total, computable) => {
        setHttpComputable(computable);
        if (computable && total > 0) {
          setHttpProgress(Math.min(100, Math.round((loaded / total) * 100)));
        }
      });
      setHttpProgress(100);
      setActiveTransferId(created.id);
      setSelectedFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      await onRefresh();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleRetry(id: string) {
    setRetryingId(id);
    setUploadError(null);
    try {
      await api.retryTransfer(id);
      setActiveTransferId(id);
      await onRefresh();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetryingId(null);
    }
  }

  const tcpPercent =
    activeTransfer && activeTransfer.file_size > 0
      ? Math.min(100, Math.round((activeTransfer.bytes_transferred / activeTransfer.file_size) * 100))
      : activeTransfer?.status === "VERIFIED"
        ? 100
        : 0;

  return (
    <div className="stack-lg">
      {/* Upload Card */}
      <section className="card">
        <div className="card-header">
          <div>
            <h2 className="card-title">Upload & Back Up File</h2>
            <p className="card-subtitle">
              Stage 1 uploads via HTTP to the API server; Stage 2 streams over a raw TCP socket to
              the Backup Worker with SHA-256 verification. Max size:{" "}
              <strong>{formatBytes(maxFileSizeBytes)}</strong>.
            </p>
          </div>
        </div>

        <div className="upload-controls">
          <input
            ref={fileInputRef}
            type="file"
            className="file-input"
            disabled={isUploading}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setSelectedFile(f);
              setUploadError(null);
            }}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!selectedFile || isUploading}
            onClick={handleStartUpload}
          >
            {isUploading ? "Uploading…" : "Start Backup"}
          </button>
        </div>

        {selectedFile && (
          <p className="muted-sm">
            Selected: <strong>{selectedFile.name}</strong> ({formatBytes(selectedFile.size)})
          </p>
        )}

        {uploadError && <div className="alert-banner alert-danger">{uploadError}</div>}

        {/* Two-Stage Progress Display */}
        {(isUploading || activeTransfer) && (
          <div className="stage-panel">
            <div className="stage-row">
              <div className="metric-top">
                <span>1. HTTP Upload (Browser → API)</span>
                <strong>
                  {isUploading
                    ? httpComputable && httpProgress !== null
                      ? `${httpProgress}%`
                      : "Uploading…"
                    : "Complete"}
                </strong>
              </div>
              <div className="progress-track">
                <div
                  className={
                    isUploading && !httpComputable
                      ? "progress-fill progress-indeterminate"
                      : "progress-fill"
                  }
                  style={{ width: `${isUploading ? (httpProgress ?? 0) : 100}%` }}
                />
              </div>
            </div>

            <div className="stage-row">
              <div className="metric-top">
                <span>2. TCP Backup & SHA-256 (API → Worker)</span>
                <strong>
                  {!activeTransfer
                    ? "Waiting for HTTP stage…"
                    : activeTransfer.status === "QUEUED"
                      ? "Queued…"
                      : activeTransfer.status === "TRANSFERRING"
                        ? `${tcpPercent}% (${formatBytes(activeTransfer.bytes_transferred)} / ${formatBytes(activeTransfer.file_size)})`
                        : activeTransfer.status}
                </strong>
              </div>
              <div className="progress-track">
                <div
                  className={
                    activeTransfer?.status === "QUEUED"
                      ? "progress-fill progress-indeterminate"
                      : activeTransfer?.status === "FAILED"
                        ? "progress-fill progress-danger"
                        : activeTransfer?.status === "VERIFIED"
                          ? "progress-fill progress-ok"
                          : "progress-fill"
                  }
                  style={{
                    width:
                      activeTransfer?.status === "QUEUED"
                        ? "35%"
                        : `${activeTransfer ? tcpPercent : 0}%`,
                  }}
                />
              </div>
              {activeTransfer?.status === "FAILED" && (
                <p className="error-text">
                  [{activeTransfer.error_code}] {activeTransfer.error_message}
                </p>
              )}
            </div>
          </div>
        )}
      </section>

      {/* Transfer List */}
      <section className="card">
        <div className="card-header">
          <h2 className="card-title">Backup History ({transfers.length})</h2>
        </div>

        {transfers.length === 0 ? (
          <p className="empty-state">
            No backups yet. Select a file above to run your first TCP-verified backup.
          </p>
        ) : (
          <div className="transfer-list">
            {transfers.map((t) => {
              const shaMatch =
                t.status === "VERIFIED" &&
                t.stored_sha256 !== null &&
                t.stored_sha256 === t.original_sha256;
              return (
                <article key={t.id} className="transfer-item">
                  <div className="transfer-top">
                    <div className="transfer-title-group">
                      <strong className="transfer-filename">{t.original_filename}</strong>
                      <span className="muted-xs">
                        {formatBytes(t.file_size)} · Attempt #{t.attempt_count} ·{" "}
                        {formatKst(t.created_at)}
                      </span>
                    </div>
                    <span className={statusBadgeClass(t.status)}>{t.status}</span>
                  </div>

                  <div className="transfer-meta">
                    <div>
                      <span className="meta-label">SHA-256: </span>
                      <code className="mono-sm">{shortHash(t.original_sha256)}</code>
                      {shaMatch && <span className="sha-ok"> ✓ Verified Match</span>}
                    </div>
                    {t.transfer_duration_ms !== null && (
                      <div className="muted-xs">
                        TCP: {t.transfer_duration_ms.toFixed(1)} ms
                        {t.throughput_mb_per_sec !== null
                          ? ` (${t.throughput_mb_per_sec} MB/s)`
                          : ""}
                        {t.upload_duration_ms !== null
                          ? ` · HTTP upload: ${t.upload_duration_ms.toFixed(1)} ms`
                          : ""}
                      </div>
                    )}
                    {t.error_message && (
                      <div className="error-text">
                        <strong>{t.error_code}:</strong> {t.error_message}
                      </div>
                    )}
                  </div>

                  <div className="transfer-actions">
                    {t.can_download && (
                      <a
                        href={api.downloadUrl(t.id)}
                        download={t.original_filename}
                        className="btn btn-secondary"
                      >
                        Download
                      </a>
                    )}
                    {t.can_retry && (
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={retryingId === t.id}
                        onClick={() => handleRetry(t.id)}
                      >
                        {retryingId === t.id ? "Retrying…" : "Retry Backup"}
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
