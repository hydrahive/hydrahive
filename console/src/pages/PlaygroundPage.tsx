/**
 * PlaygroundPage — API Playground / Developer Tools (#376)
 *
 * Ermöglicht API-Endpoints direkt in der Console zu testen.
 * Ähnlich wie Swagger UI, aber eingebaut.
 */
import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Play, Copy, Check, ChevronDown, ChevronRight, Code, Send, Clock } from "lucide-react";

// ── Endpoint Definitions ─────────────────────────────────────────────────────

interface EndpointDef {
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
  description: string;
  category: string;
  params?: { name: string; type: string; required?: boolean; description: string }[];
  body?: { name: string; type: string; required?: boolean; description: string }[];
}

const ENDPOINTS: EndpointDef[] = [
  // System
  { method: "GET", path: "/api/status", description: "System-Status und Capabilities", category: "System" },
  { method: "GET", path: "/api/admin/session-metrics", description: "Kontext- und Turn-Metriken", category: "System" },
  { method: "GET", path: "/api/admin/turn-journal/{project_id}", description: "Turn Journal Events", category: "System",
    params: [{ name: "project_id", type: "string", required: true, description: "Projekt-ID" }] },
  // Agents
  { method: "GET", path: "/api/agents", description: "Alle Agenten auflisten", category: "Agents" },
  { method: "GET", path: "/api/agents/{agent_id}", description: "Agent-Details abrufen", category: "Agents",
    params: [{ name: "agent_id", type: "string", required: true, description: "Agent-ID" }] },
  { method: "GET", path: "/api/agents/{agent_id}/session/history", description: "Chat-History eines Agenten", category: "Agents",
    params: [{ name: "agent_id", type: "string", required: true, description: "Agent-ID" }] },
  // Projects
  { method: "GET", path: "/api/projects", description: "Alle Projekte auflisten", category: "Projects" },
  { method: "GET", path: "/api/projects/{project_id}", description: "Projekt-Details", category: "Projects",
    params: [{ name: "project_id", type: "string", required: true, description: "Projekt-ID" }] },
  { method: "POST", path: "/api/projects/{project_id}/message", description: "Nachricht an Projekt senden", category: "Projects",
    params: [{ name: "project_id", type: "string", required: true, description: "Projekt-ID" }],
    body: [{ name: "content", type: "string", required: true, description: "Nachrichtentext" }] },
  // LLM
  { method: "GET", path: "/api/llm/models", description: "Verfügbare LLM-Modelle", category: "LLM" },
  { method: "GET", path: "/api/llm/config", description: "LLM-Konfiguration", category: "LLM" },
  // Extensions
  { method: "GET", path: "/api/admin/extensions", description: "Installierte Extensions", category: "Extensions" },
  // Users
  { method: "GET", path: "/api/users", description: "Alle User auflisten", category: "Users" },
];

// ── Component ────────────────────────────────────────────────────────────────

