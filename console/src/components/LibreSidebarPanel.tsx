/**
 * LibreSidebarPanel — FreeStyle Libre 3 (#912)
 * Alles an einem Ort: Config-Formular wenn nicht eingerichtet, Daten wenn aktiv.
 */
import { useEffect, useState, useCallback } from "react";
import { Activity, RefreshCw, AlertTriangle, Save, TestTube2, CheckCircle, Eye, EyeOff, Loader2, Settings2 } from "lucide-react";
import { api } from "@/lib/api";

interface GlucoseReading {
  value: number; unit: string; trend: string; trend_num: number;
  timestamp: string; color: "green" | "yellow" | "red";
}
interface HistoryEntry {
  value: number; unit: string; trend: string;
  color: "green" | "yellow" | "red"; timestamp: string;
}

const COLOR_CLASS: Record<string, string> = {
  green: "text-emerald-400", yellow: "text-yellow-400", red: "text-red-400",
};
const COLOR_BG: Record<string, string> = {
  green: "bg-emerald-500/10 border-emerald-500/20",
  yellow: "bg-yellow-500/10 border-yellow-500/20",
  red: "bg-red-500/10 border-red-500/20",
};
const REGIONS = ["EU", "DE", "US", "AP", "AU", "JP"];

function formatTs(ts: string): string {
  if (!ts) return "—";
  try { return new Date(ts.replace(" ", "T")).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }); }
  catch { return ts.slice(11, 16) || "—"; }
}

function ConfigForm({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState({ email: "", password: "", region: "EU", unit: "mmol", low: "3.9", high: "10.0" });
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.get<any>("/libre/config").then(cfg => {
      if (cfg?.email) setForm(f => ({ ...f, email: cfg.email, password: cfg.password ?? "", region: cfg.region ?? "EU", unit: cfg.unit ?? "mmol", low: String(cfg.low ?? "3.9"), high: String(cfg.high ?? "10.0") }));
    }).catch(() => {});
  }, []);

  function set(k: string, v: string) { setForm(p => ({ ...p, [k]: v })); }

  async function save() {
    setSaving(true); setMsg(null);
    try {
      const r = await api.put<{ configured: boolean }>("/libre/config", { ...form, low: parseFloat(form.low), high: parseFloat(form.high) });
      if (r.configured) onSaved();
      else setMsg({ ok: false, text: "E-Mail oder Passwort fehlt" });
    } catch (e: any) { setMsg({ ok: false, text: e?.message ?? "Fehler" }); }
    finally { setSaving(false); }
  }

  async function test() {
    setTesting(true); setMsg(null);
    try {
      const r = await api.post<{ host: string }>("/libre/test", {});
      setMsg({ ok: true, text: `OK — ${r.host}` });
    } catch (e: any) { setMsg({ ok: false, text: e?.message ?? "Verbindung fehlgeschlagen" }); }
    finally { setTesting(false); }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">LibreLinkUp-Account verbinden (in der LibreLink-App unter <strong>Verbindungen</strong> anlegen).</p>
      <input type="email" value={form.email} onChange={e => set("email", e.target.value)} placeholder="E-Mail"
        className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
      <div className="relative">
        <input type={showPw ? "text" : "password"} value={form.password} onChange={e => set("password", e.target.value)} placeholder="Passwort"
          className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 pr-9 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
        <button type="button" onClick={() => setShowPw(s => !s)} className="absolute right-2.5 top-2.5 text-white/30 hover:text-white/60">
          {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <select value={form.region} onChange={e => set("region", e.target.value)}
          className="rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
          {REGIONS.map(r => <option key={r}>{r}</option>)}
        </select>
        <select value={form.unit} onChange={e => set("unit", e.target.value)}
          className="rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60">
          <option value="mmol">mmol/L</option>
          <option value="mgdl">mg/dL</option>
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input type="number" step="0.1" value={form.low} onChange={e => set("low", e.target.value)} placeholder="Unterer Grenzwert"
          className="rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
        <input type="number" step="0.1" value={form.high} onChange={e => set("high", e.target.value)} placeholder="Oberer Grenzwert"
          className="rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
      </div>
      <div className="flex gap-2">
        <button onClick={() => void save()} disabled={saving || !form.email}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-40">
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} Speichern
        </button>
        <button onClick={() => void test()} disabled={testing || !form.email}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/60 hover:text-white disabled:opacity-40">
          {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <TestTube2 className="h-3 w-3" />} Testen
        </button>
      </div>
      {msg && (
        <div className={`flex items-center gap-1.5 text-xs rounded-lg px-2.5 py-1.5 ${msg.ok ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-red-500/10 text-red-400 border border-red-500/20"}`}>
          {msg.ok ? <CheckCircle className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />} {msg.text}
        </div>
      )}
    </div>
  );
}

