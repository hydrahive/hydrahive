import { useEffect, useState } from "react";
import { RefreshCw, ShieldCheck, Filter } from "lucide-react";
import { api, AuditEntry } from "@/lib/api";

const ACTION_GROUPS: Record<string, string[]> = {
  "Benutzer":    ["user.login", "user.create", "user.delete", "user.password_change"],
  "Agent":       ["agent.create", "agent.update", "agent.delete"],
  "Projekt":     ["project.create", "project.provision", "project.delete"],
  "Skill":       ["skill.create", "skill.update", "skill.delete"],
  "Webhook":     ["webhook.create", "webhook.delete", "webhook.fire"],
  "LLM":         ["llm.token_set"],
};

const ACTION_COLOR: Record<string, string> = {
  "create":   "text-green-500",
  "login":    "text-blue-500",
  "delete":   "text-destructive",
  "fire":     "text-purple-500",
  "error":    "text-destructive",
};

function actionColor(action: string): string {
  const suffix = action.split(".").pop() ?? "";
  return ACTION_COLOR[suffix] ?? "text-muted-foreground";
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "medium" });
  } catch { return iso; }
}

export function AuditPage() {
  const [logs,      setLogs]      = useState<AuditEntry[]>([]);
  const [count,     setCount]     = useState(0);
  const [loading,   setLoading]   = useState(true);
  const [refreshing,setRefreshing]= useState(false);
  const [filterUser,   setFilterUser]   = useState("");
  const [filterAction, setFilterAction] = useState("");
  const [filterProject,setFilterProject]= useState("");
  const [limit,     setLimit]     = useState(100);

  async function load(opts?: { user?: string; action?: string; project?: string; limit?: number }) {
    try {
      const d = await api.auditLogs({
        limit:   opts?.limit   ?? limit,
        user:    opts?.user    ?? (filterUser   || undefined),
        action:  opts?.action  ?? (filterAction || undefined),
        project: opts?.project ?? (filterProject || undefined),
      });
      setLogs(d.logs);
      setCount(d.count);
    } catch { /* Endpoint noch nicht vorhanden → leise scheitern */ }
    finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);

  function refresh() { setRefreshing(true); load(); }

  function applyFilters() { setLoading(true); load(); }

  function clearFilters() {
    setFilterUser(""); setFilterAction(""); setFilterProject("");
    setLoading(true);
    load({ user: "", action: "", project: "" });
  }

  const allActions = Object.values(ACTION_GROUPS).flat();
  const hasFilter = filterUser || filterAction || filterProject;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" />Audit-Log
          </h1>
          <p className="text-sm text-muted-foreground">{count} Einträge gesamt</p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          Aktualisieren
        </button>
      </div>

      {/* Filter-Leiste */}
      <div className="flex items-center gap-3 px-6 py-3 border-b bg-muted/20 flex-shrink-0">
        <Filter className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
        <input value={filterUser} onChange={e => setFilterUser(e.target.value)}
          placeholder="Benutzer"
          className="w-32 px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
        <select value={filterAction} onChange={e => setFilterAction(e.target.value)}
          className="px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary">
          <option value="">Alle Aktionen</option>
          {Object.entries(ACTION_GROUPS).map(([group, actions]) => (
            <optgroup key={group} label={group}>
              {actions.map(a => <option key={a} value={a}>{a}</option>)}
            </optgroup>
          ))}
        </select>
        <input value={filterProject} onChange={e => setFilterProject(e.target.value)}
          placeholder="Projekt-ID"
          className="w-36 px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}
          className="px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary">
          <option value={50}>50 Zeilen</option>
          <option value={100}>100 Zeilen</option>
          <option value={250}>250 Zeilen</option>
          <option value={500}>500 Zeilen</option>
        </select>
        <button onClick={applyFilters}
          className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 transition-colors">
          Filtern
        </button>
        {hasFilter && (
          <button onClick={clearFilters}
            className="px-3 py-1.5 text-sm border rounded hover:bg-accent transition-colors">
            Zurücksetzen
          </button>
        )}
      </div>

      {/* Tabelle */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-6 space-y-2">
            {[1,2,3,4,5].map(i => <div key={i} className="h-8 bg-muted rounded animate-pulse" />)}
          </div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-2 text-muted-foreground py-16">
            <ShieldCheck className="h-10 w-10" />
            <p className="text-sm">{hasFilter ? "Keine Einträge für diese Filter." : "Noch keine Audit-Einträge."}</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-background border-b">
              <tr className="text-xs text-muted-foreground uppercase tracking-wide">
                <th className="text-left px-4 py-2.5 font-medium">Zeitpunkt</th>
                <th className="text-left px-4 py-2.5 font-medium">Benutzer</th>
                <th className="text-left px-4 py-2.5 font-medium">Aktion</th>
                <th className="text-left px-4 py-2.5 font-medium">Ziel</th>
                <th className="text-left px-4 py-2.5 font-medium">Projekt</th>
                <th className="text-left px-4 py-2.5 font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log, i) => (
                <tr key={log.id} className={`border-b hover:bg-muted/30 transition-colors ${i % 2 === 0 ? "" : "bg-muted/10"}`}>
                  <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap font-mono">
                    {fmtTime(log.timestamp)}
                  </td>
                  <td className="px-4 py-2 font-medium">{log.user}</td>
                  <td className={`px-4 py-2 font-mono text-xs font-medium ${actionColor(log.action)}`}>
                    {log.action}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground max-w-[200px] truncate">{log.target || "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{log.project_id || "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground font-mono">{log.ip}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