export function PlaygroundPage() {
  const { t } = useTranslation();
  const [selectedEndpoint, setSelectedEndpoint] = useState<EndpointDef | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [bodyValues, setBodyValues] = useState<Record<string, string>>({});
  const [response, setResponse] = useState<{ status: number; body: string; time: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set(["System", "Agents", "Projects"]));

  const categories = [...new Set(ENDPOINTS.map(e => e.category))];

  const toggleCat = (cat: string) => {
    const next = new Set(expandedCats);
    next.has(cat) ? next.delete(cat) : next.add(cat);
    setExpandedCats(next);
  };

  const selectEndpoint = useCallback((ep: EndpointDef) => {
    setSelectedEndpoint(ep);
    setParamValues({});
    setBodyValues({});
    setResponse(null);
  }, []);

  const buildUrl = useCallback(() => {
    if (!selectedEndpoint) return "";
    let url = selectedEndpoint.path;
    for (const p of selectedEndpoint.params || []) {
      url = url.replace(`{${p.name}}`, paramValues[p.name] || `{${p.name}}`);
    }
    return url;
  }, [selectedEndpoint, paramValues]);

  const sendRequest = useCallback(async () => {
    if (!selectedEndpoint) return;
    setLoading(true);
    const url = buildUrl();
    const start = performance.now();
    try {
      const token = localStorage.getItem("hh_token");
      const opts: RequestInit = {
        method: selectedEndpoint.method,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      };
      if (selectedEndpoint.body && selectedEndpoint.method !== "GET") {
        const body: Record<string, unknown> = {};
        for (const b of selectedEndpoint.body) {
          if (bodyValues[b.name]) body[b.name] = bodyValues[b.name];
        }
        opts.body = JSON.stringify(body);
      }
      const resp = await fetch(url, opts);
      const text = await resp.text();
      let formatted = text;
      try { formatted = JSON.stringify(JSON.parse(text), null, 2); } catch {}
      setResponse({ status: resp.status, body: formatted, time: Math.round(performance.now() - start) });
    } catch (e) {
      setResponse({ status: 0, body: String(e), time: Math.round(performance.now() - start) });
    } finally { setLoading(false); }
  }, [selectedEndpoint, buildUrl, bodyValues]);

  const copyCurl = useCallback(() => {
    if (!selectedEndpoint) return;
    const url = `${window.location.origin}${buildUrl()}`;
    const token = localStorage.getItem("hh_token");
    let cmd = `curl -s`;
    if (token) cmd += ` -H "Authorization: Bearer ${token}"`;
    if (selectedEndpoint.method !== "GET") {
      cmd += ` -X ${selectedEndpoint.method}`;
      if (selectedEndpoint.body) {
        const body: Record<string, unknown> = {};
        for (const b of selectedEndpoint.body) {
          if (bodyValues[b.name]) body[b.name] = bodyValues[b.name];
        }
        cmd += ` -H "Content-Type: application/json" -d '${JSON.stringify(body)}'`;
      }
    }
    cmd += ` "${url}"`;
    navigator.clipboard.writeText(cmd);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [selectedEndpoint, buildUrl, bodyValues]);

  const methodColor: Record<string, string> = {
    GET: "text-green-500", POST: "text-blue-500",
    PUT: "text-yellow-500", DELETE: "text-red-500",
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 pt-6 pb-4 border-b border-border flex-shrink-0">
        <h1 className="text-2xl font-bold tracking-tight mb-1">
          <Code className="inline h-6 w-6 mr-2 text-primary" />
          API Playground
        </h1>
        <p className="text-xs text-muted-foreground">API-Endpoints testen und erkunden</p>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar: Endpoints */}
        <div className="w-72 border-r border-border overflow-y-auto p-3 flex-shrink-0">
          {categories.map(cat => (
            <div key={cat} className="mb-2">
              <button onClick={() => toggleCat(cat)}
                className="flex items-center gap-1.5 w-full text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider py-1 hover:text-foreground">
                {expandedCats.has(cat) ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                {cat}
              </button>
              {expandedCats.has(cat) && ENDPOINTS.filter(e => e.category === cat).map(ep => (
                <button key={`${ep.method}-${ep.path}`}
                  onClick={() => selectEndpoint(ep)}
                  className={`w-full text-left rounded-lg px-2 py-1.5 text-xs mb-0.5 transition-colors ${
                    selectedEndpoint?.path === ep.path && selectedEndpoint?.method === ep.method
                      ? "bg-primary/10 text-primary" : "hover:bg-muted"
                  }`}>
                  <span className={`font-mono font-bold mr-1.5 ${methodColor[ep.method]}`}>{ep.method}</span>
                  <span className="text-muted-foreground">{ep.path.replace("/api/", "/")}</span>
                </button>
              ))}
            </div>
          ))}
        </div>

        {/* Main: Request Builder + Response */}
        <div className="flex-1 overflow-y-auto p-6">
          {!selectedEndpoint ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <Play className="h-10 w-10 mb-3" />
              <p className="text-sm">Wähle einen Endpoint aus der Liste</p>
            </div>
          ) : (
            <div className="space-y-4 max-w-2xl">
              {/* Endpoint Info */}
              <div className="flex items-center gap-3">
                <span className={`font-mono font-bold text-sm ${methodColor[selectedEndpoint.method]}`}>
                  {selectedEndpoint.method}
                </span>
                <code className="text-sm bg-muted rounded px-2 py-0.5 flex-1">{buildUrl()}</code>
              </div>
              <p className="text-sm text-muted-foreground">{selectedEndpoint.description}</p>

              {/* Path Parameters */}
              {selectedEndpoint.params && selectedEndpoint.params.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase text-muted-foreground">Parameter</h3>
                  {selectedEndpoint.params.map(p => (
                    <div key={p.name} className="flex items-center gap-2">
                      <label className="text-xs font-mono w-28 text-right shrink-0">
                        {p.name}{p.required && <span className="text-red-500">*</span>}
                      </label>
                      <input value={paramValues[p.name] || ""}
                        onChange={e => setParamValues({ ...paramValues, [p.name]: e.target.value })}
                        placeholder={p.description}
                        className="flex-1 rounded-lg border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ring" />
                    </div>
                  ))}
                </div>
              )}

              {/* Request Body */}
              {selectedEndpoint.body && selectedEndpoint.body.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase text-muted-foreground">Body</h3>
                  {selectedEndpoint.body.map(b => (
                    <div key={b.name} className="flex items-center gap-2">
                      <label className="text-xs font-mono w-28 text-right shrink-0">
                        {b.name}{b.required && <span className="text-red-500">*</span>}
                      </label>
                      <input value={bodyValues[b.name] || ""}
                        onChange={e => setBodyValues({ ...bodyValues, [b.name]: e.target.value })}
                        placeholder={b.description}
                        className="flex-1 rounded-lg border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ring" />
                    </div>
                  ))}
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2">
                <button onClick={sendRequest} disabled={loading}
                  className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  <Send className="w-3.5 h-3.5" />
                  {loading ? "Sende..." : "Request senden"}
                </button>
                <button onClick={copyCurl}
                  className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs hover:bg-muted">
                  {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? "Kopiert!" : "Als cURL kopieren"}
                </button>
              </div>

              {/* Response */}
              {response && (
                <div className="rounded-xl border overflow-hidden">
                  <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border-b">
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-bold ${
                        response.status >= 200 && response.status < 300 ? "text-green-500" :
                        response.status >= 400 ? "text-red-500" : "text-yellow-500"
                      }`}>{response.status || "Error"}</span>
                    </div>
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="w-3 h-3" /> {response.time}ms
                    </div>
                  </div>
                  <pre className="p-3 text-xs font-mono overflow-x-auto max-h-96 overflow-y-auto bg-background">
                    {response.body}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