export function LibreSidebarPanel() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [current, setCurrent] = useState<GlucoseReading | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    api.get<{ configured: boolean }>("/libre/status")
      .then(r => setConfigured(r?.configured ?? false)).catch(() => setConfigured(false));
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [cur, hist] = await Promise.all([
        api.get<GlucoseReading>("/libre/current"),
        api.get<{ readings: HistoryEntry[] }>("/libre/history?hours=8").then(r => r.readings ?? []),
      ]);
      setCurrent(cur);
      setHistory(hist.slice(-24).reverse());
      setLastRefresh(new Date());
    } catch (e: any) { setError(e?.message ?? "Fehler"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!configured) return;
    void loadData();
    const iv = setInterval(() => void loadData(), 5 * 60 * 1000);
    return () => clearInterval(iv);
  }, [configured, loadData]);

  if (configured === null) return (
    <div className="flex items-center justify-center py-8 text-white/30">
      <Loader2 className="h-4 w-4 animate-spin" />
    </div>
  );

  if (!configured || showSettings) return (
    <div className="space-y-3">
      {showSettings && (
        <button onClick={() => setShowSettings(false)} className="text-xs text-white/40 hover:text-white">← Zurück</button>
      )}
      <ConfigForm onSaved={() => { setConfigured(true); setShowSettings(false); void loadData(); }} />
    </div>
  );

  return (
    <div className="space-y-3">
      {loading && !current ? (
        <div className="flex items-center justify-center py-8 text-white/30">
          <RefreshCw className="h-4 w-4 animate-spin mr-2" /><span className="text-xs">Lade…</span>
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-400 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />{error}
        </div>
      ) : current ? (
        <>
          <div className={`rounded-2xl border p-4 ${COLOR_BG[current.color]}`}>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-white/40 flex items-center gap-1">
                <Activity className="h-3 w-3" /> Aktuell
              </p>
              <div className="flex items-center gap-1">
                <button onClick={() => void loadData()} className="text-white/30 hover:text-white/70"><RefreshCw className="h-3 w-3" /></button>
                <button onClick={() => setShowSettings(true)} className="text-white/30 hover:text-white/70"><Settings2 className="h-3 w-3" /></button>
              </div>
            </div>
            <div className="flex items-end gap-2">
              <span className={`text-4xl font-bold tabular-nums ${COLOR_CLASS[current.color]}`}>{current.value}</span>
              <span className="text-lg text-white/50 mb-0.5">{current.unit}</span>
              <span className={`text-2xl mb-0.5 ${COLOR_CLASS[current.color]}`}>{current.trend}</span>
            </div>
            <p className="text-[0.65rem] text-white/30 mt-1">{formatTs(current.timestamp)}</p>
          </div>

          {history.length > 0 && (
            <div className="rounded-2xl border bg-background/75 p-3">
              <p className="text-[0.65rem] uppercase tracking-[0.16em] text-white/40 mb-2">Verlauf (8h)</p>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {history.map((r, i) => (
                  <div key={i} className="flex items-center justify-between text-xs py-0.5">
                    <span className="text-white/30 font-mono w-10">{formatTs(r.timestamp)}</span>
                    <span className={`font-medium tabular-nums ${COLOR_CLASS[r.color]}`}>{r.value} <span className="text-white/20">{r.unit}</span></span>
                    <span className={`w-5 text-center ${COLOR_CLASS[r.color]}`}>{r.trend}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {lastRefresh && (
            <p className="text-[0.6rem] text-white/20 text-center">
              {lastRefresh.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} · alle 5 min
            </p>
          )}
        </>
      ) : null}
    </div>
  );
}
