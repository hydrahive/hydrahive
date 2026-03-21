import { useEffect, useRef, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, ExternalLink, Save, Cpu, Download } from "lucide-react";
import { api } from "@/lib/api";

interface OllamaModel { name: string; size_gb: number; modified: string; }
interface ClaudeStatus {
  configured: boolean;
  token_age_days: number | null;
  remaining_days: number | null;
  warning: string | null;
  ttl_days: number;
}
interface OAuthStatus {
  configured: boolean;
  account_id?: string | null;
  email?: string | null;
  project_id?: string | null;
  models?: string[];
}
interface OAuthFlow {
  provider: string;
  state: string;
  authUrl: string;
  step: 1 | 2;
  input: string;
  error: string;
  loading: boolean;
}

function OAuthCard({
  id, label, description, configured, statusExtra, modelPrefix, models, onStartOAuth,
}: {
  id: string; label: string; description: string;
  configured: boolean; statusExtra?: React.ReactNode;
  modelPrefix?: string; models?: string[];
  onStartOAuth: (provider: string) => void;
}) {
  return (
    <div className="bg-card border rounded-lg p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <h2 className="font-medium text-sm">{label}</h2>
            {configured
              ? <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3.5 w-3.5"/>Aktiv</span>
              : <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle className="h-3.5 w-3.5"/>Nicht konfiguriert</span>
            }
          </div>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <button onClick={() => onStartOAuth(id)}
          className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
          <ExternalLink className="h-3.5 w-3.5"/>
          {configured ? "Erneuern" : "OAuth verbinden"}
        </button>
      </div>
      {statusExtra}
      {configured && models && models.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Verfügbare Modelle</p>
          <div className="flex flex-wrap gap-1.5">
            {models.map(m => (
              <span key={m} className="px-2 py-0.5 text-xs font-mono bg-muted rounded border">{m}</span>
            ))}
          </div>
          {modelPrefix && (
            <p className="text-xs text-muted-foreground">In agent.yaml: <code className="font-mono">{modelPrefix}/{models[0]}</code></p>
          )}
        </div>
      )}
    </div>
  );
}

