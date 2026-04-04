import { useEffect, useState } from "react";
import { Save, Mail, CheckCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

export function KasConfigPage() {
  const { t } = useTranslation();
  const [login,   setLogin]   = useState("");
  const [pw,      setPw]      = useState("");
  const [domain,  setDomain]  = useState("");
  const [smtp,    setSmtp]    = useState("");
  const [port,    setPort]    = useState("587");
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);
  const [error,   setError]   = useState("");

  useEffect(() => {
    api.getKas().then((d) => {
      if (d.configured) {
        setLogin(d.login  ?? "");
        setPw(d.password  ?? "");
        setDomain(d.default_domain ?? "");
        setSmtp(d.smtp_host  ?? "");
        setPort(String(d.smtp_port ?? 587));
      }
    }).catch(e => console.error("Failed to load KAS config", e)).finally(() => setLoading(false));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setError(""); setSaved(false);
    try {
      await api.putKas({ login, password: pw, default_domain: domain, smtp_host: smtp, smtp_port: Number(port) || 587 });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.saveError"));
    } finally { setSaving(false); }
  }

  if (loading) return <div className="p-6 text-sm text-muted-foreground">Lade KAS-Konfiguration...</div>;

  return (
    <div className="p-6 max-w-xl space-y-6">
      <div className="flex items-center gap-3">
        <Mail size={20} className="text-muted-foreground" />
        <div>
          <h2 className="text-base font-semibold text-foreground">All-Inkl KAS</h2>
          <p className="text-xs text-muted-foreground">Zugangsdaten für automatische Postfach-Anlage via KAS API</p>
        </div>
      </div>

      <form onSubmit={save} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm text-foreground">KAS-Login</label>
            <input value={login} onChange={e => setLogin(e.target.value)}
              placeholder="w012345e"
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm text-foreground">KAS-Passwort</label>
            <input type="password" value={pw} onChange={e => setPw(e.target.value)}
              placeholder="••••••••"
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm text-foreground">Standard-Domain</label>
          <input value={domain} onChange={e => setDomain(e.target.value)}
            placeholder="deine-domain.de"
            className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          <p className="text-xs text-muted-foreground">Wird verwendet wenn beim Mailanlegen keine Domain angegeben wird</p>
        </div>

        <div className="grid grid-cols-[1fr_120px] gap-4">
          <div className="space-y-1.5">
            <label className="text-sm text-foreground">SMTP-Host</label>
            <input value={smtp} onChange={e => setSmtp(e.target.value)}
              placeholder="dd12345.kasserver.com"
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm text-foreground">SMTP-Port</label>
            <input value={port} onChange={e => setPort(e.target.value)}
              placeholder="587"
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <div className="flex items-center gap-3 pt-1">
          <button type="submit" disabled={saving || !login || !pw}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-medium transition-colors">
            <Save size={14} />
            {saving ? t("common.saving") : t("common.save")}
          </button>
          {saved && (
            <span className="flex items-center gap-1.5 text-sm text-green-400">
              <CheckCircle size={14} /> {t("common.saved")}
            </span>
          )}
        </div>
      </form>

      <div className="rounded-xl border border-border bg-muted/40 p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-medium text-foreground">Wo finde ich diese Daten?</p>
        <p>Login und Passwort: KAS-Panel → Zugangsdaten (nicht der E-Mail-Login, sondern der KAS-API-Zugang)</p>
        <p>SMTP-Host: KAS-Panel → E-Mail → Server-Einstellungen</p>
      </div>
    </div>
  );
}
