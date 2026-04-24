import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, AlertCircle, Monitor, Loader2 } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

interface VNCInfo {
  vm_id: string;
  websocket_url: string;
  token: string;
  vnc_port: number;
  websockify_ok: boolean;
}

interface VMInfo {
  vm_id: string;
  name: string;
  status: string;
}

export function VNCConsolePage() {
  const { vm_id } = useParams<{ vm_id: string }>();
  const { user } = useAuth();
  const token = user?.token ?? "";

  const [vncInfo, setVncInfo] = useState<VNCInfo | null>(null);
  const [vm, setVm] = useState<VMInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!vm_id) return;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        // Load VM info
        const vmRes = await fetch(`/api/admin/vms/${vm_id}`, { headers: { Authorization: `Bearer ${token}` } });
        if (!vmRes.ok) {
          const d = await vmRes.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${vmRes.status}`);
        }
        const vmData = await vmRes.json();
        setVm(vmData as VMInfo);

        // Load VNC info
        const vncRes = await fetch(`/api/admin/vms/${vm_id}/vnc`, { headers: { Authorization: `Bearer ${token}` } });
        if (!vncRes.ok) {
          const d = await vncRes.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${vncRes.status}`);
        }
        const vncData = await vncRes.json();
        setVncInfo(vncData as VNCInfo);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [vm_id, token]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen text-muted-foreground">
        <Loader2 className="w-8 h-8 animate-spin mr-3" />
        Lade VNC-Informationen…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 max-w-md mx-auto text-center space-y-4">
        <AlertCircle className="w-12 h-12 mx-auto text-red-500" />
        <h2 className="text-xl font-semibold">Fehler beim Laden</h2>
        <p className="text-muted-foreground">{error}</p>
        <Link to="/vms" className="btn btn-primary inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> Zurück zu VMs
        </Link>
      </div>
    );
  }

  if (!vm || !vncInfo) return null;

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <div className="border-b px-6 py-4 flex items-center justify-between bg-card">
        <div className="flex items-center gap-4">
          <Link to="/vms" className="btn btn-sm flex items-center gap-1.5 text-muted-foreground hover:text-foreground">
            <ArrowLeft className="w-4 h-4" /> Zurück
          </Link>
          <div className="flex items-center gap-2">
            <Monitor className="w-5 h-5" />
            <span className="font-semibold">{vm.name}</span>
            <span className={`status-pill ${vm.status === "running" ? "status-pill-ok" : ""}`}>
              {vm.status}
            </span>
          </div>
        </div>
        {!vncInfo.websockify_ok && (
          <div className="flex items-center gap-2 text-xs text-yellow-600 dark:text-yellow-400">
            <AlertCircle className="w-4 h-4" />
            websockify nicht erreichbar (Port 6080 prüfen)
          </div>
        )}
      </div>

      {/* VNC Canvas Placeholder — #903 */}
      <div className="flex-1 flex items-center justify-center bg-gray-900 text-white">
        <div className="text-center space-y-4 max-w-md p-8">
          <Monitor className="w-16 h-16 mx-auto opacity-40" />
          <h2 className="text-xl font-semibold">VNC-Konsole — Platzhalter</h2>
          <p className="text-gray-400 text-sm">
            noVNC wird in <strong>#903</strong> implementiert.
          </p>
          <div className="bg-gray-800 rounded-lg p-4 text-left text-xs font-mono space-y-1">
            <div className="text-gray-400">WebSocket URL:</div>
            <div className="text-green-400 break-all">{vncInfo.websocket_url}</div>
            <div className="text-gray-400 mt-2">Token:</div>
            <div className="text-green-400">{vncInfo.token}</div>
            <div className="text-gray-400 mt-2">VNC Port:</div>
            <div className="text-green-400">{vncInfo.vnc_port}</div>
            <div className="text-gray-400 mt-2">websockify:</div>
            <div className={vncInfo.websockify_ok ? "text-green-400" : "text-yellow-400"}>
              {vncInfo.websockify_ok ? "OK" : "NICHT ERREICHBAR"}
            </div>
          </div>
          <p className="text-gray-500 text-xs">
            Port 6080 muss erreichbar sein und noVNC muss als Abhängigkeit installiert werden.
          </p>
        </div>
      </div>
    </div>
  );
}
