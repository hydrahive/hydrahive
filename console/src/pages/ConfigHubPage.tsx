import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/hooks/useAuth";
import {
  CheckCircle, XCircle, ChevronDown, ChevronRight, Cpu, Key,
  MessageSquare, GitBranch, Github, Mail, Network, Mic, Save, Loader2, AlertTriangle, Clock,
} from "lucide-react";

/* ── Types ──────────────────────────────────────────────────────── */

interface SectionProps {
  title: string;
  icon: React.ElementType;
  configured: boolean | null;  // null = loading
  defaultOpen?: boolean;
  badge?: string;
  children: React.ReactNode;
}

interface LlmProvider {
  enabled: boolean;
  api_key?: string;
  has_key?: boolean;
}

/* ── Collapsible Section ────────────────────────────────────────── */

function Section({ title, icon: Icon, configured, defaultOpen, badge, children }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const Chevron = open ? ChevronDown : ChevronRight;

  const statusColor =
    configured === null ? "text-muted-foreground" :
    configured ? "text-green-500" : "text-zinc-500";
  const StatusIcon = configured === null ? Loader2 : configured ? CheckCircle : XCircle;

  return (
    <div className="rounded-xl border bg-card">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-3 px-5 py-4 text-left hover:bg-muted/30 transition-colors rounded-xl"
      >
        <Icon className="h-5 w-5 text-primary shrink-0" />
        <span className="font-medium flex-1">{title}</span>
        {badge && <span className="text-xs text-muted-foreground">{badge}</span>}
        <StatusIcon className={`h-4 w-4 shrink-0 ${statusColor} ${configured === null ? "animate-spin" : ""}`} />
        <Chevron className="h-4 w-4 text-muted-foreground shrink-0" />
      </button>
      {open && (
        <div className="px-5 pb-5 pt-1 border-t border-border/50">
          {children}
        </div>
      )}
    </div>
  );
}

/* ── Save Button ─────────────────────────────────────────────── */

