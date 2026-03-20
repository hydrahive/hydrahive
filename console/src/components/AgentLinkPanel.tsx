import { useEffect, useState, useCallback } from "react";
import { Trash2, RefreshCw, ArrowRight, Clock } from "lucide-react";
import { api, Handoff } from "@/lib/api";

interface Props { projectId: string; }

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)  return `vor ${diff}s`;
  if (diff < 3600) return `vor ${Math.floor(diff/60)}m`;
  return `vor ${Math.floor(diff/3600)}h`;
}

function timeLeft(iso: string): string {
  const diff = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (diff <= 0)   return "abgelaufen";
  if (diff < 60)   return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff/60)}m`;
  return `${Math.floor(diff/3600)}h`;
}

function isExpired(iso: string): boolean {
  return new Date(iso).getTime() < Date.now();
}

export function AgentLinkPanel({ projectId }: Props) {
  const [handoffs,   setHandoffs]   = useState<Handoff[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error,      setError]      = useState("");
  const [expanded,   setExpanded]   = useState<string | null>(null);
  const [deleting,   setDeleting]   = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.agentlinkHandoffs(projectId);
      setHandoffs(res.handoffs);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally { setLoading(false); setRefreshing(false); }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  // auto-refresh every 5s
  useEffect(() => {
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  async function handleDelete(handoffId: string) {
    setDeleting(handoffId);
    try {
      await api.deleteHandoff(projectId, handoffId);
      setHandoffs(h => h.filter(x => x.id !== handoffId));
      if (expanded === handoffId) setExpanded(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Löschen");
    } finally { setDeleting(null); }
  }

  function refresh() { setRefreshing(true); load(); }

  return (
    <div className="border-t">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/20">
        <span className="text-xs font-medium text-muted-foreground">
          AgentLink Handoffs
          {handoffs.length > 0 && (
            <span className="ml-2 bg-primary/10 text-primary text-xs px-1.5 py-0.5 rounded-full">
              {handoffs.length}
            </span>
          )}
        </span>
        <button onClick={refresh} disabled={refreshing}
          className="p-1 rounded hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3 w-3 text-muted-foreground ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="px-4 py-3 space-y-2">
        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}

        {loading && (
          <div className="space-y-2">
            {[1,2].map(i => <div key={i} className="h-10 bg-muted/20 rounded animate-pulse" />)}
          </div>
        )}

        {!loading && handoffs.length === 0 && (
          <p className="text-xs text-muted-foreground py-2">Keine aktiven Handoffs.</p>
        )}

        {!loading && handoffs.map(h => (
          <div key={h.id}
            className={`border rounded-md overflow-hidden ${isExpired(h.expires_at) ? "opacity-50" : ""}`}>
            {/* Header row */}
            <div
              className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-muted/10 transition-colors"
              onClick={() => setExpanded(e => e === h.id ? null : h.id)}>
              <div className="flex items-center gap-1.5 text-xs flex-1 min-w-0">
                <span className="font-medium truncate">{h.from_agent}</span>
                <ArrowRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                <span className="text-muted-foreground truncate">{h.to_agent ?? "any"}</span>
              </div>
              <div className="flex items-center gap-1 text-xs text-muted-foreground flex-shrink-0">
                <Clock className="h-3 w-3" />
                <span className={isExpired(h.expires_at) ? "text-destructive" : ""}>
                  {isExpired(h.expires_at) ? "abgelaufen" : `${timeLeft(h.expires_at)} verbleibend`}
                </span>
              </div>
              <span className="text-xs text-muted-foreground flex-shrink-0">{timeAgo(h.created_at)}</span>
              <button
                onClick={e => { e.stopPropagation(); handleDelete(h.id); }}
                disabled={deleting === h.id}
                className="p-1 rounded hover:bg-destructive/10 hover:text-destructive text-muted-foreground transition-colors disabled:opacity-50">
                <Trash2 className="h-3 w-3" />
              </button>
            </div>

            {/* Expanded detail */}
            {expanded === h.id && (
              <div className="px-3 pb-3 pt-1 border-t bg-muted/5 space-y-2">
                {h.context && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">Context</p>
                    <p className="text-xs text-foreground leading-relaxed">{h.context}</p>
                  </div>
                )}
                {Object.keys(h.data).length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">Data</p>
                    <pre className="text-xs bg-muted/20 rounded p-2 overflow-x-auto text-muted-foreground">
                      {JSON.stringify(h.data, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="flex gap-4 text-xs text-muted-foreground pt-1">
                  <span>ID: <span className="font-mono">{h.id.slice(0, 8)}…</span></span>
                  <span>Erstellt: {new Date(h.created_at).toLocaleTimeString("de-DE")}</span>
                  <span>Läuft ab: {new Date(h.expires_at).toLocaleTimeString("de-DE")}</span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
