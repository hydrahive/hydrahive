import { useEffect, useRef, useState } from "react";
import { Code2, CheckCircle, XCircle, ExternalLink, Download, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

interface CodeserverStatus {
  installed: boolean;
  service_active: boolean;
  version: string;
  port: number;
  url: string;
  password?: string | null;
}

export function CodeEditorPage() {
  const { t } = useTranslation();
  const [status,   setStatus]  = useState<CodeserverStatus | null>(null);
  const [loading,  setLoading] = useState(true);
  const [error,    setError]   = useState("");

  const [installing,   setInstalling]   = useState(false);
  const [installLog,   setInstallLog]   = useState<string[]>([]);
  const [installDone,  setInstallDone]  = useState<boolean | null>(null);
  const logRef   = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => { abortRef.current?.abort(); }, []);

  async function load() {
    try {
      const res = await fetch("/api/admin/codeserver/status", {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(await res.json());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("codeEditor.loadError"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleInstall() {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setInstalling(true);
    setInstallLog([]);
    setInstallDone(null);
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const res = await fetch("/api/admin/codeserver/install", {
        method: "POST",
        credentials: "include",
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        setInstallLog([`Fehler: HTTP ${res.status}`]);
        setInstallDone(false);
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
            if (d.line !== undefined) setInstallLog(l => [...l.slice(-199), d.line]);
            if (d.done) {
              setInstallDone(d.ok);
              if (d.ok) load();
            }
          } catch { /* ignore */ }
        }
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        setInstallLog(l => [...l.slice(-199), `[ERROR] ${e.message}`]);
        setInstallDone(false);
      }
    } finally {
      reader?.cancel().catch(() => {});
      setInstalling(false);
    }
  }

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [installLog]);

  if (loading) return (
    <div className="p-8 text-sm text-muted-foreground">{t("codeEditor.loading")}</div>
  );

  if (!status?.installed) return (
    <div className="p-8 max-w-xl space-y-5">
      <div className="flex items-center gap-3">
        <XCircle className="w-6 h-6 text-yellow-500 shrink-0" />
        <h2 className="text-lg font-semibold">{t("codeEditor.notInstalledTitle")}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t("codeEditor.notInstalledBody")}</p>

      {installDone === null && (
        <button
          className="btn btn-primary flex items-center gap-2"
          onClick={handleInstall}
          disabled={installing}
        >
          {installing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
          {installing ? t("codeEditor.installing") : t("codeEditor.installBtn")}
        </button>
      )}

      {installLog.length > 0 && (
        <div
          ref={logRef}
          className="bg-black text-green-400 font-mono text-xs rounded p-3 h-64 overflow-y-auto"
        >
          {installLog.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
        </div>
      )}

      {installDone === true && (
        <div className="flex items-center gap-2 text-green-600 text-sm font-medium">
          <CheckCircle className="w-4 h-4" />
          {t("codeEditor.installSuccess")}
        </div>
      )}
      {installDone === false && (
        <div className="flex items-center gap-2 text-red-500 text-sm font-medium">
          <XCircle className="w-4 h-4" />
          {t("codeEditor.installFailed")}
        </div>
      )}

      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
  );

  const isRunning = status.service_active;

  return (
    <div className="p-8 max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <Code2 className="w-6 h-6 text-primary shrink-0" />
        <h1 className="text-xl font-semibold">{t("codeEditor.title")}</h1>
      </div>

      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">{t("codeEditor.statusLabel")}</p>
            {status.version && (
              <p className="text-xs text-muted-foreground">{status.version}</p>
            )}
          </div>
          <span className={`status-pill ${isRunning ? "status-pill-ok" : "status-pill-warn"}`}>
            {isRunning ? t("codeEditor.statusRunning") : t("codeEditor.statusStopped")}
          </span>
        </div>

        {status.password && (
          <div className="flex items-center gap-3 rounded-md bg-muted px-3 py-2">
            <span className="text-xs text-muted-foreground shrink-0">{t("codeEditor.password")}</span>
            <code className="text-sm font-mono select-all">{status.password}</code>
          </div>
        )}

        {isRunning && (
          <a
            href={status.url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-primary inline-flex items-center gap-2"
          >
            <ExternalLink className="w-4 h-4" />
            {t("codeEditor.openBtn")}
          </a>
        )}

        {!isRunning && (
          <p className="text-sm text-muted-foreground">{t("codeEditor.notRunning")}</p>
        )}
      </div>
    </div>
  );
}