function SaveBtn({ saving, disabled, onClick }: { saving: boolean; disabled?: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      onClick={onClick}
      disabled={saving || disabled}
      className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40 transition-colors"
    >
      {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
      {saving ? t("common.saving") : t("common.save")}
    </button>
  );
}

/* ── Input helper ─────────────────────────────────────────────── */

const inputCls = "w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono";
const labelCls = "text-sm font-medium";
const hintCls = "text-xs text-muted-foreground";

/* ── LLM Section ─────────────────────────────────────────────── */

function LlmSection() {
  const [providers, setProviders] = useState<Record<string, LlmProvider>>({});
  const [models, setModels] = useState<{ id: string; label: string; provider: string }[]>([]);
  const [systemModel, setSystemModel] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get<{ providers: Record<string, LlmProvider> }>("/llm/config"),
      api.availableModels(),
      api.getSystemDefaultModel(),
    ]).then(([cfg, mdl, def]) => {
      setProviders(cfg.providers ?? {});
      setModels(mdl.models ?? []);
      setSystemModel(def.model ?? "");
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const configured = loaded ? !!(providers.anthropic?.has_key || providers.openai?.has_key) : null;

  async function save() {
    setSaving(true); setMsg("");
    try {
      if (anthropicKey.trim())
        await api.put("/llm/config/anthropic", { provider: "anthropic", api_key: anthropicKey.trim(), enabled: true });
      if (openaiKey.trim())
        await api.put("/llm/config/openai", { provider: "openai", api_key: openaiKey.trim(), enabled: true });
      if (systemModel)
        await api.setSystemDefaultModel(systemModel);
      // Reload
      const cfg = await api.get<{ providers: Record<string, LlmProvider> }>("/llm/config");
      setProviders(cfg.providers ?? {});
      setAnthropicKey(""); setOpenaiKey("");
      setMsg("Gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Fehler");
    } finally { setSaving(false); }
  }

  return (
    <Section title="LLM-Provider" icon={Cpu} configured={configured} defaultOpen badge={configured ? "Aktiv" : "Kein API-Key"}>
      <div className="space-y-4 mt-2">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className={labelCls}>Anthropic API-Key</label>
            <div className="flex items-center gap-2">
              <input type="password" value={anthropicKey} onChange={e => setAnthropicKey(e.target.value)}
                placeholder={providers.anthropic?.has_key ? "sk-ant-***  (gesetzt)" : "sk-ant-api03-..."} className={inputCls} />
              {providers.anthropic?.has_key && <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />}
            </div>
          </div>
          <div className="space-y-1.5">
            <label className={labelCls}>OpenAI API-Key</label>
            <div className="flex items-center gap-2">
              <input type="password" value={openaiKey} onChange={e => setOpenaiKey(e.target.value)}
                placeholder={providers.openai?.has_key ? "sk-***  (gesetzt)" : "sk-..."} className={inputCls} />
              {providers.openai?.has_key && <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />}
            </div>
          </div>
        </div>
        <div className="space-y-1.5">
          <label className={labelCls}>Standard-Modell</label>
          <select value={systemModel} onChange={e => setSystemModel(e.target.value)}
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
            <option value="">— kein Standard-Modell —</option>
            {models.map(m => <option key={m.id} value={m.id}>{m.label} ({m.provider})</option>)}
          </select>
          <p className={hintCls}>Wird verwendet wenn ein Agent kein eigenes Modell konfiguriert hat.</p>
        </div>
        <div className="flex items-center gap-3">
          <SaveBtn saving={saving} onClick={save} disabled={!anthropicKey.trim() && !openaiKey.trim() && !systemModel} />
          {msg && <span className="text-sm text-green-500">{msg}</span>}
        </div>
      </div>
    </Section>
  );
}

/* ── Platforms Section ────────────────────────────────────────── */

interface PlatformEntry {
  platform: string;
  label: string;
  configured: boolean;
  connected?: boolean;
  status?: string;
  details?: Record<string, unknown>;
}

function PlatformsSection() {
  const [platforms, setPlatforms] = useState<PlatformEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  // Per-platform form state
  const [discordToken, setDiscordToken] = useState("");
  const [discordGuild, setDiscordGuild] = useState("");
  const [telegramToken, setTelegramToken] = useState("");
  const [saving, setSaving] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api.myPlatforms().then(r => { setPlatforms(r.platforms ?? []); setLoaded(true); }).catch(() => setLoaded(true));
  }, []);

  useEffect(() => { load(); }, [load]);

  const configuredCount = platforms.filter(p => p.configured).length;
  const configured = loaded ? configuredCount > 0 : null;

  async function saveDiscord() {
    setSaving("discord"); setMsg("");
    try {
      await api.put("/me/discord", { token: discordToken.trim() || undefined, guild_id: discordGuild.trim() || undefined });
      setDiscordToken(""); setDiscordGuild("");
      load();
      setMsg("Discord gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(""); }
  }

  async function saveTelegram() {
    setSaving("telegram"); setMsg("");
    try {
      await api.put("/me/telegram", { bot_token: telegramToken.trim() });
      setTelegramToken("");
      load();
      setMsg("Telegram gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(""); }
  }

  async function connectWhatsApp() {
    setSaving("whatsapp"); setMsg("");
    try {
      await api.post("/me/whatsapp/connect", {});
      load();
      setMsg("WhatsApp-Verbindung gestartet — QR-Code auf der My Agent Seite scannen");
      setTimeout(() => setMsg(""), 5000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(""); }
  }

  return (
    <Section title="Plattformen" icon={MessageSquare} configured={configured}
      badge={loaded ? `${configuredCount}/${platforms.length}` : undefined}>
      <div className="space-y-3 mt-2">
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {platforms.map(p => (
            <button key={p.platform} onClick={() => setExpanded(expanded === p.platform ? null : p.platform)}
              className={`rounded-lg border p-3 text-left transition-colors hover:bg-muted/30 ${
                expanded === p.platform ? "ring-1 ring-primary" : ""
              }`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">{p.label}</span>
                {p.configured
                  ? <CheckCircle className="h-4 w-4 text-green-500" />
                  : <XCircle className="h-4 w-4 text-zinc-500" />}
              </div>
              <p className="text-xs text-muted-foreground">
                {p.configured ? (p.connected ? "Verbunden" : "Konfiguriert") : "Nicht eingerichtet"}
              </p>
            </button>
          ))}
        </div>

        {/* Discord expanded */}
        {expanded === "discord" && (
          <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
            <p className="text-sm font-medium">Discord Bot einrichten</p>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <label className={labelCls}>Bot-Token</label>
                <input type="password" value={discordToken} onChange={e => setDiscordToken(e.target.value)}
                  placeholder={platforms.find(p => p.platform === "discord")?.configured ? "(gesetzt)" : "Bot-Token eingeben"}
                  className={inputCls} />
              </div>
              <div className="space-y-1">
                <label className={labelCls}>Guild-ID</label>
                <input value={discordGuild} onChange={e => setDiscordGuild(e.target.value)}
                  placeholder="Server-ID" className={inputCls} />
              </div>
            </div>
            <SaveBtn saving={saving === "discord"} onClick={saveDiscord} />
          </div>
        )}

        {/* WhatsApp expanded */}
        {expanded === "whatsapp" && (
          <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
            <p className="text-sm font-medium">WhatsApp verbinden</p>
            <p className={hintCls}>Startet die Bridge. Den QR-Code dann unter My Agent → WhatsApp scannen.</p>
            <button onClick={connectWhatsApp} disabled={saving === "whatsapp"}
              className="flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-40 transition-colors">
              {saving === "whatsapp" ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
              Verbinden
            </button>
          </div>
        )}

        {/* Telegram expanded */}
        {expanded === "telegram" && (
          <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
            <p className="text-sm font-medium">Telegram Bot einrichten</p>
            <div className="space-y-1">
              <label className={labelCls}>Bot-Token</label>
              <input type="password" value={telegramToken} onChange={e => setTelegramToken(e.target.value)}
                placeholder={platforms.find(p => p.platform === "telegram")?.configured ? "(gesetzt)" : "123456:ABC-DEF..."}
                className={inputCls} />
              <p className={hintCls}>Von @BotFather auf Telegram</p>
            </div>
            <SaveBtn saving={saving === "telegram"} onClick={saveTelegram} />
          </div>
        )}

        {/* Mail expanded */}
        {expanded === "mail" && (
          <div className="rounded-lg border bg-muted/10 p-4 space-y-2">
            <p className="text-sm font-medium">E-Mail</p>
            <p className={hintCls}>Mail-Konfiguration unter My Agent → Mail Tab einrichten (Auto-KAS oder manueller SMTP).</p>
          </div>
        )}

        {msg && <p className="text-sm text-green-500">{msg}</p>}
      </div>
    </Section>
  );
}

/* ── Git Section ──────────────────────────────────────────────── */

function GitSection() {
  const [ghStatus, setGhStatus] = useState<{ configured: boolean } | null>(null);
  const [giteaCfg, setGiteaCfg] = useState<{ token?: string; url?: string; org?: string } | null>(null);
  const [ghToken, setGhToken] = useState("");
  const [giteaUrl, setGiteaUrl] = useState("");
  const [giteaToken, setGiteaToken] = useState("");
  const [giteaOrg, setGiteaOrg] = useState("");
  const [saving, setSaving] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.githubTokenStatus().then(r => setGhStatus(r as { configured: boolean })).catch(e => console.error("Failed to load GitHub token status", e));
    api.giteaConfig().then(r => { setGiteaCfg(r); setGiteaUrl(r.url || ""); setGiteaOrg(r.org || ""); }).catch(e => console.error("Failed to load Gitea config", e));
  }, []);

  const configured = ghStatus !== null && giteaCfg !== null
    ? !!(ghStatus.configured || giteaCfg.token)
    : null;

  async function saveGithub() {
    setSaving("gh"); setMsg("");
    try {
      await api.saveGithubToken(ghToken.trim());
      setGhToken("");
      const s = await api.githubTokenStatus();
      setGhStatus(s as { configured: boolean });
      setMsg("GitHub Token gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(""); }
  }

  async function saveGitea() {
    setSaving("gitea"); setMsg("");
    try {
      await api.updateGiteaConfig({ url: giteaUrl, token: giteaToken.trim() || undefined, org: giteaOrg } as any);
      setGiteaToken("");
      const c = await api.giteaConfig();
      setGiteaCfg(c);
      setMsg("Gitea gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(""); }
  }

  return (
    <Section title="Git-Integration" icon={GitBranch} configured={configured}
      badge={configured ? "Aktiv" : undefined}>
      <div className="space-y-4 mt-2">
        <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Github className="h-4 w-4" />
            <span className="text-sm font-medium">GitHub</span>
            {ghStatus?.configured && <CheckCircle className="h-4 w-4 text-green-500" />}
          </div>
          <div className="space-y-1">
            <input type="password" value={ghToken} onChange={e => setGhToken(e.target.value)}
              placeholder={ghStatus?.configured ? "ghp_***  (gesetzt)" : "ghp_... (Personal Access Token)"}
              className={inputCls} />
          </div>
          <SaveBtn saving={saving === "gh"} onClick={saveGithub} disabled={!ghToken.trim()} />
        </div>

        <div className="rounded-lg border bg-muted/10 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            <span className="text-sm font-medium">Gitea</span>
            {!!giteaCfg?.token && <CheckCircle className="h-4 w-4 text-green-500" />}
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1">
              <label className={labelCls}>URL</label>
              <input value={giteaUrl} onChange={e => setGiteaUrl(e.target.value)} placeholder="http://localhost:3000" className={inputCls} />
            </div>
            <div className="space-y-1">
              <label className={labelCls}>API-Token</label>
              <input type="password" value={giteaToken} onChange={e => setGiteaToken(e.target.value)}
                placeholder={!!giteaCfg?.token ? "(gesetzt)" : "Token"} className={inputCls} />
            </div>
            <div className="space-y-1">
              <label className={labelCls}>Organisation</label>
              <input value={giteaOrg} onChange={e => setGiteaOrg(e.target.value)} placeholder="optional" className={inputCls} />
            </div>
          </div>
          <SaveBtn saving={saving === "gitea"} onClick={saveGitea} />
        </div>

        {msg && <p className="text-sm text-green-500">{msg}</p>}
      </div>
    </Section>
  );
}

/* ── KAS / Mail Provider Section ──────────────────────────────── */

function KasSection() {
  const [cfg, setCfg] = useState<{ configured?: boolean; login?: string; default_domain?: string } | null>(null);
  const [login, setLogin] = useState("");
  const [pw, setPw] = useState("");
  const [domain, setDomain] = useState("");
  const [smtp, setSmtp] = useState("");
  const [port, setPort] = useState("587");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.getKas().then(r => {
      setCfg(r as any);
      setLogin((r as any).login || "");
      setDomain((r as any).default_domain || "");
      setSmtp((r as any).smtp_host || "");
      setPort(String((r as any).smtp_port || 587));
    }).catch(() => setCfg({ configured: false }));
  }, []);

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.putKas({ login, password: pw, default_domain: domain, smtp_host: smtp, smtp_port: Number(port) || 587 });
      setPw("");
      const r = await api.getKas();
      setCfg(r as any);
      setMsg("KAS gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  return (
    <Section title="Mail-Provider (KAS / All-Inkl)" icon={Mail} configured={cfg ? !!cfg.configured : null}>
      <div className="space-y-3 mt-2">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <label className={labelCls}>KAS-Login</label>
            <input value={login} onChange={e => setLogin(e.target.value)} placeholder="w012345e" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>KAS-Passwort</label>
            <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="••••••••" className={inputCls} />
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="space-y-1">
            <label className={labelCls}>Standard-Domain</label>
            <input value={domain} onChange={e => setDomain(e.target.value)} placeholder="deine-domain.de" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>SMTP-Host</label>
            <input value={smtp} onChange={e => setSmtp(e.target.value)} placeholder="dd12345.kasserver.com" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>Port</label>
            <input value={port} onChange={e => setPort(e.target.value)} placeholder="587" className={inputCls} />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <SaveBtn saving={saving} onClick={save} disabled={!login || !pw} />
          {msg && <span className="text-sm text-green-500">{msg}</span>}
        </div>
      </div>
    </Section>
  );
}

/* ── VPN Section ──────────────────────────────────────────────── */

function VpnSection() {
  const [status, setStatus] = useState<{ configured?: boolean; connected?: boolean; tailscale_ip?: string; mode?: string } | null>(null);

  useEffect(() => {
    api.vpnStatus().then(r => setStatus(r as any)).catch(() => setStatus({ configured: false }));
  }, []);

  const configured = status ? !!status.configured : null;

  return (
    <Section title="VPN / Federation" icon={Network} configured={configured}
      badge={status?.connected ? status.tailscale_ip || "Verbunden" : undefined}>
      <div className="mt-2 space-y-2">
        {status?.connected ? (
          <div className="flex items-center gap-2 text-sm">
            <CheckCircle className="h-4 w-4 text-green-500" />
            <span>Tailscale verbunden — {status.tailscale_ip}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertTriangle className="h-4 w-4" />
            <span>VPN nicht verbunden. Konfiguration unter Einstellungen → VPN oder via Federation-Seite.</span>
          </div>
        )}
      </div>
    </Section>
  );
}

/* ── AgentLink Section ────────────────────────────────────────── */

function AgentLinkSection() {
  const [cfg, setCfg] = useState<{ base_url?: string; ws_url?: string; enabled?: boolean; healthy?: boolean } | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [wsUrl, setWsUrl] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api.get<any>("/admin/agentlink/config").then(r => {
      setCfg(r);
      setBaseUrl(r.base_url || "http://localhost:8000");
      setWsUrl(r.ws_url || "ws://localhost:8000");
      setEnabled(r.enabled ?? true);
    }).catch(() => setCfg({ enabled: false }));
  }, []);

  useEffect(() => { load(); }, [load]);

  const configured = cfg ? !!cfg.enabled : null;

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.put("/admin/agentlink/config", { base_url: baseUrl, ws_url: wsUrl, enabled });
      load();
      setMsg("Gespeichert");
      setTimeout(() => setMsg(""), 3000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  return (
    <Section title="AgentLink" icon={MessageSquare} configured={configured}
      badge={cfg?.healthy ? "Healthy" : cfg?.enabled ? "Nicht erreichbar" : "Deaktiviert"}>
      <div className="space-y-4 mt-2">
        {cfg?.healthy === false && cfg?.enabled && (
          <div className="flex items-center gap-2 rounded-lg border border-orange-500/30 bg-orange-500/5 p-3 text-sm text-orange-500">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            AgentLink nicht erreichbar — Service läuft evtl. nicht. Fix unter System → Doctor.
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <label className={labelCls}>Base URL (HTTP)</label>
            <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
              placeholder="http://localhost:8000" className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>WebSocket URL</label>
            <input value={wsUrl} onChange={e => setWsUrl(e.target.value)}
              placeholder="ws://localhost:8000" className={inputCls} />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} className="rounded" />
          AgentLink aktiviert
        </label>
        <div className="flex items-center gap-3">
          <SaveBtn saving={saving} onClick={save} />
          {msg && <span className="text-sm text-green-500">{msg}</span>}
        </div>
        <p className={hintCls}>AgentLink ist das State/Handoff-System für Agent-zu-Agent-Kommunikation. Config: /etc/hydrahive/agentlink.json</p>
      </div>
    </Section>
  );
}

/* ── Voice Section ────────────────────────────────────────────── */

function VoiceSection() {
  const [status, setStatus] = useState<{ installed?: boolean; stt?: { available: boolean }; tts?: { available: boolean } } | null>(null);

  useEffect(() => {
    api.voiceStatus().then(r => setStatus(r)).catch(() => setStatus({ installed: false }));
  }, []);

  const configured = status ? !!(status.stt?.available && status.tts?.available) : null;

  return (
    <Section title="Voice (STT / TTS)" icon={Mic} configured={configured}>
      <div className="mt-2 space-y-2 text-sm">
        <div className="flex items-center gap-2">
          {status?.stt?.available
            ? <><CheckCircle className="h-4 w-4 text-green-500" /> STT (Speech-to-Text) verfügbar</>
            : <><XCircle className="h-4 w-4 text-zinc-500" /> STT nicht verfügbar</>}
        </div>
        <div className="flex items-center gap-2">
          {status?.tts?.available
            ? <><CheckCircle className="h-4 w-4 text-green-500" /> TTS (Text-to-Speech) verfügbar</>
            : <><XCircle className="h-4 w-4 text-zinc-500" /> TTS nicht verfügbar</>}
        </div>
        <p className={hintCls}>Voice-Dienste werden über Docker-Container bereitgestellt (faster-whisper, Piper). Konfiguration unter Voice-Seite.</p>
      </div>
    </Section>
  );
}

/* ── System Time Section ──────────────────────────────────────── */

const COMMON_TIMEZONES = [
  "Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London",
  "Europe/Paris", "Europe/Amsterdam", "Europe/Madrid", "Europe/Rome",
  "Europe/Warsaw", "Europe/Prague", "Europe/Istanbul",
  "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
  "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata",
  "Australia/Sydney", "UTC",
];

function SystemTimeSection() {
  const [info, setInfo] = useState<{
    server_time?: string; utc_time?: string; timezone?: string;
    timezone_abbr?: string; utc_offset_hours?: number;
  } | null>(null);
  const [newTz, setNewTz] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api.get<any>("/admin/system/time").then(r => {
      setInfo(r);
      setNewTz(r.timezone || "");
    }).catch(() => setInfo(null));
  }, []);

  useEffect(() => { load(); }, [load]);

  const isUtc = info?.timezone === "Etc/UTC" || info?.timezone === "UTC";
  const configured = info ? !isUtc : null;

  async function save() {
    setSaving(true); setMsg("");
    try {
      await api.put("/admin/system/timezone", { timezone: newTz });
      load();
      setMsg("Zeitzone gesetzt — wirkt sofort");
      setTimeout(() => setMsg(""), 4000);
    } catch (e) { setMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(false); }
  }

  return (
    <Section title="Systemzeit" icon={Clock} configured={configured}
      badge={info ? `${info.timezone_abbr} (UTC${(info.utc_offset_hours ?? 0) >= 0 ? "+" : ""}${info.utc_offset_hours})` : undefined}>
      <div className="space-y-4 mt-2">
        {info && (
          <div className="grid gap-3 md:grid-cols-3 text-sm">
            <div>
              <span className="text-muted-foreground">Serverzeit:</span>
              <p className="font-mono">{info.server_time}</p>
            </div>
            <div>
              <span className="text-muted-foreground">UTC:</span>
              <p className="font-mono">{info.utc_time}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Zeitzone:</span>
              <p className="font-mono">{info.timezone}</p>
            </div>
          </div>
        )}
        {isUtc && (
          <div className="flex items-center gap-2 rounded-lg border border-orange-500/30 bg-orange-500/5 p-3 text-sm text-orange-500">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            Zeitzone ist UTC — zeitgesteuerte Aktionen (Butler, Schedules) laufen zur falschen Uhrzeit!
          </div>
        )}
        <div className="flex items-center gap-3">
          <select value={newTz} onChange={e => setNewTz(e.target.value)}
            className="rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
            {!COMMON_TIMEZONES.includes(info?.timezone || "") && info?.timezone && (
              <option value={info.timezone}>{info.timezone}</option>
            )}
            {COMMON_TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
          </select>
          <SaveBtn saving={saving} onClick={save} disabled={!newTz || newTz === info?.timezone} />
          {msg && <span className="text-sm text-green-500">{msg}</span>}
        </div>
      </div>
    </Section>
  );
}

/* ── Main Page ────────────────────────────────────────────────── */

export function ConfigHubPage() {
  const { isAdmin } = useAuth();

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-4">
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
            <Key className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Setup</h1>
            <p className="text-sm text-muted-foreground">Alles was du für den Betrieb brauchst — an einem Ort.</p>
          </div>
        </div>
      </div>

      {isAdmin && <SystemTimeSection />}
      <LlmSection />
      <PlatformsSection />
      {isAdmin && <GitSection />}
      {isAdmin && <KasSection />}
      {isAdmin && <VpnSection />}
      {isAdmin && <AgentLinkSection />}
      <VoiceSection />
    </div>
  );
}
