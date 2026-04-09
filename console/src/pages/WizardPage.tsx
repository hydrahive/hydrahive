/**
 * WizardPage — Setup-Wizard (#531: Ziel vor Technik)
 *
 * Neuer Flow: Starttyp → Ziel → Modellpfad → API-Key/LLM → Fertig
 * Fragt erst nach Erfahrung und Ziel, dann erst nach Technik.
 */
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle, ChevronRight, Cpu, Key, Rocket, SkipForward,
  MessageSquare, Search, Code, Users, Briefcase, Zap, Shield, Sparkles,
} from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

// ── Types ────────────────────────────────────────────────────────────────────

type StartType = "simple" | "standard" | "advanced";
type Goal = "chat" | "organize" | "research" | "coding" | "team";
type ModelPath = "cloud" | "local" | "unsure";
type Step = "welcome" | "goal" | "model" | "apikey" | "llm" | "done";

// ── Step Indicator ───────────────────────────────────────────────────────────

function StepIndicator({ steps, current }: { steps: Step[]; current: Step }) {
  const idx = steps.indexOf(current);
  return (
    <div className="flex items-center justify-center gap-1.5 mb-8">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-1.5">
          <div className={`h-2 rounded-full transition-all ${
            i <= idx ? "bg-primary w-8" : "bg-muted w-4"
          }`} />
        </div>
      ))}
    </div>
  );
}

// ── Choice Card ──────────────────────────────────────────────────────────────

