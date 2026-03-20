import { useEffect, useState } from "react";
import { Archive, Download, Trash2, RefreshCw, RotateCcw, Plus, AlertTriangle } from "lucide-react";
import { api, BackupEntry } from "@/lib/api";

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString("de-DE");
}

export function BackupPage() {
  const [backups,    setBackups]    = useState<BackupEntry[]>([]);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");
  const [creating,   setCreating]   = useState(false);
  const [deleting,   setDeleting]   = useState<string | null>(null);
  const [restoring,  setRestoring]  = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [confirmRes, setConfirmRes] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try {
      const d = await api.listBackups();
      setBackups(d.backups);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler");
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

  async function handleCreate() {
    setCreating(true); setError("");
    try {
      await api.createBackup();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backup fehlgeschlagen");
    } finally { setCreating(false); }
  }

  async function handleDelete(name: string) {
    setDeleting(name); setConfirmDel(null);
    try {
      await api.deleteBackup(name);
      setBackups(b => b.filter(x => x.name !== name));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Löschen fehlgeschlagen");
    } finally { setDeleting(null); }
  }

  async function handleRestore(name: string) {
    setRestoring(name); setConfirmRes(null);
    try {
      await api.restoreBackup(name);
      setError("");
      // Service startet neu — kurze Info
      alert(`Restore von "${name}" gestartet. OctopOS startet neu — bitte in 10 Sekunden neu laden.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restore fehlgeschlagen");
    } finally { setRestoring(null); }
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Backup & Restore</h1>
          <p className="text-sm text-muted-foreground">
            Sichert /etc/octopos, /agents und /projects
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={refresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Aktualisieren
          </button>
          <button onClick={handleCreate} disabled={creating}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors disabled:opacity-50">
            <Plus className={`h-3.5 w-3.5 ${creating ? "animate-spin" : ""}`} />
            {creating ? "Erstelle..." : "Backup erstellen"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Tabelle */}
      {loading ? (
        <div className="space-y-2">
          {[1,2,3].map(i => <div key={i} className="h-12 bg-muted/20 rounded-lg animate-pulse" />)}
        </div>
      ) : backups.length === 0 ? (
        <div className="bg-card border rounded-lg p-12 text-center space-y-3">
          <Archive className="h-10 w-10 mx-auto text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Noch keine Backups vorhanden.</p>
          <button onClick={handleCreate} disabled={creating}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Plus className="h-4 w-4" />Erstes Backup erstellen
          </button>
        </div>
      ) : (
        <div className="bg-card border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/20 text-xs text-muted-foreground uppercase tracking-wide">
                <th className="px-4 py-2.5 text-left">Backup</th>
                <th className="px-4 py-2.5 text-left">Erstellt</th>
                <th className="px-4 py-2.5 text-right">Größe</th>
                <th className="px-4 py-2.5 text-right">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {backups.map(b => (
                <tr key={b.name} className="hover:bg-muted/10 transition-colors">
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{b.name}</td>
                  <td className="px-4 py-3 text-xs">{fmtDate(b.created_at)}</td>
                  <td className="px-4 py-3 text-xs text-right">{fmtSize(b.size)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {/* Download */}
                      <a href={api.downloadBackupUrl(b.name)}
                        className="p-1.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
                        title="Herunterladen" download>
                        <Download className="h-3.5 w-3.5" />
                      </a>

                      {/* Restore */}
                      {confirmRes === b.name ? (
                        <span className="flex items-center gap-1 text-xs">
                          <span className="text-orange-500">Wirklich?</span>
                          <button onClick={() => handleRestore(b.name)} disabled={restoring === b.name}
                            className="px-2 py-0.5 text-xs bg-orange-500 text-white rounded hover:bg-orange-600 disabled:opacity-50">
                            Ja
                          </button>
                          <button onClick={() => setConfirmRes(null)}
                            className="px-2 py-0.5 text-xs border rounded hover:bg-accent">
                            Nein
                          </button>
                        </span>
                      ) : (
                        <button onClick={() => setConfirmRes(b.name)} disabled={!!restoring}
                          title="Wiederherstellen"
                          className="p-1.5 rounded hover:bg-orange-500/10 hover:text-orange-500 transition-colors text-muted-foreground disabled:opacity-50">
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}

                      {/* Delete */}
                      {confirmDel === b.name ? (
                        <span className="flex items-center gap-1 text-xs">
                          <span className="text-destructive">Löschen?</span>
                          <button onClick={() => handleDelete(b.name)} disabled={deleting === b.name}
                            className="px-2 py-0.5 text-xs bg-destructive text-destructive-foreground rounded hover:bg-destructive/90 disabled:opacity-50">
                            Ja
                          </button>
                          <button onClick={() => setConfirmDel(null)}
                            className="px-2 py-0.5 text-xs border rounded hover:bg-accent">
                            Nein
                          </button>
                        </span>
                      ) : (
                        <button onClick={() => setConfirmDel(b.name)} disabled={!!deleting}
                          title="Löschen"
                          className="p-1.5 rounded hover:bg-destructive/10 hover:text-destructive transition-colors text-muted-foreground disabled:opacity-50">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Hinweis Restore */}
      {backups.length > 0 && (
        <div className="flex items-start gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-orange-400" />
          <span>Restore überschreibt Konfiguration und startet OctopOS neu. Laufende Chats werden unterbrochen.</span>
        </div>
      )}
    </div>
  );
}
