import { useEffect, useState } from "react";
import { Webhook as WebhookIcon, Plus, Trash2, X, Save, Eye, EyeOff, Zap, Radar } from "lucide-react";
import { api, Webhook } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const ALL_EVENTS = ["message", "agent_error", "provision", "agent_start", "agent_stop"] as const;
const EVENT_LABELS: Record<string, string> = {
  message: "Nachricht",
  agent_error: "Agent-Fehler",
  provision: "Provisionierung",
  agent_start: "Agent-Start",
  agent_stop: "Agent-Stop",
};

const EMPTY_FORM = { name: "", url: "", secret: "", events: ["message"] as string[] };

interface Props { projectId: string; }

export function WebhooksPanel({ projectId }: Props) {
  const { t } = useTranslation();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    try {
      const d = await api.projectWebhooks(projectId);
      setWebhooks(d.webhooks);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [projectId]);

  function toggleEvent(ev: string) {
    setForm((f) => ({
      ...f,
      events: f.events.includes(ev) ? f.events.filter((e) => e !== ev) : [...f.events, ev],
    }));
  }

  function closeForm() {
    setShowForm(false);
    setSaveErr("");
    setForm({ ...EMPTY_FORM });
    setShowSecret(false);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (form.events.length === 0) {
      setSaveErr("Mindestens ein Event waehlen");
      return;
    }
    setSaving(true);
    setSaveErr("");
    try {
      const body: Record<string, unknown> = { name: form.name, url: form.url, events: form.events };
      if (form.secret.trim()) body.secret = form.secret.trim();
      await api.createWebhook(projectId, body);
      closeForm();
      await load();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  async function handleTest(wid: string) {
    setTesting(wid);
    try {
      await api.testWebhook(projectId, { webhook_id: wid });
      setTestResult((r) => ({ ...r, [wid]: "OK" }));
    } catch (e) {
      setTestResult((r) => ({ ...r, [wid]: e instanceof Error ? e.message : t("common.error") }));
    } finally {
      setTesting(null);
      setTimeout(() => setTestResult((r) => {
        const n = { ...r };
        delete n[wid];
        return n;
      }), 4000);
    }
  }

  function handleDelete(wid: string, name: string) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("common.confirmDelete", { name }),
      action: async () => {
        setDeleting(wid);
        try {
          await api.deleteWebhook(projectId, wid);
          await load();
        } catch (e) {
          setError(e instanceof Error ? e.message : t("common.error"));
        } finally {
          setDeleting(null);
        }
      },
    });
  }

  return (
    <>
    <div className="border-t bg-muted/10 px-5 py-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <WebhookIcon className="h-4 w-4 text-primary" />
            <h3 className="text-base font-semibold tracking-tight">Webhooks</h3>
            <span className="status-pill">{webhooks.length}</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">Externe Ziele fuer Projekt-Events. Test-Pings und HMAC-Signierung bleiben erhalten.</p>
        </div>
        <button onClick={() => { setShowForm((s) => !s); setSaveErr(""); }} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">
          <Plus className="h-4 w-4" />
          {t("common.new")} Webhook
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}

      {showForm && (
        <div className="app-panel mt-5 p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="metric-kicker">Webhook</p>
              <h4 className="mt-2 text-lg font-semibold tracking-tight">Neuen Endpoint anlegen</h4>
            </div>
            <button onClick={closeForm} className="rounded-xl p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground"><X className="h-4 w-4" /></button>
          </div>
          <form onSubmit={handleSave} className="mt-5 space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Name *</label>
                <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="z.B. Git-Push Trigger" required className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">URL *</label>
                <input value={form.url} onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))} placeholder="https://example.com/hook" required type="url" className="w-full rounded-2xl border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Secret</label>
              <div className="flex gap-2">
                <input value={form.secret} onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))} type={showSecret ? "text" : "password"} placeholder="Leer lassen fuer keine Signierung" className="flex-1 rounded-2xl border bg-background px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                <button type="button" onClick={() => setShowSecret((s) => !s)} className="rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent">
                  {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Events *</label>
              <div className="flex flex-wrap gap-2">
                {ALL_EVENTS.map((ev) => (
                  <button key={ev} type="button" onClick={() => toggleEvent(ev)} className={`rounded-full border px-3 py-1.5 text-xs transition ${form.events.includes(ev) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent"}`}>
                    {EVENT_LABELS[ev]}
                  </button>
                ))}
              </div>
            </div>

            {saveErr && <p className="text-sm text-destructive">{saveErr}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeForm} className="rounded-2xl border px-4 py-2 text-sm transition hover:bg-accent">{t("common.cancel")}</button>
              <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
                <Save className="h-4 w-4" />
                {saving ? t("common.saving") : "Webhook anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="mt-5 space-y-3">
        {loading ? (
          <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="metric-card h-24 animate-pulse" />)}</div>
        ) : webhooks.length === 0 && !showForm ? (
          <div className="section-card py-10 text-center text-sm text-muted-foreground">
            <Radar className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="mt-3">Keine Webhooks. Leg den ersten an.</p>
          </div>
        ) : (
          webhooks.map((w) => (
            <div key={w.id} className="app-panel p-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">{w.name}</span>
                    <span className="status-pill">{new Date(w.created_at).toLocaleDateString("de-DE")}</span>
                    {testResult[w.id] && <span className={testResult[w.id] === "OK" ? "status-pill status-pill-ok" : "status-pill bg-destructive/10 text-destructive"}>{testResult[w.id] === "OK" ? "Test OK" : testResult[w.id]}</span>}
                  </div>
                  <p className="mt-2 truncate text-sm text-muted-foreground">{w.url}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {w.events.map((ev) => <span key={ev} className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">{EVENT_LABELS[ev] ?? ev}</span>)}
                  </div>
                </div>
                <div className="flex gap-2 md:flex-col">
                  <button onClick={() => handleTest(w.id)} disabled={testing === w.id} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition hover:bg-accent disabled:opacity-50">
                    <Zap className="h-4 w-4" />
                    Test
                  </button>
                  <button onClick={() => handleDelete(w.id, w.name)} disabled={deleting === w.id} className="inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm text-muted-foreground transition hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50">
                    <Trash2 className="h-4 w-4" />
                    {t("common.delete")}
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
    <ConfirmDialog
      open={!!confirmState}
      title={confirmState?.title || ""}
      message={confirmState?.message || ""}
      onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
      onCancel={() => setConfirmState(null)}
      variant="danger"
    />
    </>
  );
}
