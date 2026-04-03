import { useEffect, useRef, useState } from "react";
import {
  Search, Code2, GitBranch, Cpu, MessageCircle, Network, KeyRound,
  CheckCircle, XCircle, AlertCircle, Download, Trash2,
  ExternalLink, Loader2, RefreshCw, ChevronDown, ChevronUp,
} from "lucide-react";
import { useTranslation } from "react-i18next";

interface Extension {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  installed: boolean;
  active: boolean;
  http_ok: boolean;
  open_url: string | null;
  has_uninstall: boolean;
}

const ICON_MAP: Record<string, React.ElementType> = {
  Search, Code2, GitBranch, Cpu, MessageCircle, Network, KeyRound,
};

const CATEGORY_LABELS: Record<string, string> = {
  tools:         "Tools",
  ai:            "KI & Modelle",
  communication: "Kommunikation",
  network:       "Netzwerk",
  security:      "Sicherheit",
};

function ExtCard({
  ext,
  onAction,
}: {
  ext: Extension;
  onAction: (id: string, action: "install" | "uninstall") => void;
}) {
  const { t } = useTranslation();
  const Icon = ICON_MAP[ext.icon] ?? Download;

  const status = !ext.installed
    ? "not_installed"
    : ext.active
    ? "active"
    : "stopped";

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-primary/10 shrink-0">
          <Icon className="w-5 h-5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm">{ext.name}</span>
            {status === "active"       && <span className="status-pill status-pill-ok">{t("extensions.statusActive")}</span>}
            {status === "stopped"      && <span className="status-pill status-pill-warn">{t("extensions.statusStopped")}</span>}
            {status === "not_installed"&& <span className="status-pill">{t("extensions.statusNotInstalled")}</span>}
          </div>
          <p className="text-xs text-muted-foreground mt-1">{ext.description}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap mt-auto">
        {!ext.installed && (
          <button
            className="btn btn-primary btn-sm flex items-center gap-1.5"
            onClick={() => onAction(ext.id, "install")}
          >
            <Download className="w-3.5 h-3.5" />
            {t("extensions.install")}
          </button>
        )}
        {ext.installed && ext.open_url && ext.active && (
          <a
            href={ext.open_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-primary btn-sm flex items-center gap-1.5"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            {t("extensions.open")}
          </a>
        )}
        {ext.installed && ext.has_uninstall && (
          <button
            className="btn btn-sm flex items-center gap-1.5 text-red-500 hover:bg-red-50 dark:hover:bg-red-950 border border-red-200 dark:border-red-800"
            onClick={() => onAction(ext.id, "uninstall")}
          >
            <Trash2 className="w-3.5 h-3.5" />
            {t("extensions.uninstall")}
          </button>
        )}
      </div>
    </div>
  );
}

export function ExtensionsPage() {
  const { t } = useTranslation();
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error,      setError]      = useState("");

  // Aktiver Stream-Vorgang
  const [activeId,    setActiveId]    = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<"install" | "uninstall" | null>(null);
  const [log,         setLog]         = useState<string[]>([]);
  const [logDone,     setLogDone]     = useState<boolean | null>(null);
  const [logExpanded, setLogExpanded] = useState(true);
  const logRef    = useRef<HTMLDivElement>(null);
  const abortRef  = useRef<AbortController | null>(null);

  // Cleanup bei Unmount: laufenden Stream abbrechen
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  async function load(quiet = false) {
    if (!quiet) setLoading(true);
    else setRefreshing(true);
    try {
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch("/api/admin/extensions", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setExtensions(await res.json());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("extensions.loadError"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  async function handleAction(id: string, action: "install" | "uninstall") {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setActiveId(id);
    setActiveAction(action);
    setLog([]);
    setLogDone(null);
    setLogExpanded(true);

    const token = localStorage.getItem("hydrahive_token") || "";
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const res = await fetch(`/api/admin/extensions/${id}/${action}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        setLog([`Fehler: HTTP ${res.status}`]);
        setLogDone(false);
        return;
      }
      reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.replace(/^data: /, "").trim();
          if (!line) continue;
          try {
            const d = JSON.parse(line);
            if (d.line !== undefined) setLog(l => [...l.slice(-199), d.line]);
            if (d.done) {
              setLogDone(d.ok);
              if (d.ok) load(true);
            }
          } catch { /* ignore */ }
        }
      }
      // Flush remaining buffer after stream ends
      if (buf.trim()) {
        const line = buf.replace(/^data: /, "").trim();
        try {
          const d = JSON.parse(line);
          if (d.line !== undefined) setLog(l => [...l.slice(-199), d.line]);
          if (d.done) {
            setLogDone(d.ok);
            if (d.ok) load(true);
          }
        } catch { /* ignore */ }
      }
      // If stream ended without a done event, mark as failed
      setLogDone(prev => prev ?? false);
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        setLog(l => [...l.slice(-199), `[ERROR] ${e.message}`]);
        setLogDone(false);
      }
    } finally {
      reader?.cancel().catch(() => {});
    }
  }

  function clearLog() {
    setActiveId(null);
    setActiveAction(null);
    setLog([]);
    setLogDone(null);
  }

  const categories = Array.from(new Set(extensions.map(e => e.category)));

  if (loading) return (
    <div className="p-8 text-sm text-muted-foreground">{t("extensions.loading")}</div>
  );

  return (
    <div className="p-8 space-y-8 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("extensions.title")}</h1>
          <p className="text-xs text-muted-foreground">{t("pageDesc.extensions")}</p>
          <p className="text-sm text-muted-foreground mt-1">{t("extensions.subtitle")}</p>
        </div>
        <button
          className="btn btn-sm flex items-center gap-1.5"
          onClick={() => load(true)}
          disabled={refreshing}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {t("extensions.refresh")}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-500 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Live-Log */}
      {activeId && (
        <div className="card border-primary/30 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {logDone === null && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
              {logDone === true  && <CheckCircle className="w-4 h-4 text-green-500" />}
              {logDone === false && <XCircle className="w-4 h-4 text-red-500" />}
              <span className="text-sm font-medium">
                {activeAction === "install" ? t("extensions.installing") : t("extensions.uninstalling")}
                {" "}<span className="text-muted-foreground">{extensions.find(e => e.id === activeId)?.name}</span>
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn btn-sm" onClick={() => setLogExpanded(v => !v)}>
                {logExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>
              {logDone !== null && (
                <button className="btn btn-sm" onClick={clearLog}>{t("extensions.close")}</button>
              )}
            </div>
          </div>

          {logExpanded && (
            <div
              ref={logRef}
              className="bg-black text-green-400 font-mono text-xs rounded p-3 h-56 overflow-y-auto"
            >
              {log.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
            </div>
          )}

          {logDone === true && (
            <p className="text-sm text-green-600 font-medium flex items-center gap-2">
              <CheckCircle className="w-4 h-4" />
              {activeAction === "install" ? t("extensions.installSuccess") : t("extensions.uninstallSuccess")}
            </p>
          )}
          {logDone === false && (
            <p className="text-sm text-red-500 font-medium flex items-center gap-2">
              <XCircle className="w-4 h-4" />
              {t("extensions.actionFailed")}
            </p>
          )}
        </div>
      )}

      {/* Extensions nach Kategorie */}
      {categories.map(cat => (
        <div key={cat} className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            {CATEGORY_LABELS[cat] ?? cat}
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {extensions
              .filter(e => e.category === cat)
              .map(ext => (
                <ExtCard
                  key={ext.id}
                  ext={ext}
                  onAction={handleAction}
                />
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}
