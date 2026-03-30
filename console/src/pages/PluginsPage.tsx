import { useEffect, useState } from "react";
import { Puzzle, RefreshCw, CheckCircle2, AlertCircle, Zap } from "lucide-react";

const API = "/api";
function authHeaders() {
  const token = localStorage.getItem("hydrahive_token") || "";
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

interface PluginEntry {
  module: string;
  file: string;
  agent_id: string;
  events: string[];
}

interface PluginStatus {
  plugins: PluginEntry[];
  hooks: Record<string, number>;
  total: number;
}

export function PluginsPage() {
  const [status,   setStatus]   = useState<PluginStatus | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [reloading, setReloading] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    try {
      const res = await fetch(`${API}/plugins`, { headers: authHeaders() });
      setStatus(await res.json());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }

  async function reload() {
    setReloading(true); setMsg("");
    try {
      const res = await fetch(`${API}/plugins/reload`, { method: "POST", headers: authHeaders() });
      const d: PluginStatus = await res.json();
      setStatus(d);
      setMsg(`${d.total} Plugin(s) neu geladen`);
    } catch { setMsg("Fehler beim Neu-Laden"); }
    finally { setReloading(false); }
  }

  useEffect(() => { load(); }, []);

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-zinc-800 flex items-center justify-center">
            <Puzzle className="h-5 w-5 text-zinc-300" />
          </div>
          <div>
            <h2 className="text-base font-semibold">Plugins</h2>
            <p className="text-sm text-muted-foreground">Geladene Erweiterungen aus /agents/*/plugins/</p>
          </div>
        </div>
        <button onClick={reload} disabled={reloading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-lg hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${reloading ? "animate-spin" : ""}`} />
          Neu laden
        </button>
      </div>

      {msg && (
        <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/20 rounded-lg px-4 py-2.5 text-sm text-green-400">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          {msg}
        </div>
      )}

      {/* Hook-Übersicht */}
      {status && Object.keys(status.hooks).length > 0 && (
        <div className="bg-card border rounded-xl p-4 space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <Zap className="h-3.5 w-3.5" /> Aktive Hooks
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(status.hooks).map(([event, count]) => (
              <span key={event} className="flex items-center gap-1.5 px-2.5 py-1 text-xs bg-secondary rounded-full">
                <span className="font-mono">{event}</span>
                <span className="text-muted-foreground">×{count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Plugin-Liste */}
      {loading ? (
        <div className="text-center py-8 text-muted-foreground text-sm">Lade…</div>
      ) : !status || status.total === 0 ? (
        <div className="bg-card border rounded-xl p-6 text-center space-y-2">
          <AlertCircle className="h-8 w-8 text-muted-foreground mx-auto" />
          <p className="text-sm font-medium">Keine Plugins geladen</p>
          <p className="text-xs text-muted-foreground">
            Lege Python-Dateien in <code className="bg-secondary px-1 rounded">/agents/{'<agent_id>'}/plugins/</code> ab
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {status.plugins.map(p => (
            <div key={p.module} className="bg-card border rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-green-400 shrink-0" />
                  <span className="text-sm font-medium font-mono">{p.file.split("/").pop()}</span>
                </div>
                <span className="text-xs text-muted-foreground">Agent: {p.agent_id}</span>
              </div>
              {p.events.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {p.events.map(e => (
                    <span key={e} className="px-2 py-0.5 text-xs bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-full font-mono">
                      {e}
                    </span>
                  ))}
                </div>
              )}
              <p className="text-[0.65rem] text-muted-foreground font-mono truncate">{p.file}</p>
            </div>
          ))}
        </div>
      )}

      {/* Anleitung */}
      <div className="bg-muted/40 border rounded-xl p-4 space-y-2 text-xs text-muted-foreground">
        <p className="font-medium text-foreground text-sm">Plugin erstellen</p>
        <pre className="bg-background rounded-lg p-3 text-[0.65rem] overflow-x-auto text-foreground/80">{`# /agents/my-agent/plugins/my_plugin.py
from hydrahive_core.plugin_manager import plugin_manager

async def on_message_after(project_id, response, **_):
    print(f"Antwort in {project_id}: {response[:80]}")

plugin_manager.on("message.after", on_message_after)`}</pre>
        <p>Verfügbare Events: <code>message.before</code> · <code>message.after</code> · <code>tool.before</code> · <code>tool.after</code> · <code>schedule.run</code> · <code>pipeline.file</code> · <code>agent.spawn</code></p>
      </div>
    </div>
  );
}
