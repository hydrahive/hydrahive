/**
 * ProjectSettingsPanel.tsx — v2 Agent-Settings für ein Projekt
 *
 * Zeigt und editiert: LLM-Config, AGENT.md, Execution-Mode.
 * Nutzt GET/PUT /projects/{id}/settings Endpoints.
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
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
  messenger: {
    whatsapp?: { session_ids?: string[]; enabled?: boolean };
    discord?: { channels?: string[]; bot_token_env?: string };
    telegram?: { chat_ids?: string[]; bot_token_env?: string };
  };
}

const PROVIDERS = ["anthropic", "openai", "google", "ollama", "deepseek"];

const MODELS: Record<string, string[]> = {
  anthropic: ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
  openai: ["gpt-4o", "gpt-4o-mini", "o3", "o3-mini"],
  google: ["gemini-2.0-flash", "gemini-2.5-pro"],
  ollama: ["llama3.1", "qwen2.5:7b", "mistral"],
  deepseek: ["deepseek-r1"],
};

export function ProjectSettingsPanel({ projectId, onClose }: ProjectSettingsPanelProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [data, setData] = useState<SettingsData | null>(null);

  // Editable fields
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [temperature, setTemperature] = useState(0.5);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [agentMd, setAgentMd] = useState("");
  const [executionMode, setExecutionMode] = useState("safe");
  const [maxToolRounds, setMaxToolRounds] = useState(50);
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

  useEffect(() => {
    loadSettings();
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
      const res = await api.get<any>("/me/whatsapp");
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
      await api.put("/me/whatsapp/config", {
        private_chats_enabled: waPrivateChats,
        group_chats_enabled: waGroupChats,
        require_keyword: waRequireKeyword || null,
        owner_numbers: nums(waOwnerNumbers),
        allowed_numbers: nums(waAllowedNumbers),
        blocked_numbers: nums(waBlockedNumbers),
        voice_mode: waVoiceMode,
        voice_name: waVoiceName,
      });
      setWaSuccess("Gespeichert!");
      setTimeout(() => setWaSuccess(""), 3000);
    } catch (e: any) {
      setError(e?.message || "WhatsApp-Config speichern fehlgeschlagen");
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
      setMessengerSuccess("Gespeichert!");
      setTimeout(() => setMessengerSuccess(""), 3000);
    } catch (e: any) {
      setError(e?.message || "Messenger-Config speichern fehlgeschlagen");
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
        body: JSON.stringify({ voice: waVoiceName, text: "Hallo, ich bin dein HydraHive Assistent." }),
      });
      if (!res.ok) throw new Error("Preview fehlgeschlagen");
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
      const res = await api.post<any>("/me/whatsapp/connect", {});
      const d = res as any;
      setWaStatus(d.status || "connecting");
      setWaQr(d.qr || "");
      // Polling starten für QR-Update
      const poll = setInterval(async () => {
        try {
          const s = await api.get<any>("/me/whatsapp");
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
      setError(e?.message || "WhatsApp-Verbindung fehlgeschlagen");
      setWaConnecting(false);
    }
  }

  async function disconnectWhatsApp() {
    try {
      await api.delete<any>("/me/whatsapp");
      setWaStatus("disconnected");
      setWaQr("");
      setWaPhone("");
    } catch (e: any) {
      setError(e?.message || "Trennen fehlgeschlagen");
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
      setMembers(d.members || []);
      // Messenger-Config laden (#569)
      const m = d.messenger || {};
      setDiscordBotTokenEnv(m.discord?.bot_token_env || "");
      setDiscordChannels((m.discord?.channels || []).join("\n"));
      setTelegramBotTokenEnv(m.telegram?.bot_token_env || "");
      setTelegramChatIds((m.telegram?.chat_ids || []).join("\n"));
    } catch (e: any) {
      setError(e?.message || "Fehler beim Laden");
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

  async function handleSave() {
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      await api.put(`/projects/${projectId}/settings`, {
        provider,
        model,
        temperature,
        max_tokens: maxTokens,
        api_key_env: apiKeyEnv,
        agent_md: agentMd,
        execution_mode: executionMode,
        max_tool_rounds: maxToolRounds,
        members,
      });
      setSuccess("Gespeichert!");
      setTimeout(() => setSuccess(""), 3000);
    } catch (e: any) {
      setError(e?.message || "Fehler beim Speichern");
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
    return <div className="p-4 text-sm text-destructive">{error || "Keine Daten"}</div>;
  }

  return (
    <div className="border-t px-4 pb-4 pt-3 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Bot className="h-4 w-4 text-primary" />
          Agent-Settings
          {data.is_v2 && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">v2</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {success && <span className="text-xs text-green-600">{success}</span>}
          {error && <span className="text-xs text-destructive">{error}</span>}
          <button onClick={handleSave} disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Speichern
          </button>
          {onClose && (
            <button onClick={onClose} className="rounded-lg border px-2 py-1 text-xs transition hover:bg-accent">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {/* LLM-Config */}
        <div className="rounded-2xl border bg-background/55 p-3 space-y-3">
          <div className="flex items-center gap-2 text-xs font-medium">
            <Cpu className="h-3.5 w-3.5 text-primary" />
            LLM-Konfiguration
          </div>

          {/* Provider */}
          <div>
            <label className="text-[11px] text-muted-foreground">Provider</label>
            <select value={provider} onChange={e => { setProvider(e.target.value); setModel(MODELS[e.target.value]?.[0] || ""); }}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>

          {/* Model */}
          <div>
            <label className="text-[11px] text-muted-foreground">Modell</label>
            <select value={model} onChange={e => setModel(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              {(MODELS[provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
              {!MODELS[provider]?.includes(model) && model && <option value={model}>{model}</option>}
            </select>
          </div>

          {/* Temperature */}
          <div>
            <label className="text-[11px] text-muted-foreground">Temperature: {temperature}</label>
            <input type="range" min="0" max="1" step="0.1" value={temperature}
              onChange={e => setTemperature(parseFloat(e.target.value))}
              className="mt-0.5 w-full" />
          </div>

          {/* Max Tokens */}
          <div>
            <label className="text-[11px] text-muted-foreground">Max Tokens</label>
            <input type="number" value={maxTokens} onChange={e => setMaxTokens(parseInt(e.target.value) || 4096)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs" />
          </div>

          {/* API Key */}
          <div>
            <label className="text-[11px] text-muted-foreground">API-Key (Env-Variable)</label>
            <select value={apiKeyEnv} onChange={e => setApiKeyEnv(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              <option value="">Standard (aus Environment)</option>
              {availableKeys.map(k => (
                <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
              ))}
            </select>
          </div>

          {/* Execution Mode (#568) */}
          <div>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Shield className="h-3 w-3" /> Berechtigungen
            </label>
            <select value={executionMode} onChange={e => setExecutionMode(e.target.value)}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
              <option value="safe">Safe — Blocklist aktiv, kein sudo</option>
              <option value="elevated">Elevated — erweiterte Rechte</option>
              <option value="unrestricted">Unrestricted — volle Rechte, sudo erlaubt</option>
            </select>
          </div>

          {/* Max Tool Rounds (#613) */}
          <div>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Shield className="h-3 w-3" /> Max Tool-Aufrufe pro Nachricht
            </label>
            <input
              type="number"
              min={1} max={200}
              value={maxToolRounds}
              onChange={e => setMaxToolRounds(Number(e.target.value))}
              className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs"
            />
          </div>
        </div>

        {/* Members (#570) */}
        <div>
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1">
            <Hash className="h-3 w-3" /> Members (Zugriff auf dieses Projekt)
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
            {members.length === 0 && <span className="text-[10px] text-muted-foreground">Keine Members — nur Admins haben Zugriff</span>}
          </div>
          <div className="flex gap-1.5">
            <select
              value={newMember}
              onChange={e => setNewMember(e.target.value)}
              className="flex-1 rounded-lg border bg-background px-2 py-1 text-xs"
            >
              <option value="">User auswählen...</option>
              {allUsers.filter(u => !members.includes(u)).map(u => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
            <button
              onClick={() => { if (newMember && !members.includes(newMember)) { setMembers(p => [...p, newMember]); setNewMember(""); } }}
              disabled={!newMember}
              className="rounded-lg border bg-primary/10 px-2.5 py-1 text-xs text-primary hover:bg-primary/20 disabled:opacity-40 transition-colors"
            >
              Hinzufügen
            </button>
          </div>
        </div>

        {/* AGENT.md */}
        <div className="rounded-2xl border bg-background/55 p-3 space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium">
            <FileText className="h-3.5 w-3.5 text-primary" />
            AGENT.md — Persönlichkeit &amp; Regeln
          </div>
          <textarea
            value={agentMd}
            onChange={e => setAgentMd(e.target.value)}
            rows={14}
            className="w-full rounded-lg border bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y"
            placeholder="# Agent&#10;&#10;Beschreibe hier das Fachgebiet, die Regeln und den Kontext."
          />
        </div>
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
            {waStatus === "connected" ? `Verbunden${waPhone ? ` (${waPhone})` : ""}` :
             waConnecting ? "Verbinde..." :
             waStatus === "unavailable" ? "Bridge nicht erreichbar" :
             "Nicht verbunden"}
          </span>
        </div>

        {/* QR-Code */}
        {waQr && waStatus !== "connected" && (
          <div className="flex flex-col items-center gap-2 p-2">
            <p className="text-xs text-muted-foreground">QR-Code mit WhatsApp scannen:</p>
            <img src={waQr.startsWith("data:") ? waQr : `data:image/png;base64,${waQr}`} alt="WhatsApp QR" className="w-48 h-48 rounded-lg border" />
          </div>
        )}

        {/* Buttons */}
        <div className="flex gap-2">
          {waStatus !== "connected" ? (
            <button onClick={connectWhatsApp} disabled={waConnecting}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs text-white transition hover:bg-green-700 disabled:opacity-50">
              {waConnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {waConnecting ? "Warte auf QR-Scan..." : "WhatsApp verbinden"}
            </button>
          ) : (
            <button onClick={disconnectWhatsApp}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg border border-destructive/30 px-3 py-1.5 text-xs text-destructive transition hover:bg-destructive/10">
              WhatsApp trennen
            </button>
          )}
        </div>

        {/* Filter-Config — nur wenn verbunden (#567) */}
        {waStatus === "connected" && (
          <div className="space-y-3 border-t pt-3">
            <p className="text-[10px] text-muted-foreground">
              Diese Einstellungen gelten fuer deine persoenliche WhatsApp-Verbindung.
            </p>

            {/* Checkboxen */}
            <div className="flex gap-4">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={waPrivateChats} onChange={e => setWaPrivateChats(e.target.checked)} className="h-3 w-3 rounded" />
                Private Nachrichten
              </label>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={waGroupChats} onChange={e => setWaGroupChats(e.target.checked)} className="h-3 w-3 rounded" />
                Gruppen-Nachrichten
              </label>
            </div>

            {/* Keyword */}
            <div>
              <label className="text-[11px] text-muted-foreground">Aktivierungs-Keyword (leer = immer antworten)</label>
              <input type="text" value={waRequireKeyword} onChange={e => setWaRequireKeyword(e.target.value)}
                placeholder="z.B. !bot"
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs" />
            </div>

            {/* Nummern-Listen */}
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="text-[11px] text-muted-foreground">Eigene Nummern (elevated)</label>
                <textarea value={waOwnerNumbers} onChange={e => setWaOwnerNumbers(e.target.value)}
                  placeholder="+49123456789"
                  rows={3}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">Whitelist (leer = alle)</label>
                <textarea value={waAllowedNumbers} onChange={e => setWaAllowedNumbers(e.target.value)}
                  placeholder="+49123456789"
                  rows={3}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs font-mono resize-y" />
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">Blacklist</label>
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
                  <Volume2 className="h-3 w-3" /> Sprachnachrichten
                </label>
                <select value={waVoiceMode} onChange={e => setWaVoiceMode(e.target.value)}
                  className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                  <option value="never">Nie (nur Text)</option>
                  <option value="echo">Nur Antwort auf Sprachnachrichten</option>
                  <option value="always">Immer als Audio</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">TTS-Stimme</label>
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
                    title="Vorhoeren">
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
                WhatsApp-Config speichern
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
          Messenger-Routing
          <span className="text-[10px] text-muted-foreground font-normal ml-1">
            Welche Messenger-Kanaele leiten Nachrichten an dieses Projekt?
          </span>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {/* Discord */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-indigo-400">
              <Hash className="h-3 w-3" /> Discord
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Bot-Token (Env-Variable)</label>
              <select value={discordBotTokenEnv} onChange={e => setDiscordBotTokenEnv(e.target.value)}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                <option value="">Nicht konfiguriert</option>
                {availableKeys.map(k => (
                  <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Channel-IDs (eine pro Zeile)</label>
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
              <label className="text-[11px] text-muted-foreground">Bot-Token (Env-Variable)</label>
              <select value={telegramBotTokenEnv} onChange={e => setTelegramBotTokenEnv(e.target.value)}
                className="mt-0.5 w-full rounded-lg border bg-background px-2 py-1.5 text-xs">
                <option value="">Nicht konfiguriert</option>
                {availableKeys.map(k => (
                  <option key={k.name} value={k.name}>{k.name} ({k.preview})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-muted-foreground">Chat-IDs (eine pro Zeile)</label>
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
            Messenger-Config speichern
          </button>
          {messengerSuccess && <span className="text-xs text-green-600">{messengerSuccess}</span>}
        </div>
      </div>
    </div>
  );
}
