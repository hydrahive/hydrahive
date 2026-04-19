/**
 * ProjectCreatePage.tsx — v2 Projekt-Wizard (6-Schritt-Formular)
 *
 * Schritte:
 *   1. Template wählen
 *   2. Basics (Name, ID, Beschreibung)
 *   3. LLM (Provider, Modell, Temperature, API-Key)
 *   4. Fachgebiet (AGENT.md Editor)
 *   5. Quellen (Git-Repos, URLs) — optional
 *   6. Messenger (Discord/Telegram/WhatsApp) — optional
 */
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, Check, Loader2,
  Code, Server, MessageSquare, BarChart3, Bot, FileQuestion,
  Cpu, FileText,
} from "lucide-react";
import { api } from "@/lib/api";

// ── Templates ────────────────────────────────────────────────────────────────

const TEMPLATES = [
  { id: "code-project",   label: "Code-Projekt",   icon: Code,          desc: "Entwicklung, Git, Code-Review" },
  { id: "server-admin",   label: "Server Admin",   icon: Server,        desc: "Wartung, Monitoring, SSH" },
  { id: "chat-bot",       label: "Chat-Bot",       icon: MessageSquare, desc: "Discord, Telegram, Persönlichkeit" },
  { id: "data-analysis",  label: "Datenanalyse",   icon: BarChart3,     desc: "Python, Pandas, CSV" },
  { id: "general",        label: "Allgemein",       icon: Bot,           desc: "Freier Assistent" },
  { id: "blank",          label: "Leer",            icon: FileQuestion,  desc: "Komplett selbst konfigurieren" },
];

const PROVIDERS = ["anthropic", "openai", "minimax", "nvidia", "google", "ollama", "deepseek"];

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai:    "OpenAI",
  minimax:   "MiniMax",
  nvidia:    "NVIDIA NIM",
  google:    "Google",
  ollama:    "Ollama",
  deepseek:  "DeepSeek",
};

const MODELS: Record<string, string[]> = {
  anthropic: ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6", "claude-opus-4-7"],
  openai: ["gpt-4o", "gpt-4o-mini", "o3", "o3-mini"],
  minimax: ["MiniMax-M2.7"],
  // #684: NVIDIA NIM Phase-1 Startliste (Single Source of Truth auch im Backend)
  nvidia: [
    "minimaxai/minimax-m2.7",
    "minimaxai/minimax-m2.5",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "deepseek-ai/deepseek-v3.2",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "moonshotai/kimi-k2-thinking",
  ],
  google: ["gemini-2.0-flash", "gemini-2.5-pro"],
  ollama: ["llama3.1", "qwen2.5:7b", "mistral"],
  deepseek: ["deepseek-r1"],
};

const TEMP_PRESETS: Record<string, number> = {
  "code-project": 0.3,
  "server-admin": 0.4,
  "chat-bot": 0.7,
  "data-analysis": 0.2,
  "general": 0.5,
  "blank": 0.5,
};

// ── Wizard ───────────────────────────────────────────────────────────────────

