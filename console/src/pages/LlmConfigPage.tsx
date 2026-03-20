import { useEffect, useState } from "react";
import { RefreshCw, CheckCircle, XCircle, Eye, EyeOff, Save, Cpu, Download } from "lucide-react";
import { api } from "@/lib/api";

interface OllamaModel { name: string; size_gb: number; modified: string; }

const PROVIDERS = [
  {
    id: "claude_max",
    label: "Claude Max (Subscription)",
    description: "Nutzt dein Claude Max Abo via Session-Token. Kein API-Key nötig.",
    placeholder: "sk-ant-...",
    hint: "Terminal: claude setup-token → Token kopieren und hier einfügen",
    docsUrl: "",
    noKey: false,
  },
  {
    id: "openai",
    label: "OpenAI / ChatGPT",
    description: "API-Key von platform.openai.com",
    placeholder: "sk-...",
    hint: "Von https://platform.openai.com/api-keys",
    docsUrl: "https://platform.openai.com/api-keys",
    noKey: false,
  },
  {
    id: "ollama",
    label: "Ollama (lokal)",
    description: "Lokale Modelle auf diesem Server. Kein API-Key nötig.",
    placeholder: "",
    hint: "Läuft auf http://127.0.0.1:11434",
    docsUrl: "",
    noKey: true,
  },
];

export function LlmConfigPage() {
  const [providerStatus, setProviderStatus] = useState<Record<string,{has_key:boolean}>>({});
  const [ollamaModels,   setOllamaModels]   = useState<OllamaModel[]>([]);
  const [ollamaOk,       setOllamaOk]       = useState<boolean|null>(null);
  const [loading,        setLoading]         = useState(true);
  const [keys,           setKeys]            = useState<Record<string,string>>({});
  const [show,           setShow]            = useState<Record<string,boolean>>({});
  const [saving,         setSaving]          = useState<string|null>(null);
  const [saved,          setSaved]           = useState<string|null>(null);
  const [pullModel,      setPullModel]       = useState("");
  const [pulling,        setPulling]         = useState(false);
  const [pullMsg,        setPullMsg]         = useState("");
  const [refreshing,     setRefreshing]      = useState(false);

  async function load() {
    try {
      const [cfg, ollama] = await Promise.allSettled([
        api.get<{providers:Record<string,{has_key:boolean}>}>("/llm/config"),
        api.get<{available:boolean;models:OllamaModel[]}>("/llm/ollama/models"),
      ]);
      if (cfg.status === "fulfilled")    setProviderStatus(cfg.value.providers ?? {});
      if (ollama.status === "fulfilled") {
        setOllamaOk(ollama.value.available);
        setOllamaModels(ollama.value.models ?? []);
      }
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

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

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">LLM-Konfiguration</h1>
          <p className="text-sm text-muted-foreground">API-Keys und lokale Modelle verwalten</p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing?"animate-spin":""}`}/>Aktualisieren
        </button>
      </div>

      {/* Provider Cards */}
      <div className="space-y-4">
        {PROVIDERS.map(p => {
          const status  = providerStatus[p.id];
          const hasKey  = p.noKey ? (p.id === "ollama" && ollamaOk === true) : status?.has_key;
          const isSaved = saved === p.id;

          return (
            <div key={p.id} className="bg-card border rounded-lg p-5 space-y-4">
              <div className="flex items-start justify-between">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <h2 className="font-medium text-sm">{p.label}</h2>
                    {hasKey
                      ? <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3.5 w-3.5"/>Aktiv</span>
                      : <span className="flex items-center gap-1 text-xs text-muted-foreground"><XCircle className="h-3.5 w-3.5"/>Nicht konfiguriert</span>
                    }
                    {isSaved && <span className="text-xs text-green-600">Gespeichert ✓</span>}
                  </div>
                  <p className="text-xs text-muted-foreground">{p.description}</p>
                </div>
              </div>

              {/* Claude Max — Token-Setup Anleitung */}
              {p.id === "claude_max" && (
                <div className="bg-muted/50 rounded-md p-3 text-xs space-y-1.5 font-mono">
                  <p className="font-sans font-medium text-sm text-foreground mb-2">Setup-Anleitung</p>
                  <p>1. Im Terminal auf diesem Server:</p>
                  <p className="bg-background rounded px-2 py-1 text-primary">claude setup-token</p>
                  <p>2. Den angezeigten Token kopieren und unten einfügen.</p>
                  <p className="font-sans text-muted-foreground">Das Token läuft ab wenn deine Claude-Session abläuft. Dann einfach wiederholen.</p>
                </div>
              )}

              {/* Key-Eingabe */}
              {!p.noKey && (
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <input
                      type={show[p.id] ? "text" : "password"}
                      value={keys[p.id] ?? ""}
                      onChange={e => setKeys(k => ({...k, [p.id]: e.target.value}))}
                      placeholder={hasKey ? "••••••••••••••• (gesetzt)" : p.placeholder}
                      className="w-full px-3 py-2 pr-10 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                    <button type="button" onClick={() => setShow(s => ({...s, [p.id]: !s[p.id]}))}
                      className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground">
                      {show[p.id] ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                    </button>
                  </div>
                  <button
                    onClick={() => saveKey(p.id)}
                    disabled={saving === p.id || !keys[p.id]?.trim()}
                    className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors">
                    <Save className="h-3.5 w-3.5"/>
                    {saving === p.id ? "Speichern..." : "Speichern"}
                  </button>
                </div>
              )}

              {p.hint && <p className="text-xs text-muted-foreground">{p.hint}</p>}

              {/* Ollama Modell-Liste */}
              {p.id === "ollama" && (
                <div className="space-y-3">
                  {ollamaOk === false && (
                    <p className="text-sm text-destructive">Ollama nicht erreichbar auf Port 11434</p>
                  )}
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
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="bg-muted/30 border rounded-lg p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-medium text-foreground">Wie werden Keys verwendet?</p>
        <p>Keys werden in <code>/etc/octopos/llm_config.json</code> gespeichert und als Umgebungsvariablen an litellm übergeben. Jeder Agent kann in seiner <code>agent.yaml</code> ein anderes Modell und damit einen anderen Provider wählen.</p>
      </div>
    </div>
  );
}
