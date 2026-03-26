import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle, ChevronRight, Cpu, Key, Mail, Rocket, SkipForward } from "lucide-react";
import { api } from "@/lib/api";

type Step = "welcome" | "apikey" | "llm" | "kas" | "done";

const STEPS: Step[] = ["welcome", "apikey", "llm", "kas", "done"];

function StepIndicator({ current }: { current: Step }) {
  const labels = ["Start", "API-Key", "LLM", "Mail", "Fertig"];
  const idx = STEPS.indexOf(current);
  return (
    <div className="flex items-center justify-center gap-2 mb-8">
      {STEPS.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <div className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium transition-colors ${
            i < idx ? "bg-primary text-primary-foreground" :
            i === idx ? "bg-primary text-primary-foreground ring-2 ring-primary/30" :
            "bg-muted text-muted-foreground"
          }`}>
            {i < idx ? <CheckCircle className="h-4 w-4" /> : i + 1}
          </div>
          {i < STEPS.length - 1 && <div className={`h-px w-6 ${i < idx ? "bg-primary" : "bg-muted"}`} />}
        </div>
      ))}
      <span className="ml-2 text-xs text-muted-foreground">{labels[idx]}</span>
    </div>
  );
}

function WelcomeStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="space-y-6 text-center">
      <div className="flex justify-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
          <Rocket className="h-8 w-8 text-primary" />
        </div>
      </div>
      <div>
        <h2 className="text-2xl font-bold">Willkommen bei HydraHive</h2>
        <p className="mt-2 text-muted-foreground">
          Dieser Assistent führt dich durch die Grundkonfiguration.
          Du kannst alle Schritte auch später in den Einstellungen ändern.
        </p>
      </div>
      <div className="grid gap-3 text-left text-sm">
        {[
          { icon: <Key className="h-4 w-4" />, label: "API-Schlüssel", desc: "Anthropic- oder OpenAI-Schlüssel für Claude / GPT" },
          { icon: <Cpu className="h-4 w-4" />, label: "LLM-Modell", desc: "Ollama lokal oder Standard-Modell für System-Agenten" },
          { icon: <Mail className="h-4 w-4" />, label: "Mail (All-Inkl)", desc: "KAS-Zugangsdaten für automatische Postfach-Anlage" },
        ].map((item) => (
          <div key={item.label} className="flex items-start gap-3 rounded-xl border bg-muted/30 p-3">
            <span className="mt-0.5 text-primary">{item.icon}</span>
            <div>
              <p className="font-medium">{item.label}</p>
              <p className="text-muted-foreground">{item.desc}</p>
            </div>
          </div>
        ))}
      </div>
      <button onClick={onNext} className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors">
        Einrichten <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

function ApiKeyStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey,    setOpenaiKey]    = useState("");
  const [saving,       setSaving]       = useState(false);
  const [error,        setError]        = useState("");

  async function save() {
    setSaving(true); setError("");
    try {
      if (anthropicKey.trim()) {
        await api.put("/llm/config/anthropic", { provider: "anthropic", api_key: anthropicKey.trim(), enabled: true });
      }
      if (openaiKey.trim()) {
        await api.put("/llm/config/openai", { provider: "openai", api_key: openaiKey.trim(), enabled: true });
      }
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally { setSaving(false); }
  }

  const hasInput = anthropicKey.trim() || openaiKey.trim();

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Key className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">API-Schlüssel</h2>
          <p className="text-sm text-muted-foreground">Cloud-LLM-Anbieter einrichten (optional)</p>
        </div>
      </div>

      <div className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Anthropic API-Key</label>
          <input
            value={anthropicKey}
            onChange={e => setAnthropicKey(e.target.value)}
            placeholder="sk-ant-api03-..."
            type="password"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono"
          />
          <p className="text-xs text-muted-foreground">Für Claude 3/4-Modelle — Key aus <strong>console.anthropic.com</strong></p>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">OpenAI API-Key</label>
          <input
            value={openaiKey}
            onChange={e => setOpenaiKey(e.target.value)}
            placeholder="sk-..."
            type="password"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono"
          />
          <p className="text-xs text-muted-foreground">Für GPT-4-Modelle — Key aus <strong>platform.openai.com</strong></p>
        </div>
      </div>

      <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-3 text-xs text-blue-600 dark:text-blue-400">
        Claude Max (OAuth)? Nach dem Login unter <strong>Einstellungen → LLM → Claude Max</strong> verbinden.
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={saving || !hasInput}
          className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none transition-colors"
        >
          {saving ? "Speichere..." : "Speichern & weiter"} <ChevronRight className="h-4 w-4" />
        </button>
        <button onClick={onSkip} className="flex items-center gap-1.5 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted transition-colors">
          <SkipForward className="h-4 w-4" /> Überspringen
        </button>
      </div>
    </div>
  );
}

function LlmStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [systemModel,  setSystemModel]  = useState("claude-haiku-4-5-20251001");
  const [saving,       setSaving]       = useState(false);
  const [error,        setError]        = useState("");

  async function save() {
    setSaving(true); setError("");
    try {
      if (systemModel.trim()) {
        await api.setSystemDefaultModel(systemModel.trim());
      }
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally { setSaving(false); }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Cpu className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">LLM-Modell</h2>
          <p className="text-sm text-muted-foreground">Ollama & System-Standardmodell</p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Standard-Modell</label>
          <input value={systemModel} onChange={e => setSystemModel(e.target.value)}
            placeholder="claude-haiku-4-5-20251001"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          <p className="text-xs text-muted-foreground">
            Für System-Agenten und interne Dienste. Claude Haiku empfohlen — oder ein lokales Ollama-Modell (z.B. <code>ollama/mistral-nemo:12b</code>).
          </p>
        </div>
        <div className="rounded-xl border border-muted bg-muted/20 p-3 text-xs text-muted-foreground">
          Ollama-URL und Modell-Konfiguration unter <strong>Einstellungen → LLM</strong> nach dem Login.
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving}
          className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {saving ? "Speichere..." : "Speichern & weiter"} <ChevronRight className="h-4 w-4" />
        </button>
        <button onClick={onSkip} className="flex items-center gap-1.5 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted transition-colors">
          <SkipForward className="h-4 w-4" /> Überspringen
        </button>
      </div>
    </div>
  );
}

function KasStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [login,   setLogin]   = useState("");
  const [pw,      setPw]      = useState("");
  const [domain,  setDomain]  = useState("");
  const [smtp,    setSmtp]    = useState("");
  const [port,    setPort]    = useState("587");
  const [saving,  setSaving]  = useState(false);
  const [error,   setError]   = useState("");

  async function save() {
    setSaving(true); setError("");
    try {
      await api.putKas({ login, password: pw, default_domain: domain, smtp_host: smtp, smtp_port: Number(port) || 587 });
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Speichern");
    } finally { setSaving(false); }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Mail className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">Mail-Hosting (All-Inkl)</h2>
          <p className="text-sm text-muted-foreground">KAS-Zugangsdaten für automatische Postfach-Anlage</p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">KAS-Login</label>
            <input value={login} onChange={e => setLogin(e.target.value)} placeholder="w012345e"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">KAS-Passwort</label>
            <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="••••••••"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Standard-Domain</label>
          <input value={domain} onChange={e => setDomain(e.target.value)} placeholder="deine-domain.de"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
        </div>
        <div className="grid grid-cols-[1fr_100px] gap-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">SMTP-Host</label>
            <input value={smtp} onChange={e => setSmtp(e.target.value)} placeholder="dd12345.kasserver.com"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Port</label>
            <input value={port} onChange={e => setPort(e.target.value)} placeholder="587"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving || !login || !pw}
          className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {saving ? "Speichere..." : "Speichern & weiter"} <ChevronRight className="h-4 w-4" />
        </button>
        <button onClick={onSkip} className="flex items-center gap-1.5 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted transition-colors">
          <SkipForward className="h-4 w-4" /> Überspringen
        </button>
      </div>
    </div>
  );
}

function DoneStep({ onFinish }: { onFinish: () => void }) {
  return (
    <div className="space-y-6 text-center">
      <div className="flex justify-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-500/15">
          <CheckCircle className="h-9 w-9 text-green-500" />
        </div>
      </div>
      <div>
        <h2 className="text-2xl font-bold">HydraHive ist bereit!</h2>
        <p className="mt-2 text-muted-foreground">
          Alle Einstellungen können jederzeit unter <strong>System → Einstellungen</strong> geändert werden.
        </p>
      </div>
      <button onClick={onFinish}
        className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors">
        Zum Dashboard <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

export function WizardPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("welcome");

  function next() {
    const idx = STEPS.indexOf(step);
    setStep(STEPS[idx + 1] ?? "done");
  }

  async function finish() {
    try { await api.wizardComplete(); } catch {}
    navigate("/");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-lg">
        <div className="mb-6 text-center">
          <div className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary mb-3">
            <span className="text-primary-foreground font-bold text-lg">H</span>
          </div>
          <p className="text-xs text-muted-foreground uppercase tracking-widest">Einrichtungsassistent</p>
        </div>

        <div className="rounded-2xl border bg-card p-8 shadow-sm">
          <StepIndicator current={step} />

          {step === "welcome" && <WelcomeStep onNext={next} />}
          {step === "apikey"  && <ApiKeyStep onNext={next} onSkip={next} />}
          {step === "llm"     && <LlmStep onNext={next} onSkip={next} />}
          {step === "kas"     && <KasStep onNext={next} onSkip={next} />}
          {step === "done"    && <DoneStep onFinish={finish} />}
        </div>
      </div>
    </div>
  );
}