function ChoiceCard({
  icon: Icon, title, desc, selected, onClick, badge,
}: {
  icon: React.ElementType; title: string; desc: string;
  selected: boolean; onClick: () => void; badge?: string;
}) {
  return (
    <button onClick={onClick} className={`w-full text-left rounded-xl border-2 p-4 transition-all ${
      selected
        ? "border-primary bg-primary/5 ring-1 ring-primary/20"
        : "border-muted hover:border-primary/30 hover:bg-muted/30"
    }`}>
      <div className="flex items-start gap-3">
        <div className={`p-2 rounded-lg shrink-0 ${selected ? "bg-primary/15" : "bg-muted"}`}>
          <Icon className={`w-5 h-5 ${selected ? "text-primary" : "text-muted-foreground"}`} />
        </div>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm">{title}</span>
            {badge && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-medium">{badge}</span>}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
        </div>
        {selected && <CheckCircle className="w-5 h-5 text-primary shrink-0 mt-0.5" />}
      </div>
    </button>
  );
}

// ── Step 1: Welcome + Starttyp ───────────────────────────────────────────────

function WelcomeStep({
  startType, setStartType, onNext,
}: {
  startType: StartType | null; setStartType: (v: StartType) => void; onNext: () => void;
}) {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="flex justify-center mb-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
            <Rocket className="h-8 w-8 text-primary" />
          </div>
        </div>
        <h2 className="text-2xl font-bold">Willkommen bei HydraHive</h2>
        <p className="mt-2 text-muted-foreground text-sm">
          Wie möchtest du starten? Du kannst alles später noch ändern.
        </p>
      </div>

      <div className="space-y-2">
        <ChoiceCard
          icon={Zap} title="Einfach" badge="Empfohlen"
          desc="Schnellster Weg zum ersten Chat. Minimale Konfiguration, sichere Voreinstellungen."
          selected={startType === "simple"} onClick={() => setStartType("simple")}
        />
        <ChoiceCard
          icon={Sparkles} title="Standard"
          desc="LLM-Modell wählen, API-Key hinterlegen, Assistent einrichten."
          selected={startType === "standard"} onClick={() => setStartType("standard")}
        />
        <ChoiceCard
          icon={Shield} title="Erweitert"
          desc="Alle Optionen: Modelle, Extensions, Server-Konfiguration."
          selected={startType === "advanced"} onClick={() => setStartType("advanced")}
        />
      </div>

      <button onClick={onNext} disabled={!startType}
        className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors">
        Weiter <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

// ── Step 2: Ziel ─────────────────────────────────────────────────────────────

function GoalStep({
  goal, setGoal, onNext,
}: {
  goal: Goal | null; setGoal: (v: Goal) => void; onNext: () => void;
}) {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-xl font-bold">Was möchtest du mit HydraHive tun?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Das hilft uns, die beste Konfiguration für dich vorzuschlagen.
        </p>
      </div>

      <div className="space-y-2">
        <ChoiceCard
          icon={MessageSquare} title="Einfach chatten"
          desc="KI als Gesprächspartner, Fragen beantworten, Ideen entwickeln."
          selected={goal === "chat"} onClick={() => setGoal("chat")}
        />
        <ChoiceCard
          icon={Briefcase} title="Organisation & Alltag"
          desc="Aufgaben planen, Texte schreiben, E-Mails beantworten."
          selected={goal === "organize"} onClick={() => setGoal("organize")}
        />
        <ChoiceCard
          icon={Search} title="Recherche & Web"
          desc="Informationen suchen, zusammenfassen, analysieren."
          selected={goal === "research"} onClick={() => setGoal("research")}
        />
        <ChoiceCard
          icon={Code} title="Coding & Dateien"
          desc="Code schreiben, Repos verwalten, Dateien bearbeiten."
          selected={goal === "coding"} onClick={() => setGoal("coding")}
        />
        <ChoiceCard
          icon={Users} title="Team & System"
          desc="Mehrere Agenten koordinieren, Server verwalten, Automatisierung."
          selected={goal === "team"} onClick={() => setGoal("team")}
        />
      </div>

      <button onClick={onNext} disabled={!goal}
        className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors">
        Weiter <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

// ── Step 3: Modellpfad ───────────────────────────────────────────────────────

function ModelStep({
  modelPath, setModelPath, onNext,
}: {
  modelPath: ModelPath | null; setModelPath: (v: ModelPath) => void; onNext: () => void;
}) {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-xl font-bold">Wie möchtest du KI-Modelle nutzen?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Cloud-APIs sind sofort einsatzbereit. Lokale Modelle brauchen Ollama.
        </p>
      </div>

      <div className="space-y-2">
        <ChoiceCard
          icon={Key} title="Ich habe einen API-Key" badge="Cloud"
          desc="Anthropic (Claude) oder OpenAI (GPT). Beste Qualität, kostet pro Nutzung."
          selected={modelPath === "cloud"} onClick={() => setModelPath("cloud")}
        />
        <ChoiceCard
          icon={Cpu} title="Lokal mit Ollama" badge="Kostenlos"
          desc="Modelle auf deiner Hardware. Kostenlos, braucht aber GPU/RAM."
          selected={modelPath === "local"} onClick={() => setModelPath("local")}
        />
        <ChoiceCard
          icon={Sparkles} title="Ich bin mir noch unsicher"
          desc="Kein Problem — du kannst das jederzeit in den Einstellungen einrichten."
          selected={modelPath === "unsure"} onClick={() => setModelPath("unsure")}
        />
      </div>

      <button onClick={onNext} disabled={!modelPath}
        className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors">
        {modelPath === "unsure" ? "Überspringen & fertig" : "Weiter"} <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

// ── Step 4a: API-Key ─────────────────────────────────────────────────────────

function ApiKeyStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const { t } = useTranslation();
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
      setError(e instanceof Error ? e.message : t("common.saveError"));
    } finally { setSaving(false); }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Key className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">API-Schlüssel einrichten</h2>
          <p className="text-sm text-muted-foreground">Mindestens einen Key eintragen</p>
        </div>
      </div>

      <div className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Anthropic API-Key</label>
          <input value={anthropicKey} onChange={e => setAnthropicKey(e.target.value)}
            placeholder="sk-ant-api03-..." type="password"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
          <p className="text-xs text-muted-foreground">Für Claude-Modelle — Key von <strong>console.anthropic.com</strong></p>
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">OpenAI API-Key</label>
          <input value={openaiKey} onChange={e => setOpenaiKey(e.target.value)}
            placeholder="sk-..." type="password"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
          <p className="text-xs text-muted-foreground">Für GPT-Modelle — Key von <strong>platform.openai.com</strong></p>
        </div>
      </div>

      <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-3 text-xs text-blue-600 dark:text-blue-400">
        Claude Max (OAuth)? Nach dem Setup unter <strong>Einstellungen → LLM → Claude Max</strong> verbinden.
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving || (!anthropicKey.trim() && !openaiKey.trim())}
          className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors">
          {saving ? t("common.saving") : "Speichern & fertig"} <ChevronRight className="h-4 w-4" />
        </button>
        <button onClick={onSkip} className="flex items-center gap-1.5 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted transition-colors">
          <SkipForward className="h-4 w-4" /> Später
        </button>
      </div>
    </div>
  );
}

// ── Step 4b: LLM (Ollama) ────────────────────────────────────────────────────

function LlmStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const { t } = useTranslation();
  const [availableModels, setAvailableModels] = useState<{id:string;label:string;provider:string}[]>([]);
  const [systemModel, setSystemModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.availableModels().then(r => {
      const ollamaModels = (r.models ?? []).filter((m: any) => m.provider === "ollama" || m.provider === "wks_ollama");
      setAvailableModels(ollamaModels);
      if (!systemModel && ollamaModels.length > 0) setSystemModel(ollamaModels[0].id);
    }).catch(() => {});
  }, []);

  async function save() {
    setSaving(true); setError("");
    try {
      if (systemModel.trim()) await api.setSystemDefaultModel(systemModel.trim());
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.saveError"));
    } finally { setSaving(false); }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
          <Cpu className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">Lokales Modell wählen</h2>
          <p className="text-sm text-muted-foreground">Ollama-Modelle auf deiner Hardware</p>
        </div>
      </div>

      {availableModels.length > 0 ? (
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Standard-Modell</label>
          <select value={systemModel} onChange={e => setSystemModel(e.target.value)}
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
            <option value="">— Modell wählen —</option>
            {availableModels.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </div>
      ) : (
        <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-sm">
          <p className="font-medium text-yellow-600 dark:text-yellow-400">Ollama nicht gefunden</p>
          <p className="text-xs text-muted-foreground mt-1">
            Installiere Ollama unter <strong>Extensions</strong> oder richte es manuell ein.
            Du kannst diesen Schritt überspringen und später konfigurieren.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving}
          className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {saving ? t("common.saving") : "Speichern & fertig"} <ChevronRight className="h-4 w-4" />
        </button>
        <button onClick={onSkip} className="flex items-center gap-1.5 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted transition-colors">
          <SkipForward className="h-4 w-4" /> Später
        </button>
      </div>
    </div>
  );
}

// ── Step 5: Done ─────────────────────────────────────────────────────────────

function DoneStep({ goal, onFinish }: { goal: Goal | null; onFinish: () => void }) {
  const tips: Record<Goal, { route: string; label: string; tip: string }> = {
    chat:     { route: "/my-agent", label: "Zum Assistenten", tip: "Schreib deinem Assistenten einfach eine Nachricht — er ist sofort einsatzbereit." },
    organize: { route: "/my-agent", label: "Zum Assistenten", tip: "Dein Assistent kann Aufgaben planen, Texte schreiben und organisieren." },
    research: { route: "/my-agent", label: "Zum Assistenten", tip: "Aktiviere die Web-Suche unter Extensions für beste Recherche-Ergebnisse." },
    coding:   { route: "/projects", label: "Erstes Projekt erstellen", tip: "Erstelle ein Projekt und verknüpfe es mit einem Git-Repo." },
    team:     { route: "/agents",   label: "Agenten verwalten", tip: "Erstelle spezialisierte Agenten und weise ihnen Rollen zu." },
  };
  const t = goal && tips[goal] ? tips[goal] : tips.chat;

  return (
    <div className="space-y-6 text-center">
      <div className="flex justify-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-500/15">
          <CheckCircle className="h-9 w-9 text-green-500" />
        </div>
      </div>
      <div>
        <h2 className="text-2xl font-bold">HydraHive ist bereit!</h2>
        <p className="mt-2 text-muted-foreground text-sm">{t.tip}</p>
      </div>
      <div className="rounded-xl border bg-muted/20 p-3 text-xs text-muted-foreground">
        Alle Einstellungen unter <strong>Settings</strong> jederzeit änderbar.
      </div>
      <button onClick={onFinish}
        className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors">
        {t.label} <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

// ── Main Wizard ──────────────────────────────────────────────────────────────

export function WizardPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("welcome");
  const [startType, setStartType] = useState<StartType | null>(null);
  const [goal, setGoal] = useState<Goal | null>(null);
  const [modelPath, setModelPath] = useState<ModelPath | null>(null);

  // Dynamische Steps basierend auf Auswahl
  function getSteps(): Step[] {
    if (startType === "simple") return ["welcome", "goal", "model", "done"];
    const steps: Step[] = ["welcome", "goal", "model"];
    if (modelPath === "cloud") steps.push("apikey");
    else if (modelPath === "local") steps.push("llm");
    steps.push("done");
    return steps;
  }

  function next() {
    const steps = getSteps();
    const idx = steps.indexOf(step);
    if (idx < steps.length - 1) {
      setStep(steps[idx + 1]);
    }
  }

  function handleModelNext() {
    if (modelPath === "unsure" || startType === "simple") {
      setStep("done");
    } else {
      next();
    }
  }

  async function finish() {
    try { await api.wizardComplete(); } catch {}
    const tips: Record<string, string> = {
      chat: "/my-agent", organize: "/my-agent", research: "/my-agent",
      coding: "/projects", team: "/agents",
    };
    navigate(goal ? tips[goal] || "/" : "/");
  }

  const steps = getSteps();

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
          <StepIndicator steps={steps} current={step} />

          {step === "welcome" && <WelcomeStep startType={startType} setStartType={setStartType} onNext={next} />}
          {step === "goal"    && <GoalStep goal={goal} setGoal={setGoal} onNext={next} />}
          {step === "model"   && <ModelStep modelPath={modelPath} setModelPath={setModelPath} onNext={handleModelNext} />}
          {step === "apikey"  && <ApiKeyStep onNext={() => setStep("done")} onSkip={() => setStep("done")} />}
          {step === "llm"     && <LlmStep onNext={() => setStep("done")} onSkip={() => setStep("done")} />}
          {step === "done"    && <DoneStep goal={goal} onFinish={finish} />}
        </div>
      </div>
    </div>
  );
}
