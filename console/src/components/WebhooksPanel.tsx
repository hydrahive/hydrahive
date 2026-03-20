import { useEffect, useState } from "react";
import { Webhook as WebhookIcon, Plus, Trash2, X, Save, Eye, EyeOff, Zap } from "lucide-react";
import { api, Webhook } from "@/lib/api";

const ALL_EVENTS = ["message", "agent_error", "provision", "agent_start", "agent_stop"] as const;
const EVENT_LABELS: Record<string, string> = {
  message:     "Nachricht",
  agent_error: "Agent-Fehler",
  provision:   "Provisionierung",
  agent_start: "Agent-Start",
  agent_stop:  "Agent-Stop",
};

const EMPTY_FORM = { name: "", url: "", secret: "", events: ["message"] as string[] };

interface Props { projectId: string; }

export function WebhooksPanel({ projectId }: Props) {
  const [webhooks,  setWebhooks]  = useState<Webhook[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [showForm,  setShowForm]  = useState(false);
  const [form,      setForm]      = useState({ ...EMPTY_FORM });
  const [showSecret,setShowSecret]= useState(false);
  const [saving,    setSaving]    = useState(false);
  const [saveErr,   setSaveErr]   = useState("");
  const [deleting,  setDeleting]  = useState<string | null>(null);
  const [testing,   setTesting]   = useState<string | null>(null);
  const [testResult,setTestResult]= useState<Record<string, string>>({});

  async function load() {
    try {
      const d = await api.projectWebhooks(projectId);
      setWebhooks(d.webhooks);
      setError("");
    } catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [projectId]);

  function toggleEvent(ev: string) {
    setForm(f => ({
      ...f,
      events: f.events.includes(ev) ? f.events.filter(e => e !== ev) : [...f.events, ev],
    }));
  }

  function closeForm() { setShowForm(false); setSaveErr(""); setForm({ ...EMPTY_FORM }); setShowSecret(false); }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (form.events.length === 0) { setSaveErr("Mindestens ein Event wählen"); return; }
    setSaving(true); setSaveErr("");
    try {
      const body: Record<string, unknown> = { name: form.name, url: form.url, events: form.events };
      if (form.secret.trim()) body.secret = form.secret.trim();
      await api.createWebhook(projectId, body);
      closeForm(); await load();
    } catch(e) { setSaveErr(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  async function handleTest(wid: string) {
    setTesting(wid);
    try {
      await api.testWebhook(projectId, { webhook_id: wid });
      setTestResult(r => ({ ...r, [wid]: "✓ OK" }));
    } catch(e) {
      setTestResult(r => ({ ...r, [wid]: `✗ ${e instanceof Error ? e.message : "Fehler"}` }));
    } finally {
      setTesting(null);
      setTimeout(() => setTestResult(r => { const n = {...r}; delete n[wid]; return n; }), 4000);
    }
  }

  async function handleDelete(wid: string, name: string) {
    if (!confirm(`Webhook "${name}" löschen?`)) return;
    setDeleting(wid);
    try { await api.deleteWebhook(projectId, wid); await load(); }
    catch(e) { setError(e instanceof Error ? e.message : "Fehler"); }
    finally { setDeleting(null); }
  }

  return (
    <div className="border-t">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-muted/20">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <WebhookIcon className="h-3.5 w-3.5" />
          <span>Webhooks ({webhooks.length})</span>
        </div>
        <button onClick={() => { setShowForm(s => !s); setSaveErr(""); }}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-accent transition-colors">
          <Plus className="h-3 w-3" />Neuer Webhook
        </button>
      </div>

      {error && <p className="px-4 py-2 text-xs text-destructive">{error}</p>}

      {/* Formular */}
      {showForm && (
        <div className="border-t bg-card px-4 py-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Neuer Webhook</span>
            <button onClick={closeForm}><X className="h-4 w-4 text-muted-foreground" /></button>
          </div>
          <form onSubmit={handleSave} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Name *</label>
                <input value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))}
                  placeholder="z.B. Git-Push Trigger" required
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">URL *</label>
                <input value={form.url} onChange={e => setForm(f => ({...f, url: e.target.value}))}
                  placeholder="https://example.com/hook" required type="url"
                  className="w-full px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Secret (optional — für HMAC-SHA256 Signierung)</label>
              <div className="flex gap-2">
                <input value={form.secret} onChange={e => setForm(f => ({...f, secret: e.target.value}))}
                  type={showSecret ? "text" : "password"}
                  placeholder="Leer lassen für keine Signierung"
                  className="flex-1 px-2.5 py-1.5 text-sm border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary font-mono" />
                <button type="button" onClick={() => setShowSecret(s => !s)}
                  className="p-1.5 border rounded hover:bg-accent transition-colors text-muted-foreground">
                  {showSecret ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">Events *</label>
              <div className="flex gap-2">
                {ALL_EVENTS.map(ev => (
                  <button key={ev} type="button" onClick={() => toggleEvent(ev)}
                    className={`px-2.5 py-1 text-xs rounded border transition-colors ${
                      form.events.includes(ev)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border hover:bg-accent"
                    }`}>
                    {EVENT_LABELS[ev]}
                  </button>
                ))}
              </div>
            </div>

            {saveErr && <p className="text-xs text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm}
                className="px-3 py-1.5 text-sm border rounded hover:bg-accent transition-colors">Abbrechen</button>
              <button type="submit" disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 transition-colors">
                <Save className="h-3.5 w-3.5" />{saving ? "Speichern…" : "Webhook anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Liste */}
      {loading
        ? <div className="px-4 py-3 text-xs text-muted-foreground">Lade Webhooks…</div>
        : webhooks.length === 0 && !showForm
          ? <div className="px-4 py-3 text-xs text-muted-foreground">Keine Webhooks — leg den ersten an.</div>
          : webhooks.map(w => (
            <div key={w.id} className="border-t flex items-center gap-3 px-4 py-2.5 hover:bg-muted/10">
              <WebhookIcon className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{w.name}</span>
                  <span className="text-xs text-muted-foreground truncate max-w-[200px]">{w.url}</span>
                </div>
                <div className="flex gap-1 mt-0.5">
                  {w.events.map(ev => (
                    <span key={ev} className="text-xs px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">
                      {EVENT_LABELS[ev] ?? ev}
                    </span>
                  ))}
                </div>
              </div>
              <span className="text-xs text-muted-foreground flex-shrink-0">
                {new Date(w.created_at).toLocaleDateString("de-DE")}
              </span>
              {testResult[w.id] && (
                <span className={`text-xs flex-shrink-0 ${testResult[w.id].startsWith("✓") ? "text-green-500" : "text-destructive"}`}>
                  {testResult[w.id]}
                </span>
              )}
              <button onClick={() => handleTest(w.id)} disabled={testing === w.id}
                title="Test-Ping senden"
                className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors">
                <Zap className="h-3.5 w-3.5" />
              </button>
              <button onClick={() => handleDelete(w.id, w.name)} disabled={deleting === w.id}
                className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive disabled:opacity-50 transition-colors">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))
      }
    </div>
  );
}
