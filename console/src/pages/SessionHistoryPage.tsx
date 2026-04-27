import { useCallback, useEffect, useRef, useState } from "react";
import {
  MessageSquare, Search, Clock, Loader2, X, ChevronLeft,
  ChevronRight, Copy, CheckCircle2, AlertTriangle, Bot, FolderOpen,
} from "lucide-react";
import { api, type SessionPreview, type SessionFull, type SessionMessage } from "@/lib/api";
import { useTranslation } from "react-i18next";

// ── Helpers ────────────────────────────────────────────────────────────────────
function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); }
  catch { return iso; }
}

function fmtRelTime(iso: string | null) {
  if (!iso) return "";
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1)    return "gerade eben";
    if (mins < 60)   return `vor ${mins}m`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)    return `vor ${hrs}h`;
    const days = Math.floor(hrs / 24);
    return `vor ${days}d`;
  } catch { return ""; }
}

function isToolJson(content: string): boolean {
  try {
    const p = JSON.parse(content);
    return !!(p && (p.tool_call || p.tool_calls || p.result || p.error));
  } catch { return false; }
}

// ── MessagePart ────────────────────────────────────────────────────────────────
function MessagePart({ content }: { content: string }) {
  const isJson = isToolJson(content);

  if (isJson) {
    try {
      const data = JSON.parse(content);
      const title = data.tool_call
        ? `Tool: ${data.tool_call}`
        : data.tool_calls
          ? `Tools: ${data.tool_calls.length}`
          : data.error
            ? `Error`
            : `Result`;
      return (
        <details className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
          <summary className="cursor-pointer font-mono text-xs text-muted-foreground hover:text-foreground">
            {title}
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-foreground/70">
            {JSON.stringify(data, null, 2)}
          </pre>
        </details>
      );
    } catch {
      return <p className="whitespace-pre-wrap text-sm">{content}</p>;
    }
  }

  return <p className="whitespace-pre-wrap text-sm">{content}</p>;
}

// ── SessionSlideOver ────────────────────────────────────────────────────────────
interface SessionSlideOverProps {
  session: SessionFull | null;
  tab: "agent" | "project";
  agentId?: string;
  projectId?: string;
  onClose: () => void;
}

