import { useEffect, useState } from "react";
import {
  Puzzle, Power, PowerOff, RefreshCw, AlertTriangle,
  Wrench, Zap, X, Shield, Users, Trash2,
} from "lucide-react";
import { api, type PluginInfo } from "@/lib/api";
import { useTranslation } from "react-i18next";

type PluginDetail = PluginInfo & { agents: string[] };

export function PluginsPage() {
  const { t } = useTranslation();
  const [plugins, setPlugins]     = useState<PluginInfo[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [selected, setSelected]   = useState<PluginDetail | null>(null);
  const [agents, setAgents]       = useState<string[]>([]);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try {
      const [pRes, aRes] = await Promise.all([
        api.pluginsList(),
        api.get<Record<string, unknown>>("/agents"),
      ]);
      setPlugins(pRes.plugins);
      setAgents(Object.keys(aRes).sort());
    } catch (e: any) {
      setError(e.message);
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function openDetail(id: string) {
    try {
      const d = await api.pluginGet(id);
      setSelected(d);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function toggleEnable(p: PluginInfo) {
    setActionBusy(p.id);
    try {
      if (p.enabled) await api.pluginDisable(p.id);
      else await api.pluginEnable(p.id);
      await load();
      if (selected?.id === p.id) {
        const d = await api.pluginGet(p.id);
        setSelected(d);
      }
    } catch (e: any) { setError(e.message); }
    finally { setActionBusy(null); }
  }

  async function reloadPlugin(id: string) {
    setActionBusy(id);
    try {
      await api.pluginReload(id);
      await load();
      if (selected?.id === id) {
        const d = await api.pluginGet(id);
        setSelected(d);
      }
    } catch (e: any) { setError(e.message); }
    finally { setActionBusy(null); }
  }

  async function toggleAgent(pluginId: string, agentId: string) {
    if (!selected) return;
    const current = selected.agents || [];
    const next = current.includes(agentId)
      ? current.filter(a => a !== agentId)
      : [...current, agentId];
    try {
      for (const aid of agents) {
        const pids = (await api.pluginAgentGet(aid)).plugins;
        if (next.includes(aid) && !pids.includes(pluginId)) {
          await api.pluginAgentSet(aid, [...pids, pluginId]);
        } else if (!next.includes(aid) && pids.includes(pluginId)) {
          await api.pluginAgentSet(aid, pids.filter(p => p !== pluginId));
        }
      }
      setSelected({ ...selected, agents: next });
    } catch (e: any) { setError(e.message); }
  }

  const typeBadge = (type: string) => {
    const colors: Record<string, string> = {
      tool:    "bg-blue-500/10 text-blue-600",
      hook:    "bg-purple-500/10 text-purple-600",
      service: "bg-green-500/10 text-green-600",
    };
    return colors[type] || "bg-muted/60 text-muted-foreground";
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 pt-6 pb-4 border-b border-border/40 flex-shrink-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-3">
            <Puzzle className="h-6 w-6 text-primary" />
            <div>
              <h1 className="text-2xl font-bold tracking-tight">Plugins</h1>
              <p className="text-xs text-muted-foreground">{t("pageDesc.plugins")}</p>
              <p className="text-sm text-muted-foreground">
                {plugins.length > 0 ? `${plugins.length} Plugin(s) installiert` : "Keine Plugins installiert"}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={async () => { setActionBusy("all"); try { await api.pluginsReloadAll(); await load(); } finally { setActionBusy(null); } }}
              disabled={!!actionBusy}
              className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-lg hover:bg-muted/50 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${actionBusy === "all" ? "animate-spin" : ""}`} />
              Alle neu laden
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive mb-4">
            {error}
            <button onClick={() => setError(null)} className="ml-2 underline">x</button>
          </div>
        )}

        {loading && plugins.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
            <RefreshCw className="h-6 w-6 animate-spin mb-3" />
            <p className="text-sm">Lade Plugins...</p>
          </div>
        ) : plugins.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
            <Puzzle className="h-12 w-12 mb-4 opacity-30" />
            <p className="text-lg font-medium mb-2">Keine Plugins installiert</p>
            <p className="text-sm">Lege Plugin-Verzeichnisse unter <code className="bg-muted px-2 py-0.5 rounded">/plugins/</code> an</p>
            <p className="text-xs mt-2 opacity-70">Jedes Plugin braucht: plugin.yaml + plugin.py</p>
          </div>
        ) : (
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {plugins.map(p => (
              <div
                key={p.id}
                className={`rounded-2xl border p-4 transition-all cursor-pointer group
                  ${p.error
                    ? "border-destructive/40 bg-destructive/5"
                    : p.enabled
                      ? "border-border/50 bg-card/80 hover:border-primary/40 hover:shadow-md"
                      : "border-border/30 bg-muted/20 opacity-60 hover:opacity-80"
                  }`}
                onClick={() => openDetail(p.id)}
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Puzzle className={`h-5 w-5 ${p.enabled ? "text-primary" : "text-muted-foreground"}`} />
                    <span className={`text-xs px-2 py-0.5 rounded-full ${typeBadge(p.type)}`}>
                      {p.type}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {p.error && <AlertTriangle className="h-4 w-4 text-destructive" />}
                    <button
                      onClick={e => { e.stopPropagation(); toggleEnable(p); }}
                      disabled={actionBusy === p.id}
                      className="p-1 rounded-lg hover:bg-muted/50 transition-colors"
                      title={p.enabled ? "Deaktivieren" : "Aktivieren"}
                    >
                      {p.enabled
                        ? <Power className="h-4 w-4 text-green-500" />
                        : <PowerOff className="h-4 w-4 text-muted-foreground" />
                      }
                    </button>
                  </div>
                </div>

                <h3 className="font-medium text-sm mb-1 line-clamp-1">{p.name}</h3>
                <p className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2.5rem]">
                  {p.description || "Keine Beschreibung"}
                </p>

                {p.error && <p className="text-xs text-destructive line-clamp-1 mb-2">{p.error}</p>}

                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <div className="flex gap-3">
                    {p.tools.length > 0 && (
                      <span className="flex items-center gap-1"><Wrench className="h-3 w-3" />{p.tools.length}</span>
                    )}
                    {p.hook_count > 0 && (
                      <span className="flex items-center gap-1"><Zap className="h-3 w-3" />{p.hook_count}</span>
                    )}
                  </div>
                  <span className="opacity-50">v{p.version}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Detail Drawer */}
      {selected && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/40 backdrop-blur-sm" onClick={() => setSelected(null)} />
          <div className="w-full max-w-lg bg-background border-l border-border/50 flex flex-col overflow-hidden shadow-2xl">
            <div className="flex items-start justify-between p-6 border-b border-border/40 flex-shrink-0">
              <div className="flex items-center gap-3">
                <Puzzle className={`h-6 w-6 ${selected.enabled ? "text-primary" : "text-muted-foreground"}`} />
                <div>
                  <h2 className="text-lg font-semibold">{selected.name}</h2>
                  <p className="text-xs text-muted-foreground">
                    v{selected.version}{selected.author && ` · ${selected.author}`} · {selected.type}
                  </p>
                </div>
              </div>
              <button onClick={() => setSelected(null)} className="p-1.5 rounded-lg hover:bg-muted/50"><X className="h-4 w-4" /></button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-5">
              {selected.description && <p className="text-sm text-muted-foreground">{selected.description}</p>}

              {selected.error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">{selected.error}</div>
              )}

              {selected.tools.length > 0 && (
                <div>
                  <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
                    <Wrench className="h-3 w-3" /> Tools ({selected.tools.length})
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.tools.map(t => (
                      <span key={t} className="text-xs px-2 py-1 rounded-lg bg-blue-500/10 text-blue-600 font-mono">{t}</span>
                    ))}
                  </div>
                </div>
              )}

              {selected.permissions.length > 0 && (
                <div>
                  <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
                    <Shield className="h-3 w-3" /> Permissions
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.permissions.map(p => (
                      <span key={p} className="text-xs px-2 py-1 rounded-lg bg-orange-500/10 text-orange-600">{p}</span>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
                  <Users className="h-3 w-3" /> Agent-Zuweisung
                </h3>
                <div className="space-y-1.5">
                  {agents.map(aid => {
                    const assigned = (selected.agents || []).includes(aid);
                    return (
                      <label key={aid} className="flex items-center gap-2 text-sm cursor-pointer py-1">
                        <input
                          type="checkbox"
                          checked={assigned}
                          onChange={() => toggleAgent(selected.id, aid)}
                          className="rounded border-border"
                        />
                        <span className={assigned ? "font-medium" : "text-muted-foreground"}>{aid}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="text-xs text-muted-foreground">
                <span className="opacity-60">Pfad:</span>{" "}
                <code className="bg-muted px-1.5 py-0.5 rounded">{selected.path}</code>
              </div>
            </div>

            <div className="p-6 border-t border-border/40 flex-shrink-0 flex gap-2">
              <button
                onClick={() => toggleEnable(selected)}
                disabled={!!actionBusy}
                className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium transition-colors disabled:opacity-50
                  ${selected.enabled
                    ? "border border-border/50 text-muted-foreground hover:bg-muted/50"
                    : "bg-primary text-primary-foreground hover:bg-primary/90"
                  }`}
              >
                {selected.enabled ? <><PowerOff className="h-4 w-4" />Deaktivieren</> : <><Power className="h-4 w-4" />Aktivieren</>}
              </button>
              <button
                onClick={() => reloadPlugin(selected.id)}
                disabled={!!actionBusy}
                className="px-4 py-2.5 rounded-xl border border-border/50 text-sm hover:bg-muted/50 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${actionBusy === selected.id ? "animate-spin" : ""}`} />
              </button>
              <button
                onClick={async () => {
                  if (!confirm(`Plugin "${selected.id}" deinstallieren?`)) return;
                  setActionBusy(selected.id);
                  try {
                    await api.hubUninstallPlugin(selected.id);
                    setSelected(null);
                    await load();
                  } catch (e: any) { setError(e.message); }
                  finally { setActionBusy(null); }
                }}
                disabled={!!actionBusy}
                className="px-4 py-2.5 rounded-xl border border-destructive/50 text-destructive text-sm hover:bg-destructive/10 transition-colors disabled:opacity-50"
                title="Deinstallieren"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
