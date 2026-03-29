import { useEffect, useState } from "react";
import { Save, Loader2, Bell } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// Severity levels × channels matrix
const SEVERITIES = ["critical", "error", "warning", "info", "debug"] as const;
const CHANNELS    = ["matrix", "discord", "email", "telegram", "webhook"] as const;

type Severity = typeof SEVERITIES[number];
type Channel  = typeof CHANNELS[number];

type RoutingConfig = {
  [K in Severity]?: Channel[];
};

const SEVERITY_COLORS: Record<Severity, string> = {
  critical: "text-red-400",
  error:    "text-orange-400",
  warning:  "text-yellow-400",
  info:     "text-blue-400",
  debug:    "text-white/40",
};

const CHANNEL_LABELS: Record<Channel, string> = {
  matrix:   "Matrix",
  discord:  "Discord",
  email:    "E-Mail",
  telegram: "Telegram",
  webhook:  "Webhook",
};

export function NotificationRouterTab() {
  const [config,  setConfig]  = useState<RoutingConfig>({});
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [toast,   setToast]   = useState<string | null>(null);

  useEffect(() => {
    api.get<RoutingConfig>("/admin/notification-routes")
      .then(d => setConfig(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  function toggle(severity: Severity, channel: Channel) {
    setConfig(prev => {
      const cur = prev[severity] ?? [];
      return {
        ...prev,
        [severity]: cur.includes(channel)
          ? cur.filter(c => c !== channel)
          : [...cur, channel],
      };
    });
  }

  function isOn(severity: Severity, channel: Channel) {
    return (config[severity] ?? []).includes(channel);
  }

  async function save() {
    setSaving(true);
    try {
      await api.put("/admin/notification-routes", config);
      setToast("Gespeichert");
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Fehler");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="flex items-center justify-center h-full"><Loader2 className="h-8 w-8 animate-spin text-white/30" /></div>;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-indigo-400" />
          <p className="text-xs text-white/50">Welche Severity-Stufe wird über welchen Kanal gerouted?</p>
        </div>
        <div className="flex items-center gap-3">
          {toast && <span className="text-sm text-indigo-300">{toast}</span>}
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 px-3 py-1.5 text-sm text-white transition-colors">
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {saving ? "Speichere…" : "Speichern"}
          </button>
        </div>
      </div>

      {/* Matrix table */}
      <div className="flex-1 overflow-auto p-6">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="text-left text-xs font-medium text-white/40 uppercase tracking-wider pb-3 pr-4 w-28">Severity</th>
              {CHANNELS.map(ch => (
                <th key={ch} className="text-center text-xs font-medium text-white/40 uppercase tracking-wider pb-3 px-4">
                  {CHANNEL_LABELS[ch]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {SEVERITIES.map(sev => (
              <tr key={sev} className="group hover:bg-white/3 transition-colors">
                <td className="py-3 pr-4">
                  <span className={cn("text-sm font-medium capitalize", SEVERITY_COLORS[sev])}>{sev}</span>
                </td>
                {CHANNELS.map(ch => (
                  <td key={ch} className="py-3 px-4 text-center">
                    <button
                      onClick={() => toggle(sev, ch)}
                      className={cn(
                        "inline-flex h-6 w-12 items-center rounded-full border transition-all duration-200",
                        isOn(sev, ch)
                          ? "bg-indigo-600 border-indigo-500"
                          : "bg-zinc-800 border-white/10"
                      )}
                      title={`${sev} → ${CHANNEL_LABELS[ch]}`}
                    >
                      <span className={cn(
                        "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200",
                        isOn(sev, ch) ? "translate-x-7" : "translate-x-1"
                      )} />
                    </button>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        <p className="mt-6 text-xs text-white/25">
          Routing wird gespeichert unter <code className="text-white/40">/etc/hydrahive/notification_routes.json</code>.
          Kanal-Credentials werden unter Einstellungen → Integrationen konfiguriert.
        </p>
      </div>
    </div>
  );
}
