import { useEffect, useState } from "react";
import { Globe, Plus, Trash2, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronUp, Eye, EyeOff, Send } from "lucide-react";
import { api, A2APeer, A2APeersResponse, A2ATestResult } from "@/lib/api";
import { useTranslation } from "react-i18next";

export function A2APage() {
  const { t } = useTranslation();
  const [data,       setData]       = useState<A2APeersResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");

  const [showForm,   setShowForm]   = useState(false);
  const [form,       setForm]       = useState<A2APeer>({ name: "", url: "", secret: "", description: "" });
  const [saving,     setSaving]     = useState(false);
  const [saveError,  setSaveError]  = useState("");

  const [secretVal,  setSecretVal]  = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [savingSec,  setSavingSec]  = useState(false);

  const [testResults, setTestResults] = useState<Record<string, A2ATestResult>>({});
  const [testing,     setTesting]     = useState<string | null>(null);

  const [deletingPeer, setDeletingPeer] = useState<string | null>(null);

  const [sendForm,    setSendForm]    = useState({ peer: "", agent_id: "", message: "" });
  const [sending,     setSendingTask] = useState(false);
  const [sendResult,  setSendResult]  = useState<{ ok: boolean; response: string; error?: string } | null>(null);

  async function load() {
    try {
      const d = await api.a2aPeers();
      setData(d);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("a2a.loadError"));
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function handleSaveSecret() {
    setSavingSec(true);
    try {
      await api.a2aSetSecret(secretVal);
      await load();
      setSecretVal("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("a2a.saveError"));
    } finally { setSavingSec(false); }
  }

  async function handleUpsert(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.url.trim()) return;
    setSaving(true);
    setSaveError("");
    try {
      await api.a2aUpsertPeer(form);
      setForm({ name: "", url: "", secret: "", description: "" });
      setShowForm(false);
      await load();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t("a2a.saveError"));
    } finally { setSaving(false); }
  }

  async function handleDelete(name: string) {
    setDeletingPeer(name);
    try {
      await api.a2aDeletePeer(name);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("a2a.deleteError"));
    } finally { setDeletingPeer(null); }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!sendForm.peer || !sendForm.agent_id.trim() || !sendForm.message.trim()) return;
    setSendingTask(true);
    setSendResult(null);
    try {
      const r = await api.a2aSendTask(sendForm.peer, sendForm.agent_id.trim(), sendForm.message.trim());
      setSendResult({ ok: true, response: r.response });
    } catch (e) {
      setSendResult({ ok: false, response: "", error: e instanceof Error ? e.message : "Fehler" });
    } finally { setSendingTask(false); }
  }

  async function handleTest(name: string) {
    setTesting(name);
    try {
      const r = await api.a2aTestPeer(name);
      setTestResults(prev => ({ ...prev, [name]: r }));
    } catch (e) {
      setTestResults(prev => ({
        ...prev,
        [name]: { ok: false, status: 0, peer_name: "", peer_version: "", agents: [], error: e instanceof Error ? e.message : "?" },
      }));
    } finally { setTesting(null); }
  }

  if (loading) return <div className="p-8 text-sm text-muted-foreground">{t("a2a.loading")}</div>;

  return (
    <div className="space-y-6">
      <section className="hero-panel">
        <div className="relative z-10 shell-grid">
          <div className="space-y-3 lg:col-span-8">
            <div className="flex items-center gap-3">
              <span className={`status-pill ${data?.has_secret ? "status-pill-ok" : "status-pill-warn"}`}>
                {data?.has_secret ? t("a2a.configured") : t("a2a.notConfigured")}
              </span>
            </div>
            <h1 className="shell-title">{t("a2a.title")}</h1>
            <p className="shell-copy max-w-2xl">{t("a2a.subtitle")}</p>
          </div>
          <div className="lg:col-span-4">
            <div className="app-panel app-panel-muted p-5 space-y-3">
              <p className="text-sm font-medium">{t("a2a.incomingSecret")}</p>
              <p className="text-xs text-muted-foreground">{t("a2a.incomingSecretDesc")}</p>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={showSecret ? "text" : "password"}
                    value={secretVal}
                    onChange={e => setSecretVal(e.target.value)}
                    placeholder={data?.has_secret ? t("a2a.secretSet") : t("a2a.enterSecret")}
                    className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm pr-8 focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                  <button
                    type="button"
                    onClick={() => setShowSecret(s => !s)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    {showSecret ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
                <button
                  onClick={handleSaveSecret}
                  disabled={savingSec || !secretVal.trim()}
                  className="rounded-xl bg-primary px-3 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                >
                  {t("a2a.save")}
                </button>
              </div>
              <p className="text-xs text-muted-foreground font-mono">{t("a2a.endpointHint")}: /.well-known/agent.json</p>
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>
      )}

      <div className="section-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">{t("a2a.peers")}</h2>
          <button
            onClick={() => setShowForm(f => !f)}
            className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs hover:bg-accent transition-colors"
          >
            {showForm ? <ChevronUp className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
            {showForm ? t("a2a.cancel") : t("a2a.addPeer")}
          </button>
        </div>

        {showForm && (
          <form onSubmit={handleUpsert} className="rounded-xl border bg-muted/20 p-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">{t("a2a.peerName")} *</label>
                <input
                  required
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="prod"
                  className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">{t("a2a.peerUrl")} *</label>
                <input
                  required
                  value={form.url}
                  onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
                  placeholder="http://192.168.178.181"
                  className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("a2a.peerSecret")} ({t("a2a.peerSecretHint")})</label>
              <input
                type="password"
                value={form.secret}
                onChange={e => setForm(f => ({ ...f, secret: e.target.value }))}
                placeholder={t("a2a.peerSecretPlaceholder")}
                className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">{t("a2a.description")}</label>
              <input
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder={t("a2a.descriptionPlaceholder")}
                className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={saving}
                className="rounded-xl bg-primary px-4 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                {saving ? t("a2a.saving") : t("a2a.save")}
              </button>
              <button
                type="button"
                onClick={() => { setShowForm(false); setSaveError(""); }}
                className="rounded-xl border px-4 py-1.5 text-sm hover:bg-accent"
              >
                {t("a2a.cancel")}
              </button>
            </div>
          </form>
        )}

        {(data?.peers ?? []).length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            <Globe className="h-8 w-8 mx-auto mb-2 opacity-20" />
            {t("a2a.noPeers")}
          </div>
        ) : (
          <div className="space-y-3">
            {data!.peers.map(peer => {
              const tr = testResults[peer.name];
              return (
                <div key={peer.name} className="rounded-xl border bg-muted/20 p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{peer.name}</span>
                        {peer.description && (
                          <span className="text-xs text-muted-foreground">— {peer.description}</span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground font-mono truncate">{peer.url}</p>
                    </div>
                    <div className="flex gap-2 flex-shrink-0">
                      <button
                        onClick={() => handleTest(peer.name)}
                        disabled={testing === peer.name}
                        className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-40"
                      >
                        <RefreshCw className={`h-3 w-3 ${testing === peer.name ? "animate-spin" : ""}`} />
                        {t("a2a.test")}
                      </button>
                      <button
                        onClick={() => handleDelete(peer.name)}
                        disabled={deletingPeer === peer.name}
                        className="inline-flex items-center gap-1.5 rounded-xl border border-destructive/30 px-3 py-1.5 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-40"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>

                  {tr && (
                    <div className={`rounded-lg px-3 py-2 text-xs ${tr.ok ? "bg-green-500/10 text-green-600 dark:text-green-400" : "bg-destructive/10 text-destructive"}`}>
                      {tr.ok ? (
                        <div className="space-y-1">
                          <div className="flex items-center gap-1.5">
                            <CheckCircle className="h-3.5 w-3.5" />
                            <span>{t("a2a.testOk")} — {tr.peer_name} v{tr.peer_version}</span>
                          </div>
                          {tr.agents.length > 0 && (
                            <p className="text-muted-foreground">
                              {t("a2a.remoteAgents")}: {tr.agents.map(a => a.name || a.id).join(", ")}
                            </p>
                          )}
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <XCircle className="h-3.5 w-3.5" />
                          <span>{t("a2a.testFail")}: {tr.error || `HTTP ${tr.status}`}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Test-Task senden ─────────────────────────────────────────── */}
      {(data?.peers ?? []).length > 0 && (
        <div className="section-card p-5 space-y-4">
          <h2 className="text-sm font-semibold">Test-Task senden</h2>
          <p className="text-xs text-muted-foreground">Sendet einen echten Task an einen Agenten auf dem Remote-Peer und zeigt die Antwort.</p>
          <form onSubmit={handleSend} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Peer</label>
                <select
                  value={sendForm.peer}
                  onChange={e => setSendForm(f => ({ ...f, peer: e.target.value }))}
                  className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  required
                >
                  <option value="">— Peer wählen —</option>
                  {data!.peers.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Agent-ID auf Remote</label>
                <input
                  value={sendForm.agent_id}
                  onChange={e => setSendForm(f => ({ ...f, agent_id: e.target.value }))}
                  placeholder="z.B. castiel"
                  className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  required
                />
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Nachricht</label>
              <textarea
                value={sendForm.message}
                onChange={e => setSendForm(f => ({ ...f, message: e.target.value }))}
                placeholder="Sag etwas kurzes, z.B. 'Antworte mit einem Satz über dich.'"
                rows={2}
                className="w-full rounded-xl border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                required
              />
            </div>
            <button
              type="submit"
              disabled={sending || !sendForm.peer || !sendForm.agent_id.trim() || !sendForm.message.trim()}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-1.5 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            >
              <Send className="h-3.5 w-3.5" />
              {sending ? "Sende…" : "Task senden"}
            </button>
          </form>
          {sendResult && (
            <div className={`rounded-xl px-4 py-3 text-sm ${sendResult.ok ? "bg-green-500/10 text-green-700 dark:text-green-400" : "bg-destructive/10 text-destructive"}`}>
              {sendResult.ok ? (
                <>
                  <div className="flex items-center gap-1.5 mb-2 font-medium text-xs">
                    <CheckCircle className="h-3.5 w-3.5" /> Antwort vom Remote-Agent:
                  </div>
                  <p className="whitespace-pre-wrap text-xs">{typeof sendResult.response === "string" ? sendResult.response : JSON.stringify(sendResult.response, null, 2) || "(leer)"}</p>
                </>
              ) : (
                <div className="flex items-center gap-1.5">
                  <XCircle className="h-3.5 w-3.5" /> {sendResult.error}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="section-card p-5 space-y-3">
        <h2 className="text-sm font-semibold">{t("a2a.toolUsage")}</h2>
        <p className="text-xs text-muted-foreground">{t("a2a.toolUsageDesc")}</p>
        <pre className="text-xs bg-muted/40 rounded-lg p-3 overflow-x-auto font-mono">
{`tools:
  - remote_agent`}
        </pre>
        <p className="text-xs text-muted-foreground">{t("a2a.toolExample")}</p>
        <pre className="text-xs bg-muted/40 rounded-lg p-3 overflow-x-auto font-mono whitespace-pre-wrap">
{`remote_agent(peer="prod", target="castiel", message="Wie ist der aktuelle Status?")`}
        </pre>
      </div>
    </div>
  );
}
