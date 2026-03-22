import { useEffect, useState, useCallback } from "react";
import { Trash2, RefreshCw, ArrowRight, Clock, Radar } from "lucide-react";
import { api, Handoff } from "@/lib/api";

interface Props { projectId: string; }

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `vor ${diff}s`;
  if (diff < 3600) return `vor ${Math.floor(diff / 60)}m`;
  return `vor ${Math.floor(diff / 3600)}h`;
}

function timeLeft(iso: string): string {
  const diff = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (diff <= 0) return "abgelaufen";
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${Math.floor(diff / 3600)}h`;
}

function isExpired(iso: string): boolean {
  return new Date(iso).getTime() < Date.now();
}

export function AgentLinkPanel({ projectId }: Props) {
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.agentlinkHandoffs(projectId);
      setHandoffs(res.handoffs);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  async function handleDelete(handoffId: string) {
    setDeleting(handoffId);
    try {
      await api.deleteHandoff(projectId, handoffId);
      setHandoffs((h) => h.filter((x) => x.id !== handoffId));
      if (expanded === handoffId) setExpanded(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Loeschen");
    } finally {
      setDeleting(null);
    }
  }

  function refresh() {
    setRefreshing(true);
    load();
  }

  return (
    <div className="border-t bg-muted/10 px-5 py-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <ArrowRight className="h-4 w-4 text-primary" />
            <h3 className="text-base font-semibold tracking-tight">AgentLink Handoffs</h3>
            {handoffs.length > 0 && <span className="status-pill">{handoffs.length}</span>}
          </div>
          <p className="mt-2 text-sm text-muted-foreground">Kurzlebige Uebergaben zwischen Agenten im Projektkontext. Auto-Refresh bleibt aktiv.</p>
        </div>
        <button onClick={refresh} disabled={refreshing} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Aktualisieren
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}

      <div className="mt-5 space-y-3">
        {loading ? (
          <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="metric-card h-20 animate-pulse" />)}</div>
        ) : handoffs.length === 0 ? (
          <div className="section-card py-10 text-center text-sm text-muted-foreground">
            <Radar className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="mt-3">Keine aktiven Handoffs.</p>
          </div>
        ) : (
          handoffs.map((h) => (
            <div key={h.id} className={`app-panel overflow-hidden ${isExpired(h.expires_at) ? "opacity-60" : ""}`}>
              <div className="cursor-pointer px-4 py-4 transition hover:bg-muted/10" onClick={() => setExpanded((e) => e === h.id ? null : h.id)}>
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-semibold truncate">{h.from_agent}</span>
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                      <span className="truncate text-muted-foreground">{h.to_agent ?? "any"}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span className="status-pill"><Clock className="h-3 w-3" />{isExpired(h.expires_at) ? "abgelaufen" : `${timeLeft(h.expires_at)} verbleibend`}</span>
                      <span className="status-pill">{timeAgo(h.created_at)}</span>
                    </div>
                  </div>
                  <button onClick={(e) => { e.stopPropagation(); handleDelete(h.id); }} disabled={deleting === h.id} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm text-muted-foreground transition hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50">
                    <Trash2 className="h-4 w-4" />
                    Loeschen
                  </button>
                </div>
              </div>

              {expanded === h.id && (
                <div className="border-t bg-muted/10 px-4 py-4 space-y-3">
                  {h.context && (
                    <div>
                      <p className="metric-kicker">Context</p>
                      <p className="mt-2 text-sm leading-relaxed text-foreground">{h.context}</p>
                    </div>
                  )}
                  {Object.keys(h.data).length > 0 && (
                    <div>
                      <p className="metric-kicker">Data</p>
                      <pre className="mt-2 overflow-x-auto rounded-2xl bg-background/70 p-3 text-xs text-muted-foreground">{JSON.stringify(h.data, null, 2)}</pre>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                    <span>ID: <span className="font-mono">{h.id.slice(0, 8)}...</span></span>
                    <span>Erstellt: {new Date(h.created_at).toLocaleTimeString("de-DE")}</span>
                    <span>Laeuft ab: {new Date(h.expires_at).toLocaleTimeString("de-DE")}</span>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
