/**
 * ProjectSettingsPanel.tsx — v2 Agent-Settings für ein Projekt
 *
 * Zeigt und editiert: LLM-Config, AGENT.md, Execution-Mode.
 * Nutzt GET/PUT /projects/{id}/settings Endpoints.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { ProfileComposer } from "@/components/ProfileComposer";
import {
  Save,
  X,
  Loader2,
  Bot,
  Cpu,
  FileText,
  Shield,
  Volume2,
  Play,
  MessageCircle,
  Hash,
  Database,
  Sparkles,
  Server,
  Monitor,
  ExternalLink,
  AlertTriangle,
} from "lucide-react";

interface ProjectSettingsPanelProps {
  projectId: string;
  onClose?: () => void;
}

interface SettingsData {
  project_id: string;
  is_v2: boolean;
  identity: { name: string; description: string };
  llm: {
    provider: string;
    model: string;
    temperature: number;
    max_tokens: number;
    api_key_env: string;
    failover: { provider: string; model: string }[];
  };
  agent_md: string;
  members: string[];
  execution_mode: string;
  max_tool_rounds?: number;
  risk_policy?: "interactive" | "trusted";
  messenger: {
    whatsapp?: { session_ids?: string[]; enabled?: boolean };
    discord?: { channels?: string[]; bot_token_env?: string };
    telegram?: { chat_ids?: string[]; bot_token_env?: string };
  };
}

interface ProjectTargetServer {
  server_id: string;
  name?: string;
  ip?: string;
  ssh_user?: string;
  ssh_port?: number;
  role?: string;
  note?: string;
  has_ssh_key?: boolean;
  stale?: boolean;
}

interface ProjectTargetWks {
  username: string;
  ip?: string;
  ssh_user?: string;
  ssh_port?: number;
  role?: string;
  note?: string;
  has_ssh_key?: boolean;
  stale?: boolean;
}

interface ProjectTargetsResponse {
  project_id: string;
  servers: ProjectTargetServer[];
  wks: ProjectTargetWks[];
}

const PROVIDERS = ["anthropic", "openai", "minimax", "google", "ollama", "deepseek"];

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai:    "OpenAI",
  minimax:   "MiniMax",
  google:    "Google",
  ollama:    "Ollama",
  deepseek:  "DeepSeek",
};

const MODELS: Record<string, string[]> = {
  anthropic: ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
  openai: ["gpt-4o", "gpt-4o-mini", "o3", "o3-mini"],
  minimax: ["MiniMax-M2.7"],
  google: ["gemini-2.0-flash", "gemini-2.5-pro"],
  ollama: ["llama3.1", "qwen2.5:7b", "mistral"],
  deepseek: ["deepseek-r1"],
};

export function ProjectSettingsPanel({ projectId, onClose }: ProjectSettingsPanelProps) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const isPersonalOwner = projectId === `personal_${user?.username}`;
  const canUseComposer = isAdmin || isPersonalOwner;
  const [showComposer, setShowComposer] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [data, setData] = useState<SettingsData | null>(null);
  const [targets, setTargets] = useState<ProjectTargetsResponse | null>(null);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [targetsError, setTargetsError] = useState("");

  // Editable fields
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [temperature, setTemperature] = useState(0.5);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [agentMd, setAgentMd] = useState("");
  const [executionMode, setExecutionMode] = useState("safe");
  const [maxToolRounds, setMaxToolRounds] = useState(50);
  const [riskPolicy, setRiskPolicy] = useState<"interactive" | "trusted">("interactive");
  const [availableKeys, setAvailableKeys] = useState<{ name: string; preview: string }[]>([]);

  // WhatsApp
  const [waStatus, setWaStatus] = useState<string>("unknown");
  const [waQr, setWaQr] = useState<string>("");
  const [waPhone, setWaPhone] = useState<string>("");
  const [waConnecting, setWaConnecting] = useState(false);

  // WhatsApp Filter-Config (#567)
  const [waPrivateChats, setWaPrivateChats] = useState(true);
  const [waGroupChats, setWaGroupChats] = useState(false);
  const [waRequireKeyword, setWaRequireKeyword] = useState("");
  const [waOwnerNumbers, setWaOwnerNumbers] = useState("");
  const [waAllowedNumbers, setWaAllowedNumbers] = useState("");
  const [waBlockedNumbers, setWaBlockedNumbers] = useState("");
  const [waVoiceMode, setWaVoiceMode] = useState("never");
  const [waVoiceName, setWaVoiceName] = useState("de-DE-KatjaNeural");
  const [waVoices, setWaVoices] = useState<{ id: string; label: string; lang: string }[]>([]);
  const [waSaving, setWaSaving] = useState(false);
  const [waSuccess, setWaSuccess] = useState("");
  const [waPreviewPlaying, setWaPreviewPlaying] = useState(false);

  // Discord + Telegram (#569)
  const [discordBotTokenEnv, setDiscordBotTokenEnv] = useState("");
  const [discordChannels, setDiscordChannels] = useState("");
  const [telegramBotTokenEnv, setTelegramBotTokenEnv] = useState("");
  const [telegramChatIds, setTelegramChatIds] = useState("");
  const [messengerSaving, setMessengerSaving] = useState(false);
  const [messengerSuccess, setMessengerSuccess] = useState("");

  // Members (#570)
  const [members, setMembers] = useState<string[]>([]);
  const [allUsers, setAllUsers] = useState<string[]>([]);
  const [newMember, setNewMember] = useState("");

  // Bootstrap-Memory (#614)
  const [bootstrapRunning, setBootstrapRunning] = useState(false);
  const [bootstrapResult, setBootstrapResult] = useState<string>("");

  useEffect(() => {
    loadSettings();
    loadTargets();
    loadKeys();
    loadWhatsAppStatus();
    loadVoices();
    // Alle registrierten User laden (#570)
    api.get<Record<string, unknown>>("/users").then(d => {
      setAllUsers(Object.keys(d || {}));
    }).catch(() => {});
  }, [projectId]);

  async function loadWhatsAppStatus() {
    try {
      const res = await api.get<any>(`/projects/${projectId}/whatsapp`);
      const d = res as any;
      setWaStatus(d.status || "unknown");
      setWaQr(d.qr || "");
      setWaPhone(d.phone || "");
      // Filter-Config laden (#567)
      setWaPrivateChats(d.private_chats_enabled ?? true);
      setWaGroupChats(d.group_chats_enabled ?? false);
      setWaRequireKeyword(d.require_keyword ?? "");
      setWaOwnerNumbers((d.owner_numbers ?? []).join("\n"));
      setWaAllowedNumbers((d.allowed_numbers ?? []).join("\n"));
      setWaBlockedNumbers((d.blocked_numbers ?? []).join("\n"));
      setWaVoiceMode(d.voice_mode ?? "never");
      setWaVoiceName(d.voice_name ?? "de-DE-KatjaNeural");
    } catch {
      setWaStatus("unavailable");
    }
  }

  async function loadVoices() {
    try {
      const res = await api.get<any>("/me/whatsapp/voices");
      setWaVoices(Array.isArray(res) ? res : (res as any).voices || []);
    } catch {
      // nicht kritisch
    }
  }

  async function saveWhatsAppConfig() {
    setWaSaving(true);
    setWaSuccess("");
    try {
      const nums = (s: string) => s.split("\n").map(n => n.trim()).filter(Boolean);
      await api.put(`/projects/${projectId}/whatsapp/config`, {
        private_chats_enabled: waPrivateChats,
        group_chats_enabled: waGroupChats,
        require_keyword: waRequireKeyword || null,
        owner_numbers: nums(waOwnerNumbers),
        allowed_numbers: nums(waAllowedNumbers),
        blocked_numbers: nums(waBlockedNumbers),
        voice_mode: waVoiceMode,
        voice_name: waVoiceName,
      });
      setWaSuccess(t("projectSettings.whatsapp.saved", { defaultValue: "Gespeichert!" }));
      setTimeout(() => setWaSuccess(""), 3000);
    } catch (e: any) {
      setError(e?.message || t("projectSettings.whatsapp.saveFailed", { defaultValue: "WhatsApp-Config speichern fehlgeschlagen" }));
    } finally {
      setWaSaving(false);
    }
  }

  async function saveMessengerConfig() {
    setMessengerSaving(true);
    setMessengerSuccess("");
    try {
      const lines = (s: string) => s.split("\n").map(l => l.trim()).filter(Boolean);
      const messenger: Record<string, any> = {};
      if (discordBotTokenEnv || discordChannels.trim()) {
        messenger.discord = {
          bot_token_env: discordBotTokenEnv || undefined,
          channels: lines(discordChannels),
        };
      }
      if (telegramBotTokenEnv || telegramChatIds.trim()) {
        messenger.telegram = {
          bot_token_env: telegramBotTokenEnv || undefined,
          chat_ids: lines(telegramChatIds),
        };
      }
      // WhatsApp aus bestehender Config beibehalten
      const res = await api.get<any>(`/projects/${projectId}/settings`);
      const existing = (res as any).messenger || {};
      if (existing.whatsapp) messenger.whatsapp = existing.whatsapp;
      if (existing.matrix) messenger.matrix = existing.matrix;

      await api.put(`/projects/${projectId}/settings`, { messenger });
      setMessengerSuccess(t("projectSettings.messenger.saved", { defaultValue: "Gespeichert!" }));
      setTimeout(() => setMessengerSuccess(""), 3000);
    } catch (e: any) {
      setError(e?.message || t("projectSettings.messenger.saveFailed", { defaultValue: "Messenger-Config speichern fehlgeschlagen" }));
    } finally {
      setMessengerSaving(false);
    }
  }

  async function previewVoice() {
    setWaPreviewPlaying(true);
    try {
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch("/api/me/whatsapp/voice-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          voice: waVoiceName,
          text: t("projectSettings.whatsapp.previewText", { defaultValue: "Hallo, ich bin dein HydraHive Assistent." }),
        }),
      });
      if (!res.ok) throw new Error(t("projectSettings.whatsapp.previewFailed", { defaultValue: "Preview fehlgeschlagen" }));
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); setWaPreviewPlaying(false); };
      audio.onerror = () => { URL.revokeObjectURL(url); setWaPreviewPlaying(false); };
      await audio.play();
    } catch {
      setWaPreviewPlaying(false);
    }
  }

  async function connectWhatsApp() {
    setWaConnecting(true);
    try {
      const res = await api.post<any>(`/projects/${projectId}/whatsapp/connect`, {});
      const d = res as any;
      setWaStatus(d.status || "connecting");
      setWaQr(d.qr || "");
      // Polling starten für QR-Update
      const poll = setInterval(async () => {
        try {
          const s = await api.get<any>(`/projects/${projectId}/whatsapp`);
          const sd = s as any;
          setWaStatus(sd.status || "unknown");
          setWaQr(sd.qr || "");
          setWaPhone(sd.phone || "");
          if (sd.status === "connected" || sd.status === "error") {
            clearInterval(poll);
            setWaConnecting(false);
          }
        } catch {
          clearInterval(poll);
          setWaConnecting(false);
        }
      }, 3000);
      // Timeout nach 2 Minuten
      setTimeout(() => { clearInterval(poll); setWaConnecting(false); }, 120000);
    } catch (e: any) {
      setError(e?.message || t("projectSettings.whatsapp.connectFailed", { defaultValue: "WhatsApp-Verbindung fehlgeschlagen" }));
      setWaConnecting(false);
    }
  }

  async function disconnectWhatsApp() {
    try {
      await api.delete<any>(`/projects/${projectId}/whatsapp`);
      setWaStatus("disconnected");
      setWaQr("");
      setWaPhone("");
    } catch (e: any) {
      setError(e?.message || t("projectSettings.whatsapp.disconnectFailed", { defaultValue: "Trennen fehlgeschlagen" }));
    }
  }

  async function loadSettings() {
    setLoading(true);
    setError("");
    try {
      const res = await api.get<SettingsData>(`/projects/${projectId}/settings`);
      const d = res as SettingsData;
      setData(d);
      setProvider(d.llm?.provider || "anthropic");
      setModel(d.llm?.model || "claude-sonnet-4-6");
      setTemperature(d.llm?.temperature ?? 0.5);
      setMaxTokens(d.llm?.max_tokens ?? 4096);
      setApiKeyEnv(d.llm?.api_key_env || "");
      setAgentMd(d.agent_md || "");
      setExecutionMode(d.execution_mode || "safe");
      setMaxToolRounds(d.max_tool_rounds ?? 50);
      setRiskPolicy(d.risk_policy === "trusted" ? "trusted" : "interactive");
      setMembers(d.members || []);
      // Messenger-Config laden (#569)
      const m = d.messenger || {};
      setDiscordBotTokenEnv(m.discord?.bot_token_env || "");
      setDiscordChannels((m.discord?.channels || []).join("\n"));
      setTelegramBotTokenEnv(m.telegram?.bot_token_env || "");
      setTelegramChatIds((m.telegram?.chat_ids || []).join("\n"));
    } catch (e: any) {
      setError(e?.message || t("projectSettings.loadFailed", { defaultValue: "Fehler beim Laden" }));
    } finally {
      setLoading(false);
    }
  }

  async function loadKeys() {
    try {
      const res = await api.get<{ keys: { name: string; preview: string }[] }>("/secrets/keys");
      setAvailableKeys((res as any).keys || []);
    } catch {
      // nicht kritisch
    }
  }

  async function loadTargets() {
    setTargetsLoading(true);
    setTargetsError("");
    try {
      const res = await api.get<ProjectTargetsResponse>(`/projects/${projectId}/targets`);
      setTargets(res);
    } catch (e: any) {
      setTargets(null);
      setTargetsError(e?.message || t("projectSettings.targets.loadFailed", { defaultValue: "Zielsysteme konnten nicht geladen werden" }));
    } finally {
      setTargetsLoading(false);
    }
  }

  async function runBootstrapMemory(force = false) {
    setBootstrapRunning(true);
    setBootstrapResult("");
    try {
      const res = await api.post<any>(`/projects/${projectId}/bootstrap-memory${force ? "?force=true" : ""}`, {});
      if ((res as any).skipped) {
        setBootstrapResult(t("projectSettings.bootstrapMemory.skipped", { defaultValue: "Memory bereits vorhanden. Erzwingen mit 'Neu aufbauen'." }));
      } else {
        setBootstrapResult(t("projectSettings.bootstrapMemory.started", { defaultValue: "Memory-Aufbau gestartet — dauert einige Sekunden." }));
      }
    } catch (e: any) {
      setBootstrapResult(t("projectSettings.bootstrapMemory.error", {
        message: e?.message || t("projectSettings.bootstrapMemory.unknown", { defaultValue: "Unbekannt" }),
        defaultValue: "Fehler: {{message}}",
      }));
    } finally {
      setBootstrapRunning(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      // #645 Phase 1e: AGENT.md nur mitsenden, wenn Caller es auch editieren
      // darf. Sonst versucht das Backend den Persona-Write-Guard — und bei
      // inhaltlicher Abweichung würde das 403 werfen, auch wenn der User
      // gar nichts an der Textarea geändert hat.
      const body: Record<string, unknown> = {
        provider,
        model,
        temperature,
        max_tokens: maxTokens,
        api_key_env: apiKeyEnv,
        execution_mode: executionMode,
        max_tool_rounds: maxToolRounds,
        risk_policy: riskPolicy,
        members,
      };
      if (canUseComposer) body.agent_md = agentMd;
      await api.put(`/projects/${projectId}/settings`, body);
      setSuccess(t("projectSettings.saved", { defaultValue: "Gespeichert!" }));
      setTimeout(() => setSuccess(""), 3000);
    } catch (e: any) {
      // Backend-Guard: nicht-Admin darf risk_policy="trusted" nicht setzen → 403
      const raw = e?.message || t("projectSettings.saveFailed", { defaultValue: "Fehler beim Speichern" });
      const isTrustedDenied = riskPolicy === "trusted" && /trusted/i.test(raw) && /admin/i.test(raw);
      setError(isTrustedDenied
        ? t("projectSettings.trustedDeniedNonAdmin", { defaultValue: "Trusted-Modus für den Projekt-Boss darf nur durch Admins gesetzt werden." })
        : raw);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!data) {
    return <div className="p-4 text-sm text-destructive">{error || t("projectSettings.noData", { defaultValue: "Keine Daten" })}</div>;
  }

  return (
    <div className="border-t px-4 pb-4 pt-3 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Bot className="h-4 w-4 text-primary" />
          {t("projectSettings.title", { defaultValue: "Agent-Settings" })}
          {data.is_v2 && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">
              {t("projectSettings.versionBadge", { defaultValue: "v2" })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {success && <span className="text-xs text-green-600">{success}</span>}
          {error && <span className="text-xs text-destructive">{error}</span>}
          <button onClick={handleSave} disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            {t("projectSettings.save", { defaultValue: "Speichern" })}
          </button>
          {onClose && (
            <button onClick={onClose} className="rounded-lg border px-2 py-1 text-xs transition hover:bg-accent">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      <ProjectTargetsSummary
        targets={targets}
        loading={targetsLoading}
        error={targetsError}
        isAdmin={isAdmin}
      />

      <div className="grid gap-4 md:grid-cols-2">
        {/* LLM-Config */}
        <div className="rounded-2xl border bg-background/55 p-3 space-y-3">
          <div className="flex items-center gap-2 text-xs font-medium">
            <Cpu className="h-3.5 w-3.5 text-primary" />
            {t("projectSettings.llm.title", { defaultValue: "LLM-Konfiguration" })}
          </div>

          {/* Provider */}
          <div>
            <label className="text-[11px] text-muted-foreground">{t("projectSettings.llm.provider", { defaultValue: "Provider" })}</label>
            <select value={provider} onChange={e => { setProvider(e.target.value); setModel(MODELS[e.target.value]?.[0] || ""); }}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              {PROVIDERS.map(p => <option key={p} value={p}>{PROVIDER_LABELS[p] ?? p}</option>)}
            </select>
          </div>

          {/* Model */}
          <div>
            <label className="text-[11px] text-muted-foreground">{t("projectSettings.llm.model", { defaultValue: "Modell" })}</label>
            <select value={model} onChange={e => setModel(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              {(MODELS[provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
              {!MODELS[provider]?.includes(model) && model && <option value={model}>{model}</option>}
            </select>
          </div>

          {/* Temperature */}
          <div>
            <label className="text-[11px] text-muted-foreground">
              {t("projectSettings.llm.temperature", { value: temperature, defaultValue: "Temperature: {{value}}" })}
            </label>
            <input type="range" min="0" max="1" step="0.1" value={temperature}
              onChange={e => setTemperature(parseFloat(e.target.value))}
              className="mt-0.5 w-full" />
          </div>

          {/* Max Tokens */}
          <div>
            <label className="text-[11px] text-muted-foreground">{t("projectSettings.llm.maxTokens", { defaultValue: "Max Tokens" })}</label>
            <input type="number" value={maxTokens} onChange={e => setMaxTokens(parseInt(e.target.value) || 4096)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs" />
          </div>

          {/* API Key */}
          <div>
            <label className="text-[11px] text-muted-foreground">{t("projectSettings.llm.apiKey", { defaultValue: "API-Key (Env-Variable)" })}</label>
            <select value={apiKeyEnv} onChange={e => setApiKeyEnv(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              <option value="">{t("projectSettings.llm.apiKeyDefault", { defaultValue: "Standard (aus Environment)" })}</option>
              {availableKeys.map(k => (
                <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
              ))}
            </select>
          </div>

          {/* Execution Mode (#568) */}
          <div>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Shield className="h-3 w-3" /> {t("projectSettings.execMode.label", { defaultValue: "Berechtigungen" })}
            </label>
            <select value={executionMode} onChange={e => setExecutionMode(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              <option value="safe">{t("projectSettings.execMode.safe", { defaultValue: "Safe — Blocklist aktiv, kein sudo" })}</option>
              <option value="elevated">{t("projectSettings.execMode.elevated", { defaultValue: "Elevated — erweiterte Rechte" })}</option>
              <option value="unrestricted">{t("projectSettings.execMode.unrestricted", { defaultValue: "Unrestricted — volle Rechte, sudo erlaubt" })}</option>
            </select>
          </div>

          {/* Max Tool Rounds (#613) */}
          <div>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Shield className="h-3 w-3" /> {t("projectSettings.maxToolRounds.label", { defaultValue: "Max Tool-Aufrufe pro Nachricht" })}
            </label>
            <input
              type="number"
              min={1} max={200}
              value={maxToolRounds}
              onChange={e => setMaxToolRounds(Number(e.target.value))}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs"
            />
          </div>

          {/* Risiko-Policy — Trusted-Boss ohne CONFIRM-Klicks (Admin-only) */}
          <div className="md:col-span-2">
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Shield className="h-3 w-3" /> {t("projectSettings.risk.label", { defaultValue: "Risiko-Policy (Projekt-Boss)" })}
            </label>
            <select
              value={riskPolicy}
              onChange={e => setRiskPolicy(e.target.value as "interactive" | "trusted")}
              className={`mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs ${riskPolicy === "trusted" ? "border-red-500 text-red-600 font-semibold" : ""}`}
            >
              <option value="interactive">{t("projectSettings.risk.interactive", { defaultValue: "Interactive — Bestätigung bei riskanten Aktionen" })}</option>
              <option value="trusted">{t("projectSettings.risk.trusted", { defaultValue: "⚠ Trusted — CONFIRM automatisch genehmigen (nur Admin)" })}</option>
            </select>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {riskPolicy === "trusted"
                ? t("projectSettings.risk.descTrusted", { defaultValue: "⚠ Der Projekt-Boss führt CONFIRM-Aktionen ohne Klick aus. DENY bleibt blockiert. Auto-Approves landen im Server-Log. Nur Admins dürfen diesen Modus setzen — Speichern als Nicht-Admin schlägt mit 403 fehl." })
                : t("projectSettings.risk.descInteractive", { defaultValue: "Riskante Aktionen brauchen eine Bestätigung im Chat (Standard)." })}
            </p>
          </div>
        </div>

        {/* Members (#570) */}
        <div>
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1">
            <Hash className="h-3 w-3" /> {t("projectSettings.members.label", { defaultValue: "Members (Zugriff auf dieses Projekt)" })}
          </label>
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            {members.map(m => (
              <span key={m} className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5 text-[10px] font-mono">
                {m}
                <button onClick={() => setMembers(prev => prev.filter(x => x !== m))}
                  className="text-muted-foreground hover:text-destructive transition-colors ml-0.5">
                  <X className="h-2.5 w-2.5" />
                </button>
              </span>
            ))}
            {members.length === 0 && <span className="text-[10px] text-muted-foreground">{t("projectSettings.members.empty", { defaultValue: "Keine Members — nur Admins haben Zugriff" })}</span>}
          </div>
          <div className="flex gap-1.5">
            <select
              value={newMember}
              onChange={e => setNewMember(e.target.value)}
              className="flex-1 rounded-lg border bg-background px-2 py-1 text-xs"
            >
              <option value="">{t("projectSettings.members.pick", { defaultValue: "User auswählen..." })}</option>
              {allUsers.filter(u => !members.includes(u)).map(u => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
            <button
              onClick={() => { if (newMember && !members.includes(newMember)) { setMembers(p => [...p, newMember]); setNewMember(""); } }}
              disabled={!newMember}
              className="rounded-lg border bg-primary/10 px-2.5 py-1 text-xs text-primary hover:bg-primary/20 disabled:opacity-40 transition-colors"
            >
              {t("projectSettings.members.add", { defaultValue: "Hinzufügen" })}
            </button>
          </div>
        </div>

        {/* AGENT.md */}
        <div className="rounded-2xl border bg-background/55 p-3 space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium">
            <FileText className="h-3.5 w-3.5 text-primary" />
            {t("projectSettings.agentMd.title", { defaultValue: "AGENT.md — Persönlichkeit & Regeln" })}
          </div>
          {!canUseComposer && (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {t("projectSettings.agentMd.readOnlyHint", { defaultValue: "Nur Admin oder Personal-Projekt-Owner dürfen die AGENT.md (Projekt-Boss-Persona) ändern. Andere Settings kannst du weiter speichern." })}
            </p>
          )}
          <textarea
            value={agentMd}
            onChange={e => setAgentMd(e.target.value)}
            rows={14}
            readOnly={!canUseComposer}
            disabled={!canUseComposer}
            className={`w-full rounded-lg border bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y ${!canUseComposer ? "opacity-60 cursor-not-allowed" : ""}`}
            placeholder={t("projectSettings.agentMd.placeholder", { defaultValue: "# Agent\n\nBeschreibe hier das Fachgebiet, die Regeln und den Kontext." })}
          />
        </div>

        {/* #645 Phase 1e — Projekt-Boss Profile-Composer */}
        {canUseComposer && (
          <div className="rounded-2xl border bg-background/55 p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-medium">
                <Sparkles className="h-3.5 w-3.5 text-primary" />
                {t("projectSettings.composer.title", { defaultValue: "Profil-Composer (AGENT.md)" })}
              </div>
              <button
                type="button"
                onClick={() => setShowComposer(s => !s)}
                className="text-xs px-2 py-1 rounded-md border hover:bg-accent transition"
              >
                {showComposer
                  ? t("projectSettings.composer.close", { defaultValue: "Schließen" })
                  : t("projectSettings.composer.open", { defaultValue: "Öffnen" })}
              </button>
            </div>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {t("projectSettings.composer.intro", { defaultValue: "Der Composer überschreibt die AGENT.md dieses Projekts aus Bausteinen. Vor dem Überschreiben wird ein Backup als AGENT.md.backup angelegt. config.yaml und Mitglieder bleiben unberührt." })}
            </p>
            {showComposer && (
              <ProfileComposer scope="project" projectId={projectId} showSoulHint />
            )}
          </div>
        )}
      </div>

      {/* WhatsApp */}
      <div className="rounded-2xl border bg-background/55 p-3 space-y-3">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Shield className="h-3.5 w-3.5 text-green-600" />
          WhatsApp
          <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            waStatus === "connected" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" :
            waStatus === "connecting" || waConnecting ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400" :
            "bg-muted text-muted-foreground"
          }`}>
            {waStatus === "connected"
              ? (waPhone
                  ? t("projectSettings.whatsapp.status.connectedWithPhone", { phone: waPhone, defaultValue: "Verbunden ({{phone}})" })
                  : t("projectSettings.whatsapp.status.connected", { defaultValue: "Verbunden" }))
              : waConnecting
                ? t("projectSettings.whatsapp.status.connecting", { defaultValue: "Verbinde..." })
                : waStatus === "unavailable"
                  ? t("projectSettings.whatsapp.status.unavailable", { defaultValue: "Bridge nicht erreichbar" })
                  : t("projectSettings.whatsapp.status.disconnected", { defaultValue: "Nicht verbunden" })}
          </span>
        </div>

        {/* QR-Code */}
        {waQr && waStatus !== "connected" && (
          <div className="flex flex-col items-center gap-2 p-2">
            <p className="text-xs text-muted-foreground">{t("projectSettings.whatsapp.qrHint", { defaultValue: "QR-Code mit WhatsApp scannen:" })}</p>
            <img src={waQr.startsWith("data:") ? waQr : `data:image/png;base64,${waQr}`} alt="WhatsApp QR" className="w-48 h-48 rounded-lg border" />
          </div>
        )}

        {/* Buttons */}
        <div className="flex gap-2">
          {waStatus !== "connected" ? (
            <button onClick={connectWhatsApp} disabled={waConnecting}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs text-white transition hover:bg-green-700 disabled:opacity-50">
              {waConnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {waConnecting
                ? t("projectSettings.whatsapp.waitingQr", { defaultValue: "Warte auf QR-Scan..." })
                : t("projectSettings.whatsapp.btnConnect", { defaultValue: "WhatsApp verbinden" })}
            </button>
          ) : (
            <button onClick={disconnectWhatsApp}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg border border-destructive/30 px-3 py-1.5 text-xs text-destructive transition hover:bg-destructive/10">
              {t("projectSettings.whatsapp.btnDisconnect", { defaultValue: "WhatsApp trennen" })}
            </button>
          )}
        </div>

        {/* Filter-Config — nur wenn verbunden (#567) */}
        {waStatus === "connected" && (
          <div className="space-y-3 border-t pt-3">
            <p className="text-[10px] text-muted-foreground">
              {t("projectSettings.whatsapp.filterIntro", { defaultValue: "Diese Einstellungen gelten fuer deine persoenliche WhatsApp-Verbindung." })}
            </p>

            {/* Checkboxen */}
            <div className="flex gap-4">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={waPrivateChats} onChange={e => setWaPrivateChats(e.target.checked)} className="h-3 w-3 rounded" />
                {t("projectSettings.whatsapp.privateChats", { defaultValue: "Private Nachrichten" })}
              </label>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={waGroupChats} onChange={e => setWaGroupChats(e.target.checked)} className="h-3 w-3 rounded" />
                {t("projectSettings.whatsapp.groupChats", { defaultValue: "Gruppen-Nachrichten" })}
              </label>
            </div>

            {/* Keyword */}
            <div>
              <label className="text-[11px] text-muted-foreground">{t("projectSettings.whatsapp.keywordLabel", { defaultValue: "Aktivierungs-Keyword (leer = immer antworten)" })}</label>
              <input type="text" value={waRequireKeyword} onChange={e => setWaRequireKeyword(e.target.value)}
                placeholder={t("projectSettings.whatsapp.keywordPlaceholder", { defaultValue: "z.B. !bot" })}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs" />
            </div>

            {/* Nummern-Listen */}
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="text-[11px] text-muted-foreground">{t("projectSettings.whatsapp.ownerNumbers", { defaultValue: "Eigene Nummern (elevated)" })}</label>
                <textarea value={waOwnerNumbers} onChange={e => setWaOwnerNumbers(e.target.value)}
                  placeholder="+49123456789"
                  rows={3}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">{t("projectSettings.whatsapp.allowedNumbers", { defaultValue: "Whitelist (leer = alle)" })}</label>
                <textarea value={waAllowedNumbers} onChange={e => setWaAllowedNumbers(e.target.value)}
                  placeholder="+49123456789"
                  rows={3}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">{t("projectSettings.whatsapp.blockedNumbers", { defaultValue: "Blacklist" })}</label>
                <textarea value={waBlockedNumbers} onChange={e => setWaBlockedNumbers(e.target.value)}
                  placeholder="+49123456789"
                  rows={3}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
              </div>
            </div>

            {/* Voice */}
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <Volume2 className="h-3 w-3" /> {t("projectSettings.whatsapp.voiceMessages", { defaultValue: "Sprachnachrichten" })}
                </label>
                <select value={waVoiceMode} onChange={e => setWaVoiceMode(e.target.value)}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                  <option value="never">{t("projectSettings.whatsapp.voiceMode.never", { defaultValue: "Nie (nur Text)" })}</option>
                  <option value="echo">{t("projectSettings.whatsapp.voiceMode.echo", { defaultValue: "Nur Antwort auf Sprachnachrichten" })}</option>
                  <option value="always">{t("projectSettings.whatsapp.voiceMode.always", { defaultValue: "Immer als Audio" })}</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">{t("projectSettings.whatsapp.ttsVoice", { defaultValue: "TTS-Stimme" })}</label>
                <div className="flex gap-1.5 mt-0.5">
                  <select value={waVoiceName} onChange={e => setWaVoiceName(e.target.value)}
                    className="flex-1 rounded-lg border bg-background px-2 py-1.5 text-xs">
                    {waVoices.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
                    {!waVoices.find(v => v.id === waVoiceName) && waVoiceName && (
                      <option value={waVoiceName}>{waVoiceName}</option>
                    )}
                  </select>
                  <button onClick={previewVoice} disabled={waPreviewPlaying}
                    className="inline-flex items-center gap-1 rounded-lg border px-2 py-1.5 text-xs hover:bg-accent disabled:opacity-50"
                    title={t("projectSettings.whatsapp.previewTitle", { defaultValue: "Vorhoeren" })}>
                    {waPreviewPlaying ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                  </button>
                </div>
              </div>
            </div>

            {/* Save Button */}
            <div className="flex items-center gap-2">
              <button onClick={saveWhatsAppConfig} disabled={waSaving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs text-white transition hover:bg-green-700 disabled:opacity-50">
                {waSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                {t("projectSettings.whatsapp.saveBtn", { defaultValue: "WhatsApp-Config speichern" })}
              </button>
              {waSuccess && <span className="text-xs text-green-600">{waSuccess}</span>}
            </div>
          </div>
        )}
      </div>

      {/* Discord + Telegram (#569) */}
      <div className="rounded-2xl border bg-background/55 p-3 space-y-3">
        <div className="flex items-center gap-2 text-xs font-medium">
          <MessageCircle className="h-3.5 w-3.5 text-indigo-500" />
          {t("projectSettings.messenger.title", { defaultValue: "Messenger-Routing" })}
          <span className="text-[10px] text-muted-foreground font-normal ml-1">
            {t("projectSettings.messenger.subtitle", { defaultValue: "Welche Messenger-Kanaele leiten Nachrichten an dieses Projekt?" })}
          </span>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {/* Discord */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-indigo-400">
              <Hash className="h-3 w-3" /> Discord
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">{t("projectSettings.messenger.discord.botToken", { defaultValue: "Bot-Token (Env-Variable)" })}</label>
              <select value={discordBotTokenEnv} onChange={e => setDiscordBotTokenEnv(e.target.value)}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                <option value="">{t("projectSettings.messenger.discord.notConfigured", { defaultValue: "Nicht konfiguriert" })}</option>
                {availableKeys.map(k => (
                  <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">{t("projectSettings.messenger.discord.channelIds", { defaultValue: "Channel-IDs (eine pro Zeile)" })}</label>
              <textarea value={discordChannels} onChange={e => setDiscordChannels(e.target.value)}
                placeholder="123456789012345678"
                rows={3}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
            </div>
          </div>

          {/* Telegram */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-sky-400">
              <MessageCircle className="h-3 w-3" /> Telegram
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">{t("projectSettings.messenger.telegram.botToken", { defaultValue: "Bot-Token (Env-Variable)" })}</label>
              <select value={telegramBotTokenEnv} onChange={e => setTelegramBotTokenEnv(e.target.value)}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                <option value="">{t("projectSettings.messenger.telegram.notConfigured", { defaultValue: "Nicht konfiguriert" })}</option>
                {availableKeys.map(k => (
                  <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">{t("projectSettings.messenger.telegram.chatIds", { defaultValue: "Chat-IDs (eine pro Zeile)" })}</label>
              <textarea value={telegramChatIds} onChange={e => setTelegramChatIds(e.target.value)}
                placeholder="-1001234567890"
                rows={3}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
            </div>
          </div>
        </div>

        {/* Save */}
        <div className="flex items-center gap-2">
          <button onClick={saveMessengerConfig} disabled={messengerSaving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white transition hover:bg-indigo-700 disabled:opacity-50">
            {messengerSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            {t("projectSettings.messenger.saveBtn", { defaultValue: "Messenger-Config speichern" })}
          </button>
          {messengerSuccess && <span className="text-xs text-green-600">{messengerSuccess}</span>}
        </div>
      </div>

      {/* Bootstrap-Memory (#614) */}
      <div className="rounded-2xl border bg-background/55 p-3 space-y-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Database className="h-3.5 w-3.5 text-primary" />
          {t("projectSettings.bootstrapMemory.title", { defaultValue: "Memory aufbauen" })}
        </div>
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          {t("projectSettings.bootstrapMemory.description", { defaultValue: "Scannt Projekt-Verzeichnis und erstellt eine strukturierte Memory-Basis (Verzeichnisbaum, wichtige Dateien). Nur einmalig nötig — der Agent pflegt die Memory danach selbst." })}
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => runBootstrapMemory(false)} disabled={bootstrapRunning}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
            {bootstrapRunning ? <Loader2 className="h-3 w-3 animate-spin" /> : <Database className="h-3 w-3" />}
            {t("projectSettings.bootstrapMemory.btnBuild", { defaultValue: "Memory aufbauen" })}
          </button>
          <button onClick={() => runBootstrapMemory(true)} disabled={bootstrapRunning}
            className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition hover:bg-accent disabled:opacity-50">
            {t("projectSettings.bootstrapMemory.btnRebuild", { defaultValue: "Neu aufbauen" })}
          </button>
          {bootstrapResult && <span className="text-[10px] text-muted-foreground">{bootstrapResult}</span>}
        </div>
      </div>
    </div>
  );
}

function ProjectTargetsSummary({
  targets,
  loading,
  error,
  isAdmin,
}: {
  targets: ProjectTargetsResponse | null;
  loading: boolean;
  error: string;
  isAdmin: boolean;
}) {
  const { t } = useTranslation();
  const servers = targets?.servers ?? [];
  const wks = targets?.wks ?? [];
  const hasTargets = servers.length > 0 || wks.length > 0;

  return (
    <div className="rounded-2xl border bg-background/55 p-3 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Server className="h-3.5 w-3.5 text-primary" />
          {t("projectSettings.targets.title", { defaultValue: "Zugewiesene Zielsysteme" })}
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-normal text-muted-foreground">
            {t("projectSettings.targets.count", {
              count: servers.length + wks.length,
              defaultValue: "{{count}} Ziele",
            })}
          </span>
        </div>
        {isAdmin && (
          <Link
            to="/target-systems"
            className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] transition hover:bg-accent"
          >
            {t("projectSettings.targets.manage", { defaultValue: "In Zielsysteme verwalten" })}
            <ExternalLink className="h-3 w-3" />
          </Link>
        )}
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t("projectSettings.targets.loading", { defaultValue: "Zielsysteme laden..." })}
        </div>
      )}

      {!loading && error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
          {error}
        </div>
      )}

      {!loading && !error && !hasTargets && (
        <p className="text-[11px] text-muted-foreground">
          {t("projectSettings.targets.empty", {
            defaultValue: "Diesem Projekt sind noch keine Root-/Remote-Server oder WKS zugewiesen.",
          })}
        </p>
      )}

      {!loading && !error && hasTargets && (
        <div className="grid gap-3 md:grid-cols-2">
          <TargetList
            icon={<Server className="h-3.5 w-3.5 text-primary" />}
            title={t("projectSettings.targets.servers", { defaultValue: "Root-/Remote-Server" })}
            empty={t("projectSettings.targets.noServers", { defaultValue: "Keine Server zugewiesen." })}
            items={servers.map(s => ({
              id: s.server_id,
              title: s.name || s.server_id,
              subtitle: s.stale
                ? t("projectSettings.targets.staleServer", { defaultValue: "Server existiert nicht mehr" })
                : `${s.ssh_user || "ssh"}@${s.ip || "?"}:${s.ssh_port ?? 22}`,
              role: s.role,
              note: s.note,
              stale: s.stale,
              keyMissing: s.has_ssh_key === false && !s.stale,
            }))}
          />
          <TargetList
            icon={<Monitor className="h-3.5 w-3.5 text-cyan-500" />}
            title={t("projectSettings.targets.wks", { defaultValue: "WKS" })}
            empty={t("projectSettings.targets.noWks", { defaultValue: "Keine WKS zugewiesen." })}
            items={wks.map(w => ({
              id: w.username,
              title: w.username,
              subtitle: w.stale
                ? t("projectSettings.targets.staleWks", { defaultValue: "WKS existiert nicht mehr" })
                : `${w.ssh_user || w.username}@${w.ip || "?"}:${w.ssh_port ?? 22}`,
              role: w.role,
              note: w.note,
              stale: w.stale,
              keyMissing: w.has_ssh_key === false && !w.stale,
            }))}
          />
        </div>
      )}
    </div>
  );
}

function TargetList({
  icon,
  title,
  empty,
  items,
}: {
  icon: ReactNode;
  title: string;
  empty: string;
  items: Array<{
    id: string;
    title: string;
    subtitle: string;
    role?: string;
    note?: string;
    stale?: boolean;
    keyMissing?: boolean;
  }>;
}) {
  const { t } = useTranslation();

  return (
    <div className="rounded-xl border bg-muted/20 p-2.5 space-y-2">
      <div className="flex items-center gap-2 text-[11px] font-medium">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">{empty}</p>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <div
              key={item.id}
              className={`rounded-lg border bg-background px-2.5 py-2 text-[11px] ${
                item.stale ? "border-amber-400/70 bg-amber-50/60 dark:bg-amber-950/20" : ""
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium">{item.title}</div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{item.subtitle}</div>
                </div>
                {item.role && (
                  <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                    {item.role}
                  </span>
                )}
              </div>
              {item.note && (
                <p className="mt-1 text-[10px] text-muted-foreground">{item.note}</p>
              )}
              {(item.stale || item.keyMissing) && (
                <div className="mt-1.5 flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-3 w-3" />
                  {item.stale
                    ? t("projectSettings.targets.staleHint", { defaultValue: "Eintrag prüfen oder entfernen." })
                    : t("projectSettings.targets.keyMissing", { defaultValue: "SSH-Key fehlt." })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