function SessionSlideOver({ session, tab, agentId, projectId, onClose }: SessionSlideOverProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<SessionFull | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!session) { setData(null); return; }
    // If we already have the full data passed in, use it
    if ((session as SessionFull).messages) {
      setData(session as SessionFull);
      return;
    }
    // Otherwise fetch
    setLoading(true);
    const id = (session as SessionPreview).id;
    const fetcher = tab === "agent" && agentId
      ? api.getSessionById(agentId, id)
      : projectId
        ? api.get<SessionFull>(`/projects/${projectId}/sessions/${id}`)
        : null;
    if (!fetcher) { setLoading(false); return; }
    fetcher.then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [session, tab, agentId, projectId]);

  useEffect(() => {
    if (data) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [data]);

  if (!session) return null;

  const preview = (session as SessionPreview).preview ?? "";

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />

      {/* Panel */}
      <div className="fixed right-0 top-0 z-50 flex h-full w-full flex-col bg-background shadow-2xl animate-in slide-in-from-right duration-200"
           style={{ maxWidth: "680px" }}>
        {/* Header */}
        <div className="flex items-center gap-3 border-b px-4 py-3 shrink-0">
          <button
            onClick={onClose}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronRight className="w-4 h-4" />
            {t("common.back")}
          </button>
          <div className="h-4 w-px bg-border" />
          <div className="flex-1 min-w-0">
            <p className="font-mono text-xs truncate">
              {(session as SessionPreview).id ?? session.id}
            </p>
            <p className="text-xs text-muted-foreground">
              {(session as SessionPreview).message_count
                ? `${(session as SessionPreview).message_count} messages`
                : fmtDate(session.started_at)}
              {" · "}
              {preview && <span className="truncate max-w-[300px]">{preview}</span>}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1.5 hover:bg-muted transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-auto p-4 space-y-3">
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : !data ? (
            <p className="text-center text-muted-foreground py-12">Session nicht geladen.</p>
          ) : data.messages.length === 0 ? (
            <p className="text-center text-muted-foreground py-12">Keine Messages.</p>
          ) : (
            data.messages.map((msg, i) => {
              const roleColors: Record<string, string> = {
                user:      "bg-blue-900/40 text-blue-300",
                assistant: "bg-green-900/40 text-green-300",
                tool:      "bg-purple-900/40 text-purple-300",
                system:    "bg-muted text-muted-foreground",
              };
              return (
                <div key={i} className="flex gap-3">
                  <div className="flex flex-col items-center gap-1 shrink-0 pt-0.5">
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${roleColors[msg.role] ?? "bg-muted text-muted-foreground"}`}>
                      {msg.role}
                    </span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(msg.content);
                        setCopiedId(String(i));
                        setTimeout(() => setCopiedId(null), 1500);
                      }}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                      title="Copy"
                    >
                      {copiedId === String(i)
                        ? <CheckCircle2 className="w-3 h-3 text-green-500" />
                        : <Copy className="w-3 h-3" />}
                    </button>
                  </div>
                  <div className="flex-1 min-w-0 space-y-1">
                    <p className="text-xs text-muted-foreground">
                      {fmtDate(msg.timestamp)}
                      {msg.tool_call_id && (
                        <span className="ml-2 font-mono text-purple-400">tool: {msg.tool_call_id.slice(0, 12)}…</span>
                      )}
                    </p>
                    <MessagePart content={msg.content} />
                  </div>
                </div>
              );
            })
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────────

type Tab = "agent" | "project";

export default function SessionHistoryPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("agent");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sessions, setSessions] = useState<SessionPreview[]>([]);
  const [selectedSession, setSelectedSession] = useState<SessionPreview | null>(null);

  // Agents / Projects for dropdowns
  const [agents, setAgents] = useState<{id: string; name: string}[]>([]);
  const [projects, setProjects] = useState<{id: string; name: string}[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [selectedProject, setSelectedProject] = useState<string>("");

  // Filters
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<{session_id: string; started_at: string; match_count: number; matches: {role: string; content: string; timestamp: string}[]}[]>([]);
  const [searching, setSearching] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [limit, setLimit] = useState(50);

  const limitRef = useRef(50);
  limitRef.current = limit;

  // Load agents
  useEffect(() => {
    api.agents().then(r => {
      const amap = (r as any).agents ?? r as Record<string, unknown>;
      setAgents(
        Object.entries(amap as Record<string, unknown>).map(([id, cfg]: [string, unknown]) => ({
          id,
          name: (cfg as any)?.config?.identity?.name ?? (cfg as any)?.name ?? id,
        }))
      );
    }).catch(() => {});
  }, []);

  // Load projects
  useEffect(() => {
    api.projects().then(r => {
      const pmap = (r as any).projects ?? r as Record<string, unknown>;
      setProjects(
        Object.entries(pmap as Record<string, unknown>).map(([id, cfg]: [string, unknown]) => ({
          id,
          name: (cfg as any)?.name ?? id,
        }))
      );
    }).catch(() => {});
  }, []);

  // Load sessions
  async function loadSessions(target: Tab, id: string, q?: string) {
    if (!id) return;
    setLoading(true);
    setError("");
    setSessions([]);
    try {
      if (q && q.length >= 2 && target === "agent") {
        const r = await api.searchAgentSessions(id, q);
        setSearchResults(r.results ?? []);
        // Load previews for matching sessions
        const listR = await api.listSessions(id, limitRef.current);
        const matchedIds = new Set(r.results.map((s: { session_id: string }) => s.session_id));
        setSessions((listR.sessions ?? []).filter((s: SessionPreview) => matchedIds.has(s.id)));
        setLoading(false);
        return;
      }
      const r = target === "agent"
        ? await api.listSessions(id, limitRef.current)
        : await api.listProjectSessions(id, limitRef.current);
      setSessions(r.sessions ?? []);
      setSearchResults([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed");
    } finally {
      setLoading(false);
    }
  }

  function applyFilters() {
    if (tab === "agent" && selectedAgent) {
      loadSessions("agent", selectedAgent, searchQ);
    } else if (tab === "project" && selectedProject) {
      loadSessions("project", selectedProject);
    }
  }

  function handleSearch() {
    setLoading(true);
    loadSessions(tab, tab === "agent" ? selectedAgent : selectedProject, searchQ);
  }

  function handleTabChange(newTab: Tab) {
    setTab(newTab);
    setSessions([]);
    setSearchResults([]);
    setSearchQ("");
    setError("");
  }

  // Auto-load when tab/id changes
  useEffect(() => {
    if (tab === "agent" && selectedAgent) {
      loadSessions("agent", selectedAgent);
    } else if (tab === "project" && selectedProject) {
      loadSessions("project", selectedProject);
    }
  }, [tab, selectedAgent, selectedProject]);

  // Client-side date filter
  const filteredSessions = sessions.filter(s => {
    if (!dateFrom && !dateTo) return true;
    const d = new Date(s.started_at);
    if (dateFrom && d < new Date(dateFrom)) return false;
    if (dateTo && d > new Date(dateTo + "T23:59:59")) return false;
    return true;
  });

  const hasDateFilter = dateFrom || dateTo;
  const activeLimit = limitRef.current;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <MessageSquare className="w-6 h-6 text-blue-400" />
        <div>
          <h1 className="text-xl font-semibold">{t("nav.sessionHistory", "Session History")}</h1>
          <p className="text-xs text-muted-foreground">{t("pageDesc.sessionHistory", "Alte Chat-Sessions durchsuchen")}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b">
        <button
          onClick={() => handleTabChange("agent")}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm border-b-2 transition-colors ${
            tab === "agent"
              ? "border-blue-500 text-blue-500"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <Bot className="w-4 h-4" />
          {t("sessionHistory.agentSessions", "Agent-Sessions")}
        </button>
        <button
          onClick={() => handleTabChange("project")}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm border-b-2 transition-colors ${
            tab === "project"
              ? "border-blue-500 text-blue-500"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <FolderOpen className="w-4 h-4" />
          {t("sessionHistory.projectSessions", "Project-Sessions")}
        </button>
      </div>

      {/* Toolbar */}
      <div className="space-y-3">
        {/* Entity selector */}
        <div className="flex flex-wrap gap-3">
          {tab === "agent" ? (
            <select
              value={selectedAgent}
              onChange={e => { setSelectedAgent(e.target.value); }}
              className="bg-card border border-border rounded px-3 py-2 text-sm"
            >
              <option value="">— Agent wählen —</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          ) : (
            <select
              value={selectedProject}
              onChange={e => { setSelectedProject(e.target.value); }}
              className="bg-card border border-border rounded px-3 py-2 text-sm"
            >
              <option value="">— Project wählen —</option>
              {projects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}

          {/* Search (agent only) */}
          {tab === "agent" && selectedAgent && (
            <div className="flex gap-2 flex-1 min-w-[200px]">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  value={searchQ}
                  onChange={e => setSearchQ(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleSearch()}
                  placeholder={t("sessionHistory.searchPlaceholder", "Suchbegriff (min 2 Zeichen)…")}
                  className="w-full bg-card border border-border rounded pl-9 pr-3 py-2 text-sm"
                />
              </div>
              <button
                onClick={handleSearch}
                disabled={searching || searchQ.length < 2}
                className="px-3 py-2 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              </button>
            </div>
          )}
        </div>

        {/* Date filter + limit */}
        <div className="flex flex-wrap gap-3 items-center">
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Von:</span>
            <input
              type="date"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              className="bg-card border border-border rounded px-2 py-1 text-sm"
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Bis:</span>
            <input
              type="date"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              className="bg-card border border-border rounded px-2 py-1 text-sm"
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Limit:</span>
            <select
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
              className="bg-card border border-border rounded px-2 py-1 text-sm"
            >
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
            </select>
          </label>
          <button
            onClick={() => { setDateFrom(""); setDateTo(""); setLimit(50); }}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Filter zurücksetzen
          </button>
        </div>

        {hasDateFilter && (
          <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-950/30 rounded px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            Datumsfilter arbeitet auf den geladenen Sessions (Limit: {activeLimit}). Ältere Sessions werden nicht gefiltert.
          </div>
        )}

        {searchResults.length > 0 && (
          <div className="text-xs text-muted-foreground">
            {t("sessionHistory.searchResults", "Volltext-Suchergebnisse")}: {searchResults.length} Sessions mit {searchResults.reduce((a, r) => a + r.match_count, 0)} Treffern
          </div>
        )}
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive/30 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Session list */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : filteredSessions.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <MessageSquare className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>
            {(!selectedAgent && tab === "agent")
              ? t("sessionHistory.selectAgent", "Agent wählen um Sessions zu sehen")
              : (!selectedProject && tab === "project")
                ? t("sessionHistory.selectProject", "Project wählen um Sessions zu sehen")
                : t("sessionHistory.empty", "Keine Sessions gefunden")}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredSessions.map(s => {
            const searchMatch = searchResults.find(r => r.session_id === s.id);
            return (
              <div
                key={s.id}
                className="bg-card border border-border rounded-xl p-4 space-y-2 hover:border-ring cursor-pointer transition-colors"
                onClick={() => setSelectedSession(s)}
              >
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">
                        {s.id}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        <Clock className="w-3 h-3 inline mr-1" />
                        {fmtRelTime(s.started_at)} · {fmtDate(s.started_at)}
                      </span>
                      {s.ended_at && (
                        <span className="text-xs text-muted-foreground">
                          → {fmtDate(s.ended_at)}
                        </span>
                      )}
                      <span className="text-xs bg-muted/50 text-muted-foreground px-2 py-0.5 rounded">
                        {s.message_count} msg
                      </span>
                    </div>
                    {s.preview && (
                      <p className="text-sm text-muted-foreground mt-1.5 truncate">{s.preview}</p>
                    )}
                    {searchMatch && (
                      <div className="mt-2 space-y-1">
                        {searchMatch.matches.slice(0, 3).map((m, i) => (
                          <div key={i} className="text-xs bg-muted/30 rounded p-2 border-l-2 border-blue-500">
                            <span className="font-mono text-blue-400 mr-2">{m.role}:</span>
                            <span className="text-muted-foreground">{m.content.slice(0, 120)}{m.content.length > 120 ? "…" : ""}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0 mt-1" />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Slide-over */}
      <SessionSlideOver
        session={selectedSession}
        tab={tab}
        agentId={selectedAgent}
        projectId={selectedProject}
        onClose={() => setSelectedSession(null)}
      />
    </div>
  );
}
