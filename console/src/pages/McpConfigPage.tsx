import { useEffect, useState } from "react";
import { Plus, Trash2, Edit2, Save, X, RefreshCw, Server } from "lucide-react";
import { api, McpServer } from "@/lib/api";

const TRANSPORTS = ["streamableHttp", "sse", "stdio"];

const EMPTY: Omit<McpServer, "headers"> & { headers: string } = {
  id: "", name: "", transport: "streamableHttp", url: "", headers: "",
};

export function McpConfigPage() {
  const [servers,  setServers]  = useState<McpServer[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [editing,  setEditing]  = useState<string | null>(null); // id or "new"
  const [form,     setForm]     = useState<typeof EMPTY>({ ...EMPTY });
  const [saving,   setSaving]   = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  async function load() {
    try {
      const res = await api.mcpServers();
      setServers(res.servers);
    } catch (e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  function startNew() {
    setForm({ ...EMPTY });
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
      const payload = { id: form.id, name: form.name, transport: form.transport, url: form.url, headers };
      if (editing === "new") {
        await api.createMcpServer(payload);
      } else {
        await api.updateMcpServer(editing!, payload);
      }
      setEditing(null);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function del(id: string) {
    if (!confirm(`MCP-Server "${id}" wirklich löschen?`)) return;
    try {
      await api.deleteMcpServer(id);
      await load();
    } catch (e) { alert(e instanceof Error ? e.message : "Fehler"); }
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
            <RefreshCw className="h-3.5 w-3.5"/>Aktualisieren
          </button>
          <button onClick={startNew} className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-3.5 w-3.5"/>Server hinzufügen
          </button>
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
              {saving ? "Speichern..." : "Speichern"}
            </button>
            <button onClick={() => { setEditing(null); setError(null); }}
              className="flex items-center gap-2 px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">
              <X className="h-3.5 w-3.5"/>Abbrechen
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
    </div>
  );
}