function OAuthFlowPanel({
  flow,
  onClose,
  onNextStep,
  onInputChange,
  onExchange,
}: {
  flow: OAuthFlow;
  onClose: () => void;
  onNextStep: () => void;
  onInputChange: (val: string) => void;
  onExchange: () => void;
}) {
  const isAnthropic = flow.provider === "anthropic";

  const step2Hint = isAnthropic
    ? "Die Seite zeigt einen Code — kopiere den gesamten Text (Format: code#state) und füge ihn unten ein."
    : flow.provider === "openai_codex"
    ? "Der Browser zeigt einen Verbindungsfehler — das ist normal! Kopiere die gesamte URL aus der Adresszeile (http://localhost:1455/auth/callback?code=...&state=...) und füge sie unten ein."
    : "Der Browser zeigt einen Verbindungsfehler — das ist normal! Kopiere die gesamte URL aus der Adresszeile (http://localhost:51121/oauth-callback?code=...&state=...) und füge sie unten ein.";

  const inputPlaceholder = isAnthropic
    ? "4/0AX4XfWi...#verifier..."
    : "http://localhost:.../callback?code=...&state=...";

  return (
    <div className="bg-card border rounded-lg p-5 space-y-4 ring-2 ring-primary/30">
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-sm">OAuth verbinden — Schritt {flow.step} von 2</h3>
        <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">Abbrechen</button>
      </div>

      {flow.step === 1 && (
        <div className="space-y-3">
          <div className="bg-muted/50 rounded-md p-3 text-xs space-y-1">
            <p className="font-medium text-foreground">Schritt 1: Im Browser einloggen</p>
            <p>Öffne den Link und logge dich ein. Du wirst danach auf localhost weitergeleitet — das ist normal.</p>
          </div>
          <div className="flex gap-2">
            <a href={flow.authUrl} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
              <ExternalLink className="h-3.5 w-3.5"/>Login-Seite öffnen
            </a>
            <button onClick={onNextStep}
              className="px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors">
              Weiter → Code eingeben
            </button>
          </div>
        </div>
      )}

      {flow.step === 2 && (
        <div className="space-y-3">
          <div className="bg-muted/50 rounded-md p-3 text-xs">
            <p className="font-medium text-foreground mb-1">Schritt 2: Code einfügen</p>
            <p>{step2Hint}</p>
          </div>
          <textarea
            value={flow.input}
            onChange={e => onInputChange(e.target.value)}
            placeholder={inputPlaceholder}
            rows={3}
            className="w-full px-3 py-2 text-xs font-mono border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
          />
          {flow.error && <p className="text-xs text-destructive">{flow.error}</p>}
          <button
            onClick={onExchange}
            disabled={flow.loading || !flow.input.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5"/>
            {flow.loading ? "Verbinde..." : "Verbinden"}
          </button>
        </div>
      )}
    </div>
  );
}

export function LlmConfigPage() {
  const [providerStatus, setProviderStatus] = useState<Record<string,{has_key:boolean}>>({});
  const [ollamaModels,   setOllamaModels]   = useState<OllamaModel[]>([]);
  const [ollamaOk,       setOllamaOk]       = useState<boolean|null>(null);
  const [loading,        setLoading]        = useState(true);
  const [keys,           setKeys]           = useState<Record<string,string>>({});
  const [saving,         setSaving]         = useState<string|null>(null);
  const [saved,          setSaved]          = useState<string|null>(null);
  const [pullModel,      setPullModel]      = useState("");
  const [pulling,        setPulling]        = useState(false);
  const [pullMsg,        setPullMsg]        = useState("");
  const [refreshing,     setRefreshing]     = useState(false);
  const [claudeStatus,   setClaudeStatus]   = useState<ClaudeStatus|null>(null);
  const [codexStatus,    setCodexStatus]    = useState<OAuthStatus|null>(null);
  const [googleStatus,   setGoogleStatus]   = useState<OAuthStatus|null>(null);
  const [oauthFlow,      setOauthFlow]      = useState<OAuthFlow|null>(null);

  // Ref so exchange handler always sees current flow state
  const oauthFlowRef = useRef<OAuthFlow | null>(null);
  oauthFlowRef.current = oauthFlow;

  async function load() {
    try {
      const [cfg, ollama, claudeSt, codexSt, googleSt] = await Promise.allSettled([
        api.get<{providers:Record<string,{has_key:boolean}>}>("/llm/config"),
        api.get<{available:boolean;models:OllamaModel[]}>("/llm/ollama/models"),
        api.claudeTokenStatus(),
        api.openaiCodexStatus(),
        api.googleAntigravityStatus(),
      ]);
      if (claudeSt.status  === "fulfilled") setClaudeStatus(claudeSt.value);
      if (codexSt.status   === "fulfilled") setCodexStatus(codexSt.value);
      if (googleSt.status  === "fulfilled") setGoogleStatus(googleSt.value);
      if (cfg.status       === "fulfilled") setProviderStatus(cfg.value.providers ?? {});
      if (ollama.status    === "fulfilled") {
        setOllamaOk(ollama.value.available);
        setOllamaModels(ollama.value.models ?? []);
      }
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

  async function startOAuth(provider: string) {
    try {
      const { auth_url, state } = await api.startOAuth(provider);
      setOauthFlow({ provider, state, authUrl: auth_url, step: 1, input: "", error: "", loading: false });
    } catch(e) { alert(e instanceof Error ? e.message : "Fehler beim Starten"); }
  }

  async function handleExchange() {
    const flow = oauthFlowRef.current;
    if (!flow) return;
    setOauthFlow(f => f ? { ...f, loading: true, error: "" } : f);
    try {
      const body: Record<string, string> = {};
      const val = flow.input.trim();
      if (flow.provider === "anthropic") {
        body.code_and_state = val;
      } else if (val.startsWith("http")) {
        body.redirect_url = val;
      } else {
        body.code  = val;
        body.state = flow.state;
      }
      await api.exchangeOAuth(flow.provider, body);
      setOauthFlow(null);
      await load();
    } catch(err) {
      setOauthFlow(f => f ? { ...f, loading: false, error: err instanceof Error ? err.message : "Fehler" } : f);
    }
  }

  async function saveKey(providerId: string) {
    if (!keys[providerId]?.trim()) return;
    setSaving(providerId);
    try {
      await api.put(`/llm/config/${providerId}`, { provider: providerId, api_key: keys[providerId], enabled: true });
      setSaved(providerId);
      setKeys(k => ({ ...k, [providerId]: "" }));
      setTimeout(() => setSaved(null), 3000);
      await load();
    } catch(e) { alert(e instanceof Error ? e.message : "Fehler"); }
    finally { setSaving(null); }
  }

  async function pullOllamaModel() {
    if (!pullModel.trim()) return;
    setPulling(true); setPullMsg("Starte Download...");
    try {
      await api.post("/llm/ollama/pull", { model: pullModel });
      setPullMsg(`Modell "${pullModel}" erfolgreich geladen`);
      setPullModel("");
      await load();
    } catch(e) { setPullMsg(e instanceof Error ? e.message : "Fehler"); }
    finally { setPulling(false); }
  }

  if (loading) return <div className="p-6"><div className="animate-pulse space-y-4">{[1,2,3].map(i=><div key={i} className="h-32 bg-muted rounded-lg"/>)}</div></div>;

  const claudeWarning = claudeStatus?.warning;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">LLM-Konfiguration</h1>
          <p className="text-sm text-muted-foreground">API-Keys, OAuth-Abos und lokale Modelle verwalten</p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing?"animate-spin":""}`}/>Aktualisieren
        </button>
      </div>

      {/* OAuth Flow Panel */}
      {oauthFlow && (
        <OAuthFlowPanel
          flow={oauthFlow}
          onClose={() => setOauthFlow(null)}
          onNextStep={() => setOauthFlow(f => f ? { ...f, step: 2 } : f)}
          onInputChange={val => setOauthFlow(f => f ? { ...f, input: val, error: "" } : f)}
          onExchange={handleExchange}
        />
      )}

      {/* Claude Max OAuth */}
      <OAuthCard
        id="anthropic"
        label="Claude Max (Subscription)"
        description="Claude Max/Pro via OAuth — claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5. Kein API-Key nötig."
        configured={!!claudeStatus?.configured}
        statusExtra={claudeStatus?.configured ? (
          <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-md ${
            claudeWarning === "expired"            ? "bg-destructive/10 text-destructive"
            : claudeWarning?.startsWith("expires_soon") ? "bg-orange-50 text-orange-700 border border-orange-200"
            : claudeWarning?.startsWith("expires_in")   ? "bg-yellow-50 text-yellow-700 border border-yellow-200"
            : "bg-green-50 text-green-700 border border-green-200"
          }`}>
            {claudeWarning === "expired"
              ? "⚠ Token abgelaufen — bitte erneuern"
              : claudeWarning
                ? `⚠ Token läuft in ${claudeStatus?.remaining_days?.toFixed(0)} Tagen ab`
                : `✓ Token aktiv — noch ${claudeStatus?.remaining_days?.toFixed(0)} Tage gültig`}
          </div>
        ) : undefined}
        models={claudeStatus?.configured ? ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"] : undefined}
        onStartOAuth={startOAuth}
      />

      {/* OpenAI Codex OAuth */}
      <OAuthCard
        id="openai_codex"
        label="OpenAI Codex (ChatGPT Plus/Pro)"
        description="ChatGPT Plus/Pro via OAuth — gpt-5.2, gpt-5.1-codex-max und weitere. Kein API-Key nötig."
        configured={!!codexStatus?.configured}
        statusExtra={codexStatus?.configured ? (
          <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-md bg-green-50 text-green-700 border border-green-200">
            <CheckCircle className="h-3.5 w-3.5"/>
            Verbunden — <code className="font-mono ml-1">{codexStatus.account_id?.slice(0, 20)}…</code>
          </div>
        ) : undefined}
        models={codexStatus?.configured ? codexStatus.models : undefined}
        modelPrefix="openai-codex"
        onStartOAuth={startOAuth}
      />

      {/* Google Antigravity OAuth */}
      <OAuthCard
        id="google_antigravity"
        label="Google Antigravity (Gemini 3)"
        description="Gemini 3 Flash/Pro via Google Cloud Code Assist OAuth. Kein API-Key nötig."
        configured={!!googleStatus?.configured}
        statusExtra={googleStatus?.configured ? (
          <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-md bg-green-50 text-green-700 border border-green-200">
            <CheckCircle className="h-3.5 w-3.5"/>
            Verbunden — {googleStatus.email} · Projekt: {googleStatus.project_id}
          </div>
        ) : undefined}
        models={googleStatus?.configured ? googleStatus.models : undefined}
        modelPrefix="google-antigravity"
        onStartOAuth={startOAuth}
      />

      {/* OpenAI API Key */}
      <div className="bg-card border rounded-lg p-5 space-y-4">
        <div className="flex items-start justify-between">
          <div className="space-y-0.5">
            <div className="flex items-center gap-2">
              <h2 className="font-medium text-sm">OpenAI API</h2>
              {providerStatus["openai"]?.has_key
                ? <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3.5 w-3.5"/>Aktiv</span>
                : <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle className="h-3.5 w-3.5"/>Nicht konfiguriert</span>
              }
              {saved === "openai" && <span className="text-xs text-green-600">Gespeichert ✓</span>}
            </div>
            <p className="text-xs text-muted-foreground">API-Key von platform.openai.com — für gpt-4o etc.</p>
          </div>
        </div>
        <div className="flex gap-2">
          <input
            type="password"
            value={keys["openai"] ?? ""}
            onChange={e => setKeys(k => ({...k, openai: e.target.value}))}
            placeholder={providerStatus["openai"]?.has_key ? "••••••••••••••• (gesetzt)" : "sk-..."}
            className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <button onClick={() => saveKey("openai")}
            disabled={saving === "openai" || !keys["openai"]?.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
            <Save className="h-3.5 w-3.5"/>
            {saving === "openai" ? "Speichern..." : "Speichern"}
          </button>
        </div>
        <p className="text-xs text-muted-foreground">Von https://platform.openai.com/api-keys</p>
      </div>

      {/* Ollama */}
      <div className="bg-card border rounded-lg p-5 space-y-4">
        <div className="flex items-start justify-between">
          <div className="space-y-0.5">
            <div className="flex items-center gap-2">
              <h2 className="font-medium text-sm">Ollama (lokal)</h2>
              {ollamaOk === true
                ? <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3.5 w-3.5"/>Aktiv</span>
                : <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle className="h-3.5 w-3.5"/>Nicht erreichbar</span>
              }
            </div>
            <p className="text-xs text-muted-foreground">Lokale Modelle auf diesem Server. Kein API-Key nötig.</p>
          </div>
        </div>
        {ollamaOk === false && <p className="text-sm text-destructive">Ollama nicht erreichbar auf Port 11434</p>}
        {ollamaOk && ollamaModels.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
              <Cpu className="h-3.5 w-3.5"/>Installierte Modelle
            </p>
            <div className="space-y-1">
              {ollamaModels.map(m => (
                <div key={m.name} className="flex items-center justify-between text-sm py-1.5 px-3 bg-muted/50 rounded">
                  <span className="font-mono text-xs">{m.name}</span>
                  <span className="text-xs text-muted-foreground">{m.size_gb} GB</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {ollamaOk && (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Modell laden</p>
            <div className="flex gap-2">
              <input value={pullModel} onChange={e=>setPullModel(e.target.value)}
                placeholder="z.B. llama3.1:8b"
                className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              <button onClick={pullOllamaModel} disabled={pulling || !pullModel.trim()}
                className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
                <Download className="h-3.5 w-3.5"/>
                {pulling ? "Lädt..." : "Laden"}
              </button>
            </div>
            {pullMsg && <p className="text-xs text-muted-foreground">{pullMsg}</p>}
          </div>
        )}
        <p className="text-xs text-muted-foreground">Läuft auf http://127.0.0.1:11434</p>
      </div>

      <div className="bg-muted/30 border rounded-lg p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-medium text-foreground">Wie werden Keys verwendet?</p>
        <p>API-Keys in <code>/etc/octopos/llm_config.json</code>, OAuth-Tokens in <code>/etc/octopos/*.json</code>. Jeder Agent wählt sein Modell in <code>agent.yaml</code>.</p>
      </div>
    </div>
  );
}
