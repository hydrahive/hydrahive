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
  const [availableKeys, setAvailableKeys] = useState<{ name: string; preview: string }[]>([]);

  // WhatsApp
  const [waStatus, setWaStatus] = useState<string>("unknown");
  const [waQr, setWaQr] = useState<string>("");
  const [waPhone, setWaPhone] = useState<string>("");
  const [waConnecting, setWaConnecting] = useState(false);

  useEffect(() => {
    loadSettings();
    loadKeys();
    loadWhatsAppStatus();
  }, [projectId]);

  async function loadWhatsAppStatus() {
    try {
      const res = await api.get<any>("/me/whatsapp");
      const d = res as any;
      setWaStatus(d.status || "unknown");
      setWaQr(d.qr || "");
      setWaPhone(d.phone || "");
    } catch {
      setWaStatus("unavailable");
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
      await api.post<any>("/me/whatsapp/disconnect", {});
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
      </div>
    </div>
  );
}
