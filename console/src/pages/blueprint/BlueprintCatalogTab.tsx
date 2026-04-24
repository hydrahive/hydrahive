import { useEffect, useState } from "react";
import { Bookmark, Plus, Trash2, Download, Upload, Loader2, Rocket } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface BlueprintSummary {
  id: string;
  version: string;
  description: string;
  installed_at: string;
  node_count: number;
}

export function BlueprintCatalogTab() {
  const { t } = useTranslation();
  const [bps, setBps] = useState<BlueprintSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [agents, setAgents] = useState<string[]>([]);
  const [installFor, setInstallFor] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importId, setImportId] = useState("");
  const [importData, setImportData] = useState("");
  const [importing, setImporting] = useState(false);
  const [confirm, setConfirm] = useState<{ bpId: string; action: () => void; title: string; message: string } | null>(null);

  async function load() {
    setLoading(true);
    try {
      const data = await api.blueprintList();
      setBps(data ?? []);
    } catch {
      setBps([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function showToast(msg: string, ok: boolean) {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  }

  async function doInstall(bpId: string, agentId: string) {
    setInstalling(bpId);
    setInstallFor(null);
    try {
      await api.blueprintInstall(bpId, agentId);
      showToast(`Installiert auf ${agentId}`, true);
    } catch (e: any) {
      showToast("Install fehlgeschlagen: " + (e.message ?? "Unbekannt"), false);
    } finally {
      setInstalling(null);
    }
  }

  async function doDelete(bpId: string) {
    setDeleting(bpId);
    try {
      await api.blueprintDelete(bpId);
      setBps(bps => bps.filter(b => b.id !== bpId));
    } finally {
      setDeleting(null);
      setConfirm(null);
    }
  }

  async function doImport() {
    if (!importId.trim()) return;
    setImporting(true);
    try {
      const parsed = JSON.parse(importData);
      await api.blueprintImport({ id: importId, ...parsed });
      setImportOpen(false);
      setImportId("");
      setImportData("");
      load();
    } catch (e: any) {
      alert("Import fehlgeschlagen: " + e.message);
    } finally {
      setImporting(false);
    }
  }

  async function doExport(bpId: string) {
    const data = await api.blueprintExport(bpId);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${bpId}.blueprint.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex h-full flex-col">
      {toast && (
        <div className={cn("fixed top-4 right-4 z-50 rounded-lg px-4 py-2 text-sm font-medium shadow-xl",
          toast.ok ? "bg-green-900/90 text-green-200 border border-green-700" : "bg-red-900/90 text-red-200 border border-red-700")}>
          {toast.ok ? "✓" : "✗"} {toast.msg}
        </div>
      )}
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10 shrink-0">
        <Bookmark className="h-4 w-4 text-indigo-400" />
        <span className="text-sm font-medium text-white">Blueprints</span>
        <span className="text-xs text-white/30">{bps.length} insgesamt</span>
        <div className="flex-1" />
        <button
          onClick={() => setImportOpen(true)}
          className="flex items-center gap-1.5 rounded-lg bg-zinc-800 border border-white/10 px-3 py-1.5 text-xs text-white hover:bg-zinc-700 transition-colors">
          <Upload className="h-3.5 w-3.5" /> Import
        </button>
        <button
          onClick={load}
          className="flex items-center gap-1.5 rounded-lg bg-zinc-800 border border-white/10 px-3 py-1.5 text-xs text-white hover:bg-zinc-700 transition-colors">
          <Download className="h-3.5 w-3.5" /> Aktualisieren
        </button>
      </div>

      {/* Liste */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="h-6 w-6 text-white/20 animate-spin" />
          </div>
        ) : bps.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-white/20">
            <Bookmark className="h-8 w-8 mb-2" />
            <p className="text-sm">Keine Blueprints vorhanden</p>
            <p className="text-xs mt-1">Importiere eins oder erstelle es per API</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {bps.map(bp => (
              <div key={bp.id} className="rounded-xl border border-white/10 bg-zinc-900/60 p-4 hover:border-indigo-500/30 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="text-sm font-semibold text-white truncate">{bp.id}</h3>
                      <span className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[0.6rem] text-white/40">v{bp.version}</span>
                      {bp.node_count > 0 && (
                        <span className="shrink-0 rounded bg-indigo-950/60 px-1.5 py-0.5 text-[0.6rem] text-indigo-300">{bp.node_count} Nodes</span>
                      )}
                    </div>
                    <p className="text-xs text-white/40 line-clamp-2">{bp.description || "Keine Beschreibung"}</p>
                    {bp.installed_at && (
                      <p className="text-[0.6rem] text-white/20 mt-1">Installiert: {new Date(bp.installed_at).toLocaleDateString("de")}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => doExport(bp.id)}
                      className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors"
                      title="Exportieren">
                      <Download className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => setConfirm({ bpId: bp.id, action: () => doDelete(bp.id), title: "Blueprint löschen", message: ` '${bp.id}' unwiderruflich löschen?` })}
                      className="p-1.5 rounded-lg text-red-400/60 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      title="Löschen">
                      {deleting === bp.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Import-Dialog */}
      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 p-6 shadow-2xl">
            <h2 className="text-lg font-semibold text-white mb-4">Blueprint importieren</h2>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-white/40 mb-1">Blueprint-ID</label>
                <input value={importId} onChange={e => setImportId(e.target.value)}
                  placeholder="z.B. mein-blueprint"
                  className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60" />
              </div>
              <div>
                <label className="block text-xs text-white/40 mb-1">Blueprint JSON</label>
                <textarea value={importData} onChange={e => setImportData(e.target.value)}
                  placeholder='{"version":"1.0","nodes":[],"edges":[]}'
                  rows={8}
                  className="w-full rounded-lg bg-zinc-800 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500/60 font-mono resize-none" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => { setImportOpen(false); setImportId(""); setImportData(""); }}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-white/60 hover:text-white transition-colors">
                Abbrechen
              </button>
              <button onClick={doImport} disabled={importing || !importId.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors">
                {importing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Importieren
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ""}
        message={confirm?.message ?? ""}
        onConfirm={() => confirm?.action()}
        onCancel={() => setConfirm(null)}
        variant="danger"
      />
    </div>
  );
}
