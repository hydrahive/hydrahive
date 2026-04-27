import { useEffect, useState } from "react";
import { Moon, Play, RefreshCw, Save, CheckCircle, Clock } from "lucide-react";
import { dreamApi, DreamConfig, DreamAgentStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function DreamPage() {
  const { t } = useTranslation();

  const [config, setConfig]       = useState<DreamConfig | null>(null);
  const [status, setStatus]       = useState<DreamAgentStatus[]>([]);
  const [saving, setSaving]       = useState(false);
  const [triggering, setTrigger]  = useState<string | "all" | null>(null);
  const [toast, setToast]         = useState<string | null>(null);
  const [loadingCfg, setLoadingCfg] = useState(true);
  const [loadingSt,  setLoadingSt]  = useState(true);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }

  async function loadConfig() {
    setLoadingCfg(true);
    try { setConfig(await dreamApi.getConfig()); }
    finally { setLoadingCfg(false); }
  }

  async function loadStatus() {
    setLoadingSt(true);
    try { setStatus(await dreamApi.getStatus()); }
    finally { setLoadingSt(false); }
  }

  useEffect(() => { loadConfig(); loadStatus(); }, []);

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    try {
      await dreamApi.saveConfig(config);
      showToast("Konfiguration gespeichert");
    } catch { showToast("Fehler beim Speichern"); }
    finally { setSaving(false); }
  }

  async function handleRun(agentId?: string) {
    setTrigger(agentId ?? "all");
    try {
      const res = await dreamApi.runNow(agentId);
      const triggered = res.triggered?.length ?? 0;
      showToast(triggered > 0 ? `Dream ausgelöst für ${triggered} Agent(en)` : "Keine Agenten bereit (Gates nicht erfüllt)");
      await loadStatus();
    } catch { showToast("Fehler beim Auslösen"); }
    finally { setTrigger(null); }
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Moon className="h-6 w-6 text-indigo-400" />
          <div>
            <h1 className="text-xl font-semibold">AutoDream</h1>
            <p className="text-sm text-muted-foreground">
              Automatische Memory-Konsolidierung aus Session-Transcripts
            </p>
          </div>
        </div>
        <button
          onClick={() => handleRun()}
          disabled={triggering !== null}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium disabled:opacity-50 transition-colors"
        >
          {triggering === "all"
            ? <RefreshCw className="h-4 w-4 animate-spin" />
            : <Play className="h-4 w-4" />}
          Alle jetzt ausführen
        </button>
      </div>

      {/* Toast */}
      {toast && (
        <div className="flex items-center gap-2 rounded-lg border border-green-500/30 bg-green-500/10 px-4 py-2 text-sm text-green-400">
          <CheckCircle className="h-4 w-4" />
          {toast}
        </div>
      )}

      {/* Config */}
      <section className="rounded-xl border bg-card p-5 space-y-5">
        <h2 className="font-medium text-sm uppercase tracking-wide text-muted-foreground">Konfiguration</h2>

        {loadingCfg || !config ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <RefreshCw className="h-4 w-4 animate-spin" /> Lädt…
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            {/* Enabled Toggle */}
            <label className="flex items-center justify-between col-span-full">
              <span className="text-sm">AutoDream aktiviert</span>
              <button
                type="button"
                role="switch"
                aria-checked={config.enabled}
                onClick={() => setConfig({ ...config, enabled: !config.enabled })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                  config.enabled ? "bg-indigo-600" : "bg-muted"
                }`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  config.enabled ? "translate-x-6" : "translate-x-1"
                }`} />
              </button>
            </label>

            {/* min_hours */}
            <div className="space-y-1">
              <label className="text-sm text-muted-foreground">Mindest-Stunden zwischen Dreams</label>
              <input
                type="number" min={1} max={168}
                value={config.min_hours}
                onChange={e => setConfig({ ...config, min_hours: +e.target.value })}
                className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            {/* min_sessions */}
            <div className="space-y-1">
              <label className="text-sm text-muted-foreground">Mindest-Sessions seit letztem Dream</label>
              <input
                type="number" min={1} max={100}
                value={config.min_sessions}
                onChange={e => setConfig({ ...config, min_sessions: +e.target.value })}
                className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            {/* check_interval_seconds */}
            <div className="space-y-1">
              <label className="text-sm text-muted-foreground">Check-Intervall (Sekunden)</label>
              <input
                type="number" min={60} max={86400}
                value={config.check_interval_seconds}
                onChange={e => setConfig({ ...config, check_interval_seconds: +e.target.value })}
                className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            {/* max_transcript_chars */}
            <div className="space-y-1">
              <label className="text-sm text-muted-foreground">Max. Transcript-Zeichen</label>
              <input
                type="number" min={1000} max={200000} step={1000}
                value={config.max_transcript_chars}
                onChange={e => setConfig({ ...config, max_transcript_chars: +e.target.value })}
                className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            {/* summary_model */}
            <div className="space-y-1 col-span-full">
              <label className="text-sm text-muted-foreground">Summary-Modell</label>
              <input
                type="text"
                value={config.summary_model}
                onChange={e => setConfig({ ...config, summary_model: e.target.value })}
                placeholder="z.B. claude-haiku-4-5"
                className="w-full rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>
        )}

        <div className="flex justify-end pt-1">
          <button
            onClick={handleSave}
            disabled={saving || loadingCfg || !config}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50 hover:bg-primary/90 transition-colors"
          >
            {saving ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Speichern
          </button>
        </div>
      </section>

      {/* Status-Tabelle */}
      <section className="rounded-xl border bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-sm uppercase tracking-wide text-muted-foreground">Agent-Status</h2>
          <button
            onClick={loadStatus}
            disabled={loadingSt}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loadingSt ? "animate-spin" : ""}`} />
            Aktualisieren
          </button>
        </div>

        {loadingSt ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
            <RefreshCw className="h-4 w-4 animate-spin" /> Lädt…
          </div>
        ) : status.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">Keine Agenten gefunden.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-muted-foreground text-xs uppercase tracking-wide">
                  <th className="text-left py-2 pr-4 font-medium">Agent</th>
                  <th className="text-left py-2 pr-4 font-medium">Letzter Dream</th>
                  <th className="text-left py-2 pr-4 font-medium">Vor (Std)</th>
                  <th className="text-left py-2 pr-4 font-medium">Anzahl</th>
                  <th className="text-right py-2 font-medium">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {status.map(agent => (
                  <tr key={agent.agent_id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    <td className="py-2.5 pr-4 font-mono text-xs">{agent.agent_id}</td>
                    <td className="py-2.5 pr-4 text-xs text-muted-foreground">
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3 w-3 shrink-0" />
                        {formatDate(agent.last_dream_at)}
                      </div>
                    </td>
                    <td className="py-2.5 pr-4 text-xs">
                      {agent.hours_since_dream !== null
                        ? `${agent.hours_since_dream.toFixed(1)} h`
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-4 text-xs">{agent.dream_count}</td>
                    <td className="py-2.5 text-right">
                      <button
                        onClick={() => handleRun(agent.agent_id)}
                        disabled={triggering !== null}
                        className="flex items-center gap-1 ml-auto px-2.5 py-1 rounded-md border text-xs hover:bg-muted transition-colors disabled:opacity-50"
                      >
                        {triggering === agent.agent_id
                          ? <RefreshCw className="h-3 w-3 animate-spin" />
                          : <Play className="h-3 w-3" />}
                        Jetzt
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
