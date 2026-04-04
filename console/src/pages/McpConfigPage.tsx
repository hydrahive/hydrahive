import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Edit2, Save, X, RefreshCw, Server, Brain, ExternalLink } from "lucide-react";
import { api, McpServer } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const TRANSPORTS = ["streamableHttp", "sse", "stdio"];
const AMEM_BROWSER_HOST = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
const AMEM_SEARCH_UI_URL = `http://${AMEM_BROWSER_HOST}:8021`;

const EMPTY: Omit<McpServer, "headers"> & { headers: string } = {
  id: "", name: "", transport: "streamableHttp", url: "", headers: "",
};

const AMEM_PRESET: McpServer = {
  id: "amem",
  name: "A-MEM Shared Memory",
  transport: "sse",
  url: "http://127.0.0.1:8020/sse",
  headers: {},
  meta: {
    role: "shared_memory",
    search_ui_url: AMEM_SEARCH_UI_URL,
  },
};

export function McpConfigPage() {
  const { t } = useTranslation();
  const [servers,  setServers]  = useState<McpServer[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [editing,  setEditing]  = useState<string | null>(null); // id or "new"
  const [form,     setForm]     = useState<typeof EMPTY>({ ...EMPTY });
  const [saving,   setSaving]   = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);
  const hasAmem = useMemo(() => servers.some((s) => s.id === "amem"), [servers]);

  async function load() {
    try {
      const res = await api.mcpServers();
      setServers(res.servers);
    } catch (e) { setError(e instanceof Error ? e.message : t("common.error")); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  function startNew() {
    setForm({ ...EMPTY });
    setEditing("new");
    setError(null);
  }

  function startAmemPreset() {
    setForm({
      id: AMEM_PRESET.id,
      name: AMEM_PRESET.name,
      transport: AMEM_PRESET.transport,
      url: AMEM_PRESET.url,
      headers: "",
    });
    setEditing("new");
    setError(null);
  }

  function startEdit(s: McpServer) {
    setForm({
      id:        s.id,
      name:      s.name,
      transport: s.transport,
      url:       s.url,
      headers:   Object.keys(s.headers).length
        ? JSON.stringify(s.headers, null, 2)
        : "",
    });
    setEditing(s.id);
    setError(null);
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      let headers: Record<string, string> = {};
      if (form.headers.trim()) {
        try { headers = JSON.parse(form.headers); }
        catch { throw new Error("Headers müssen gültiges JSON sein"); }
      }
      const payload = {
        id: form.id,
        name: form.name,
        transport: form.transport,
        url: form.url,
        headers,
        meta: form.id === "amem" ? AMEM_PRESET.meta : {},
      };
      if (editing === "new") {
        await api.createMcpServer(payload);
      } else {
        await api.updateMcpServer(editing!, payload);
      }
      setEditing(null);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : t("common.error")); }
    finally { setSaving(false); }
  }

  function del(id: string) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("common.confirmDelete", { name: id }),
      action: async () => {
        try {
          await api.deleteMcpServer(id);
          await load();
        } catch (e) { alert(e instanceof Error ? e.message : t("common.error")); }
      },
    });
  }

  if (loading) return <div className="p-6"><div className="animate-pulse space-y-3">{[1,2].map(i=><div key={i} className="h-20 bg-muted rounded-lg"/>)}</div></div>;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">MCP-Server</h1>
          <p className="text-sm text-muted-foreground">Model Context Protocol Server verwalten und Agenten zuweisen</p>
        </div>
        <div className="flex gap-2">
          <button onClick={load} className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors">
            <RefreshCw className="h-3.5 w-3.5"/>{t("common.refresh")}
          </button>
          <button onClick={startAmemPreset} disabled={hasAmem}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <Brain className="h-3.5 w-3.5"/>A-MEM Preset
          </button>
          <button onClick={startNew} className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-3.5 w-3.5"/>Server hinzufügen
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Brain className="h-4 w-4 text-primary" />
              A-MEM Shared Memory
            </div>
            <p className="text-sm text-muted-foreground">
              Zentrale agentenuebergreifende Langzeit-Wissensdatenbank fuer Fehler, Loesungen, Learnings und Betriebswissen.
            </p>
            <p className="text-xs font-mono text-muted-foreground">{AMEM_PRESET.url}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <a href={String(AMEM_PRESET.meta?.search_ui_url ?? "#")} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs hover:bg-accent transition-colors">
              Search UI
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
            <span className={`rounded-full px-2.5 py-1 text-[11px] ${hasAmem ? "bg-emerald-500/15 text-emerald-700" : "bg-muted text-muted-foreground"}`}>
              {hasAmem ? "konfiguriert" : "noch nicht konfiguriert"}
            </span>
          </div>
        </div>
      </div>

      {error && <div className="bg-destructive/10 text-destructive text-sm px-4 py-2 rounded-md">{error}</div>}

      {/* Edit/Create Form */}
      {editing && (
        <div className="bg-card border rounded-lg p-5 space-y-4">
          <h2 className="font-medium text-sm">{editing === "new" ? "Neuer MCP-Server" : `Server bearbeiten: ${editing}`}</h2>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">ID</label>
              <input value={form.id} onChange={e => setForm(f=>({...f, id: e.target.value}))}
                disabled={editing !== "new"}
                placeholder="godot-mcp"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-60" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Name</label>
              <input value={form.name} onChange={e => setForm(f=>({...f, name: e.target.value}))}
                placeholder="Godot MCP"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Transport</label>
              <select value={form.transport} onChange={e => setForm(f=>({...f, transport: e.target.value}))}
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                {TRANSPORTS.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">URL</label>
              <input value={form.url} onChange={e => setForm(f=>({...f, url: e.target.value}))}
                placeholder="http://localhost:7401/mcp"
                className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Headers (JSON, optional)</label>
            <textarea value={form.headers} onChange={e => setForm(f=>({...f, headers: e.target.value}))}
              rows={3}
              placeholder={'{"Authorization": "Bearer token"}'}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
          </div>

          <div className="flex gap-2">
            <button onClick={save} disabled={saving || !form.id || !form.name || !form.url}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
              <Save className="h-3.5 w-3.5"/>
              {saving ? t("common.saving") : t("common.save")}
            </button>
            <button onClick={() => { setEditing(null); setError(null); }}
              className="flex items-center gap-2 px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">
              <X className="h-3.5 w-3.5"/>{t("common.cancel")}
            </button>
          </div>
        </div>
      )}

      {/* Server List */}
      {servers.length === 0 && !editing ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          <Server className="h-8 w-8 mx-auto mb-3 opacity-40"/>
          Noch keine MCP-Server konfiguriert.
        </div>
      ) : (
        <div className="space-y-3">
          {servers.map(s => (
            <div key={s.id} className="bg-card border rounded-lg p-4 flex items-center justify-between gap-4">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0">
                  <Server className="h-4 w-4 text-primary"/>
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{s.name}</span>
                    <span className="text-xs text-muted-foreground font-mono bg-muted px-1.5 py-0.5 rounded">{s.id}</span>
                    <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">{s.transport}</span>
                    {s.id === "amem" && <span className="text-xs text-primary bg-primary/10 px-1.5 py-0.5 rounded">shared memory</span>}
                  </div>
                  <p className="text-xs text-muted-foreground font-mono truncate">{s.url}</p>
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button onClick={() => startEdit(s)}
                  className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors">
                  <Edit2 className="h-3.5 w-3.5"/>
                </button>
                <button onClick={() => del(s.id)}
                  className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors">
                  <Trash2 className="h-3.5 w-3.5"/>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="bg-muted/30 border rounded-lg p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-medium text-foreground">Wie werden MCP-Server verwendet?</p>
        <p>Konfigurierte Server können Agenten unter <strong>Agenten → Bearbeiten</strong> zugewiesen werden. Admins können jeden Agenten konfigurieren; normale User können ihrem persönlichen Agenten unter <strong>Mein Agent → MCP</strong> Server zuweisen.</p>
        <p>MCP-Server werden beim nächsten Aufruf des Agenten aktiviert und stellen dem Modell externe Tools zur Verfügung.</p>
      </div>
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </div>
  );
}
