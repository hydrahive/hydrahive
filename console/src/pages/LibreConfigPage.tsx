/**
 * LibreConfigPage — FreeStyle Libre 3 Konfiguration (#912)
 * Settings-Tab für LibreLinkUp-Credentials und Grenzwerte.
 */
import { useEffect, useState } from "react";
import { Activity, Save, TestTube2, CheckCircle, AlertTriangle, Eye, EyeOff, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

const REGIONS = ["EU", "DE", "US", "AP", "AU", "JP"];

export function LibreConfigPage() {
  const [form, setForm] = useState({
    email: "", password: "", region: "EU", unit: "mmol", low: "3.9", high: "10.0",
  });
  const [configured, setConfigured] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.get<any>("/libre/config").then(cfg => {
      setConfigured(cfg?.configured ?? false);
      setForm({
        email:    cfg?.email    ?? "",
        password: cfg?.password ?? "",
        region:   cfg?.region   ?? "EU",
        unit:     cfg?.unit     ?? "mmol",
        low:      String(cfg?.low  ?? "3.9"),
        high:     String(cfg?.high ?? "10.0"),
      });
    }).catch(() => {});
  }, []);

  function set(k: string, v: string) {
    setForm(prev => ({ ...prev, [k]: v }));
  }

  async function handleSave() {
    setSaving(true); setSaveMsg(null);
    try {
      const r = await api.put<{ saved: boolean; configured: boolean }>("/libre/config", {
        ...form, low: parseFloat(form.low), high: parseFloat(form.high),
      });
      setConfigured(r.configured);
      setSaveMsg({ ok: true, text: "Gespeichert." });
    } catch (e: any) {
      setSaveMsg({ ok: false, text: e?.message ?? "Fehler beim Speichern" });
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true); setTestMsg(null);
    try {
      const r = await api.post<{ ok: boolean; host: string }>("/libre/test", {});
      setTestMsg({ ok: true, text: `Verbindung OK — ${r.host}` });
    } catch (e: any) {
      setTestMsg({ ok: false, text: e?.message ?? "Verbindung fehlgeschlagen" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-6 max-w-xl">
      <div className="flex items-center gap-2">
        <Activity className="h-5 w-5 text-emerald-400" />
        <h2 className="text-lg font-semibold">FreeStyle Libre 3</h2>
        {configured && (
          <span className="flex items-center gap-1 text-xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded-full px-2 py-0.5">
            <CheckCircle className="h-3 w-3" /> Verbunden
          </span>
        )}
      </div>

      <p className="text-sm text-muted-foreground">
        Verbinde deinen LibreLinkUp-Account um Glukosedaten im Agenten-Chat anzuzeigen.
        Lege zuerst in der LibreLink-App unter <strong>Verbindungen</strong> einen
        LibreLinkUp-Account an.
      </p>

      <div className="space-y-4">
        {/* E-Mail */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">LibreLinkUp E-Mail</label>
          <input
            type="email"
            value={form.email}
            onChange={e => set("email", e.target.value)}
            placeholder="deine@email.de"
            className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
          />
        </div>

        {/* Passwort */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Passwort</label>
          <div className="relative">
            <input
              type={showPw ? "text" : "password"}
              value={form.password}
              onChange={e => set("password", e.target.value)}
              placeholder={configured ? "leer lassen = unverändert" : "Passwort"}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 pr-9 text-sm text-white focus:outline-none focus:border-indigo-500/60"
            />
            <button
              type="button"
              onClick={() => setShowPw(s => !s)}
              className="absolute right-2.5 top-2.5 text-white/30 hover:text-white/70"
            >
              {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Region + Einheit */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Region</label>
            <select
              value={form.region}
              onChange={e => set("region", e.target.value)}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
            >
              {REGIONS.map(r => <option key={r}>{r}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Einheit</label>
            <select
              value={form.unit}
              onChange={e => set("unit", e.target.value)}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
            >
              <option value="mmol">mmol/L</option>
              <option value="mgdl">mg/dL</option>
            </select>
          </div>
        </div>

        {/* Zielbereich */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              Unterer Grenzwert ({form.unit === "mmol" ? "mmol/L" : "mg/dL"})
            </label>
            <input
              type="number"
              step="0.1"
              value={form.low}
              onChange={e => set("low", e.target.value)}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">
              Oberer Grenzwert ({form.unit === "mmol" ? "mmol/L" : "mg/dL"})
            </label>
            <input
              type="number"
              step="0.1"
              value={form.high}
              onChange={e => set("high", e.target.value)}
              className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60"
            />
          </div>
        </div>

        {/* Buttons */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={() => void handleSave()}
            disabled={saving || !form.email}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Speichern
          </button>
          <button
            onClick={() => void handleTest()}
            disabled={testing || !configured}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-4 py-2 text-sm text-white/70 hover:text-white disabled:opacity-40 transition-colors"
          >
            {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
            Verbindung testen
          </button>
        </div>

        {saveMsg && (
          <div className={`flex items-center gap-2 text-sm rounded-lg px-3 py-2 ${saveMsg.ok ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-red-500/10 text-red-400 border border-red-500/20"}`}>
            {saveMsg.ok ? <CheckCircle className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
            {saveMsg.text}
          </div>
        )}
        {testMsg && (
          <div className={`flex items-center gap-2 text-sm rounded-lg px-3 py-2 ${testMsg.ok ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-red-500/10 text-red-400 border border-red-500/20"}`}>
            {testMsg.ok ? <CheckCircle className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
            {testMsg.text}
          </div>
        )}
      </div>
    </div>
  );
}