export function ProjectCreatePage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Step 1: Template
  const [template, setTemplate] = useState("general");

  // Step 2: Basics
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [description, setDescription] = useState("");
  const [idManual, setIdManual] = useState(false);

  // Step 3: LLM
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [temperature, setTemperature] = useState(0.5);
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [availableKeys, setAvailableKeys] = useState<{ name: string; preview: string }[]>([]);

  // Step 4: AGENT.md
  const [agentMd, setAgentMd] = useState("");
  const [agentMdLoaded, setAgentMdLoaded] = useState(false);

  // Step 5: Quellen (optional)
  const [repoUrl, setRepoUrl] = useState("");

  // Step 6: Messenger (optional)
  const [whatsappEnabled, setWhatsappEnabled] = useState(false);
  const [discordEnabled, setDiscordEnabled] = useState(false);
  const [telegramEnabled, setTelegramEnabled] = useState(false);

  // Keys laden
  useEffect(() => {
    api.get<{ keys: { name: string; preview: string }[] }>("/secrets/keys")
      .then(res => setAvailableKeys((res as any).keys || []))
      .catch(() => {});
  }, []);

  // Name → ID generieren
  useEffect(() => {
    if (!idManual && name) {
      setProjectId(
        name.toLowerCase()
          .replace(/[äö]/g, m => m === "ä" ? "ae" : "oe")
          .replace(/ü/g, "ue")
          .replace(/ß/g, "ss")
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 40)
      );
    }
  }, [name, idManual]);

  // Template → Temperature + AGENT.md
  useEffect(() => {
    setTemperature(TEMP_PRESETS[template] ?? 0.5);
    setAgentMdLoaded(false);
  }, [template]);

  // AGENT.md aus Template laden wenn noch nicht manuell bearbeitet
  useEffect(() => {
    if (step === 3 && !agentMdLoaded) {
      // #764 Phase 2: Cookie-Auth via credentials:'include'.
      fetch(`/api/templates/${template}/agent-md`, {
        credentials: "include",
      })
        .then(r => r.ok ? r.text() : "")
        .then(text => {
          if (text) setAgentMd(text);
          else setAgentMd(`# ${name || "Agent"}\n\nBeschreibe hier das Fachgebiet.`);
          setAgentMdLoaded(true);
        })
        .catch(() => {
          setAgentMd(`# ${name || "Agent"}\n\nBeschreibe hier das Fachgebiet.`);
          setAgentMdLoaded(true);
        });
    }
  }, [step]);

  const STEPS = ["Template", "Basics", "LLM", "Fachgebiet", "Quellen", "Messenger"];

  async function handleSubmit() {
    setSubmitting(true);
    setError("");
    try {
      // #592: Messenger-Config bauen nur wenn mindestens einer aktiviert
      const messenger: Record<string, any> = {};
      if (discordEnabled) messenger.discord = {};
      if (telegramEnabled) messenger.telegram = {};
      if (whatsappEnabled) messenger.whatsapp = {};

      const res = await api.post<any>("/projects/v2", {
        id: projectId,
        name,
        description,
        template,
        provider,
        model,
        temperature,
        api_key_env: apiKeyEnv,
        agent_md: agentMd,
        members: ["admin"],
        // #592 Provisioning + Git + Messenger
        samba: true,
        github_repo: repoUrl.trim(),
        git_clone: !!repoUrl.trim(),
        git_branch: "main",
        messenger,
      });
      // Warnungen anzeigen wenn Provisioning teilweise fehlschlug
      const warnings: string[] = (res as any)?.warnings || [];
      if (warnings.length > 0) {
        console.warn("Projekt erstellt mit Warnungen:", warnings);
      }
      navigate("/projects");
    } catch (e: any) {
      setError(e?.message || "Fehler beim Erstellen");
    } finally {
      setSubmitting(false);
    }
  }

  function canNext(): boolean {
    if (step === 1) return name.trim().length > 0 && projectId.trim().length > 0;
    return true;
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-3xl p-6">
      {/* Header */}
      <div className="mb-6 flex items-center gap-3">
        <button onClick={() => navigate("/projects")}
          className="rounded-lg border p-2 transition hover:bg-accent">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-lg font-semibold">Neues Projekt erstellen</h1>
          <p className="text-sm text-muted-foreground">Schritt {step + 1} von {STEPS.length}: {STEPS[step]}</p>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mb-8 flex gap-1">
        {STEPS.map((s, i) => (
          <div key={s} className={`h-1 flex-1 rounded-full transition-colors ${
            i <= step ? "bg-primary" : "bg-muted"
          }`} />
        ))}
      </div>

      {/* Step Content */}
      <div className="min-h-[300px]">

        {/* Step 0: Template */}
        {step === 0 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map(t => (
              <button
                key={t.id}
                onClick={() => setTemplate(t.id)}
                className={`flex flex-col items-start gap-2 rounded-2xl border p-4 text-left transition hover:border-primary/50 hover:bg-accent/50 ${
                  template === t.id ? "border-primary bg-primary/5 ring-1 ring-primary/20" : ""
                }`}
              >
                <t.icon className={`h-6 w-6 ${template === t.id ? "text-primary" : "text-muted-foreground"}`} />
                <div>
                  <p className="font-medium text-sm">{t.label}</p>
                  <p className="text-xs text-muted-foreground">{t.desc}</p>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Step 1: Basics */}
        {step === 1 && (
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Projektname *</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)}
                placeholder="z.B. Mein Discord Bot"
                className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm" autoFocus />
            </div>
            <div>
              <label className="text-sm font-medium">Projekt-ID</label>
              <div className="mt-1 flex gap-2">
                <input type="text" value={projectId}
                  onChange={e => { setProjectId(e.target.value); setIdManual(true); }}
                  className="flex-1 rounded-xl border bg-background px-3 py-2 text-sm font-mono" />
                {idManual && (
                  <button onClick={() => setIdManual(false)} className="text-xs text-primary hover:underline">Auto</button>
                )}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">Nur a-z, 0-9, - und _</p>
            </div>
            <div>
              <label className="text-sm font-medium">Beschreibung</label>
              <input type="text" value={description} onChange={e => setDescription(e.target.value)}
                placeholder="Optional — worum geht es?"
                className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm" />
            </div>
          </div>
        )}

        {/* Step 2: LLM */}
        {step === 2 && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Cpu className="h-4 w-4 text-primary" />
              LLM-Konfiguration
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-xs text-muted-foreground">Provider</label>
                <select value={provider} onChange={e => { setProvider(e.target.value); setModel(MODELS[e.target.value]?.[0] || ""); }}
                  className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm">
                  {PROVIDERS.map(p => <option key={p} value={p}>{PROVIDER_LABELS[p] ?? p}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Modell</label>
                <select value={model} onChange={e => setModel(e.target.value)}
                  className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm">
                  {(MODELS[provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Temperature: {temperature}</label>
              <input type="range" min="0" max="1" step="0.1" value={temperature}
                onChange={e => setTemperature(parseFloat(e.target.value))}
                className="mt-1 w-full" />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Präzise (0.0)</span>
                <span>Kreativ (1.0)</span>
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">API-Key</label>
              <select value={apiKeyEnv} onChange={e => setApiKeyEnv(e.target.value)}
                className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm">
                <option value="">Standard (aus Environment)</option>
                {availableKeys.map(k => (
                  <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Step 3: AGENT.md */}
        {step === 3 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileText className="h-4 w-4 text-primary" />
              Fachgebiet &amp; Regeln (AGENT.md)
            </div>
            <p className="text-xs text-muted-foreground">
              Beschreibe was der Agent können soll, wie er sich verhalten soll und welche Regeln gelten.
              Vorausgefüllt aus dem Template — passe es an.
            </p>
            <textarea
              value={agentMd}
              onChange={e => setAgentMd(e.target.value)}
              rows={16}
              className="w-full rounded-xl border bg-background px-4 py-3 text-sm font-mono leading-relaxed resize-y"
            />
          </div>
        )}

        {/* Step 4: Quellen (optional) */}
        {step === 4 && (
          <div className="space-y-4">
            <div className="text-sm font-medium">Quellen (optional)</div>
            <p className="text-xs text-muted-foreground">
              Git-Repository oder Dokumentation die der Agent als Referenz nutzen kann.
              Kann auch später in den Projekt-Settings konfiguriert werden.
            </p>
            <div>
              <label className="text-xs text-muted-foreground">Git-Repository URL</label>
              <input type="text" value={repoUrl} onChange={e => setRepoUrl(e.target.value)}
                placeholder="z.B. https://github.com/user/repo"
                className="mt-1 w-full rounded-xl border bg-background px-3 py-2 text-sm" />
            </div>
          </div>
        )}

        {/* Step 5: Messenger (optional) */}
        {step === 5 && (
          <div className="space-y-4">
            <div className="text-sm font-medium">Messenger (optional)</div>
            <p className="text-xs text-muted-foreground">
              Verbinde Messenger-Dienste mit diesem Projekt.
              Kann auch später in den Projekt-Settings konfiguriert werden.
            </p>
            <div className="space-y-3">
              <label className="flex items-center gap-3 rounded-xl border p-3 cursor-pointer hover:bg-accent/50 transition">
                <input type="checkbox" checked={whatsappEnabled} onChange={e => setWhatsappEnabled(e.target.checked)}
                  className="rounded" />
                <div>
                  <p className="text-sm font-medium">WhatsApp</p>
                  <p className="text-xs text-muted-foreground">Über WhatsApp-Bridge (QR-Code Pairing)</p>
                </div>
              </label>
              <label className="flex items-center gap-3 rounded-xl border p-3 cursor-pointer hover:bg-accent/50 transition">
                <input type="checkbox" checked={discordEnabled} onChange={e => setDiscordEnabled(e.target.checked)}
                  className="rounded" />
                <div>
                  <p className="text-sm font-medium">Discord</p>
                  <p className="text-xs text-muted-foreground">Discord-Bot in Channels</p>
                </div>
              </label>
              <label className="flex items-center gap-3 rounded-xl border p-3 cursor-pointer hover:bg-accent/50 transition">
                <input type="checkbox" checked={telegramEnabled} onChange={e => setTelegramEnabled(e.target.checked)}
                  className="rounded" />
                <div>
                  <p className="text-sm font-medium">Telegram</p>
                  <p className="text-xs text-muted-foreground">Telegram-Bot</p>
                </div>
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="mt-4 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Navigation Buttons */}
      <div className="mt-8 flex items-center justify-between">
        <button
          onClick={() => step > 0 ? setStep(s => s - 1) : navigate("/projects")}
          className="inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm transition hover:bg-accent"
        >
          <ArrowLeft className="h-4 w-4" />
          {step === 0 ? "Abbrechen" : "Zurück"}
        </button>

        {step < STEPS.length - 1 ? (
          <button
            onClick={() => setStep(s => s + 1)}
            disabled={!canNext()}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
          >
            Weiter
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={submitting || !projectId.trim()}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            Projekt erstellen
          </button>
        )}
      </div>
    </div>
  );
}
