import { useRef, useState } from "react";
import { Download, Upload, Server, AlertTriangle, CheckCircle, Loader2, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

function Section({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="bg-card border rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary" />
        <h2 className="font-semibold text-sm">{title}</h2>
      </div>
      {children}
    </div>
  );
}

export function MigrationPage() {
  const { t } = useTranslation();

  // ── Export ──────────────────────────────────────────────────────────────
  const [exportLoading, setExportLoading] = useState(false);
  const [exportAmem,    setExportAmem]    = useState(false);
  const [exportError,   setExportError]   = useState("");

  async function handleExport() {
    setExportLoading(true); setExportError("");
    try {
      const blob = await api.migrationExport(exportAmem);
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = "hydrahive-export.tar.gz.enc";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export fehlgeschlagen");
    } finally {
      setExportLoading(false);
    }
  }

  // ── Import ──────────────────────────────────────────────────────────────
  const fileRef = useRef<HTMLInputElement>(null);
  const [importFile,    setImportFile]    = useState<File | null>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importError,   setImportError]   = useState("");
  const [importDone,    setImportDone]    = useState(false);

  async function handleImport() {
    if (!importFile) return;
    setImportLoading(true); setImportError(""); setImportDone(false);
    try {
      await api.migrationImport(importFile);
      setImportDone(true);
      setImportFile(null);
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import fehlgeschlagen");
    } finally {
      setImportLoading(false);
    }
  }

  // ── Transfer ────────────────────────────────────────────────────────────
  const [target,          setTarget]          = useState("");
  const [sshKey,          setSshKey]          = useState("/root/.ssh/id_ed25519");
  const [sshPort,         setSshPort]         = useState("22");
  const [transferAmem,    setTransferAmem]    = useState(false);
  const [transferRunning, setTransferRunning] = useState(false);
  const [transferLog,     setTransferLog]     = useState<string[]>([]);
  const [transferDone,    setTransferDone]    = useState(false);
  const [transferError,   setTransferError]   = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  async function handleTransfer() {
    if (!target.trim()) return;
    setTransferRunning(true); setTransferLog([]); setTransferDone(false); setTransferError("");

    const form = new FormData();
    form.append("target", target.trim());
    form.append("ssh_key", sshKey);
    form.append("ssh_port", sshPort);
    form.append("include_amem", String(transferAmem));

    try {
      const res = await fetch("/api/admin/migration/transfer", {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.split("\n").find(l => l.startsWith("data: "));
          if (!line) continue;
          const msg = line.slice(6);
          if (msg === "__DONE__") { setTransferDone(true); setTransferRunning(false); return; }
          if (msg.startsWith("__ERROR__")) { setTransferError(msg.slice(9).trim()); setTransferRunning(false); return; }
          setTransferLog(prev => [...prev, msg]);
          setTimeout(() => logRef.current?.scrollTo(0, logRef.current.scrollHeight), 10);
        }
      }
    } catch (e) {
      setTransferError(e instanceof Error ? e.message : "Transfer fehlgeschlagen");
    } finally {
      setTransferRunning(false);
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-semibold">Migration</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Export, Import und direkter Server-zu-Server Transfer
        </p>
      </div>

      {/* Export */}
      <Section title="Export" icon={Download}>
        <p className="text-xs text-muted-foreground">
          Erstellt ein AES-256-verschlüsseltes Archiv (Agenten, Configs, Memory).
          Das Passwort wird im Terminal-Output des Skripts ausgegeben.
        </p>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={exportAmem} onChange={e => setExportAmem(e.target.checked)}
            className="rounded" />
          A-MEM Model-Cache einschließen (groß, ~2 GB)
        </label>
        {exportError && <p className="text-xs text-destructive">{exportError}</p>}
        <button onClick={handleExport} disabled={exportLoading}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {exportLoading
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Exportiere...</>
            : <><Download className="h-4 w-4" /> Export herunterladen</>}
        </button>
      </Section>

      {/* Import */}
      <Section title="Import" icon={Upload}>
        <p className="text-xs text-muted-foreground">
          Spielt ein Export-Archiv ein. HydraHive wird danach neu gestartet.
        </p>
        <div className="flex items-center gap-3">
          <input ref={fileRef} type="file" accept=".enc,.tar.gz.enc"
            onChange={e => { setImportFile(e.target.files?.[0] ?? null); setImportDone(false); setImportError(""); }}
            className="text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:text-xs file:rounded file:border file:bg-card file:hover:bg-accent cursor-pointer" />
        </div>
        {importError && <p className="text-xs text-destructive">{importError}</p>}
        {importDone && (
          <div className="flex items-center gap-2 text-xs text-green-500">
            <CheckCircle className="h-3.5 w-3.5" /> Import abgeschlossen — Service wird neu gestartet
          </div>
        )}
        <div className="flex items-start gap-2 text-xs text-orange-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
          <span>Überschreibt bestehende Daten. Vorher Backup erstellen!</span>
        </div>
        <button onClick={handleImport} disabled={!importFile || importLoading}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 transition-colors">
          {importLoading
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Importiere...</>
            : <><Upload className="h-4 w-4" /> Import starten</>}
        </button>
      </Section>

      {/* Transfer */}
      <Section title="Server-zu-Server Transfer" icon={Server}>
        <p className="text-xs text-muted-foreground">
          Exportiert direkt auf einen Ziel-Server via SSH — ohne lokale Zwischenspeicherung.
          Ziel muss HydraHive bereits installiert haben.
        </p>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Ziel-Server (user@host)</label>
            <input value={target} onChange={e => setTarget(e.target.value)}
              placeholder="root@192.168.1.100"
              className="w-full px-3 py-1.5 text-sm bg-background border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground block mb-1">SSH Key Pfad</label>
              <input value={sshKey} onChange={e => setSshKey(e.target.value)}
                className="w-full px-3 py-1.5 text-sm bg-background border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">SSH Port</label>
              <input value={sshPort} onChange={e => setSshPort(e.target.value)} type="number"
                className="w-full px-3 py-1.5 text-sm bg-background border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={transferAmem} onChange={e => setTransferAmem(e.target.checked)}
              className="rounded" />
            A-MEM einschließen
          </label>
        </div>

        {transferLog.length > 0 && (
          <div ref={logRef}
            className="h-48 overflow-y-auto bg-black/40 rounded-lg p-3 font-mono text-xs text-green-400 space-y-0.5">
            {transferLog.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        )}

        {transferError && <p className="text-xs text-destructive">{transferError}</p>}
        {transferDone && (
          <div className="flex items-center gap-2 text-xs text-green-500">
            <CheckCircle className="h-3.5 w-3.5" /> Transfer abgeschlossen
          </div>
        )}

        <button onClick={handleTransfer} disabled={!target.trim() || transferRunning}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {transferRunning
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Transfer läuft...</>
            : <><ArrowRight className="h-4 w-4" /> Transfer starten</>}
        </button>
      </Section>
    </div>
  );
}
