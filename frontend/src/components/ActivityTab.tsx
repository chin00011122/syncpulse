import { formatKst } from "../services/format";
import type { HealthEvent } from "../types/api";

interface ActivityTabProps {
  events: HealthEvent[];
}

function eventBadgeClass(eventType: string): string {
  switch (eventType) {
    case "WORKER_HEALTHY":
    case "WORKER_RECOVERED":
    case "TRANSFER_VERIFIED":
      return "badge badge-ok";
    case "WORKER_UNRESPONSIVE":
    case "TRANSFER_FAILED":
      return "badge badge-danger";
    default:
      return "badge badge-neutral";
  }
}

export function ActivityTab({ events }: ActivityTabProps) {
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="card-title">Incident & Activity History</h2>
          <p className="card-subtitle">
            Recorded worker liveness transitions (UDP) and backup outcomes (TCP).
          </p>
        </div>
        <span className="muted-sm">{events.length} events</span>
      </div>

      {events.length === 0 ? (
        <p className="empty-state">No state transitions or transfer events recorded yet.</p>
      ) : (
        <div className="event-list">
          {events.map((ev) => (
            <div key={ev.id} className="event-item">
              <div className="event-top">
                <span className={eventBadgeClass(ev.event_type)}>{ev.event_type}</span>
                <span className="muted-xs">{formatKst(ev.created_at)}</span>
              </div>
              {ev.details && <p className="event-details">{ev.details}</p>}
              <div className="muted-xs">
                Worker: <code>{ev.worker_id}</code>
                {ev.previous_status && ev.new_status
                  ? ` · ${ev.previous_status} → ${ev.new_status}`
                  : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
