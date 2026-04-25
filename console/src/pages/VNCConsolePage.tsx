import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, AlertCircle, Monitor, Loader2,
  Maximize2, Keyboard, Wifi, WifiOff, Play, X,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

// noVNC types — RFB instance for browser VNC connections
type RFBInstance = InstanceType<typeof import("@novnc/novnc/lib/rfb").RFB>;

// ── Types ──────────────────────────────────────────────────────────────────────
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

// ── VNCCanvas ──────────────────────────────────────────────────────────────────
interface VNCCanvasProps {
  wsUrl: string;
  onDisconnect: () => void;
  onConnect: () => void;
}

function VNCCanvas({ wsUrl, onDisconnect, onConnect }: VNCCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFBInstance | null>(null);

  useEffect(() => {
    if (!containerRef.current || !wsUrl) return;

    // RFB is the default export of @novnc/novnc
    import("@novnc/novnc/lib/rfb").then((mod) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const RFB = (mod.default ?? mod) as any;
      if (!containerRef.current || !RFB) return;

      const rfb = new RFB(containerRef.current, wsUrl, {
        scaleViewport: true,
        resizeSession: true,
        credentials: { password: "" },
      } as Record<string, unknown>);

      rfbRef.current = rfb as RFBInstance;

      rfb.addEventListener("connect", () => {
        onConnect();
      });

      rfb.addEventListener("disconnect", () => {
        rfbRef.current = null;
        onDisconnect();
      });

      // Listen for Ctrl+Alt+Del events from toolbar
      const handleCtrlAltDel = () => rfb.sendCtrlAltDel();
      window.addEventListener("vnc-ctrl-alt-del", handleCtrlAltDel);
      (rfbRef as React.MutableRefObject<RFBInstance | null>).current = rfb as RFBInstance;
    });

    return () => {
      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }
    };
  }, [wsUrl, onDisconnect, onConnect]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full"
      style={{
        background: "#1a1a1a",
        overflow: "hidden",
      }}
    />
  );
}

// ── Connection States ─────────────────────────────────────────────────────────
type ConnState = "loading" | "error" | "not_running" | "connected" | "disconnected";

// ── VNCConsolePage ─────────────────────────────────────────────────────────────
export function VNCConsolePage() {
  const { vm_id } = useParams<{ vm_id: string }>();
  const navigate = useNavigate();



  const [connState, setConnState] = useState<ConnState>("loading");
  const [vm, setVm] = useState<VMInfo | null>(null);
  const [vncInfo, setVncInfo] = useState<VNCInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disconnectReason, setDisconnectReason] = useState<string | null>(null);

  // Load data on mount
  useEffect(() => {
    if (!vm_id) return;

    async function load() {
      setConnState("loading");
      setError(null);

      try {
        // Fetch VM info
        const vmRes = await fetch(`/api/admin/vms/${vm_id}`, {
          credentials: "include",
        });
        if (!vmRes.ok) {
          const d = await vmRes.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${vmRes.status}`);
        }
        const vmData = await vmRes.json();
        setVm(vmData as VMInfo);

        // VM not running → show not_running state
        if ((vmData as VMInfo).status !== "running") {
          setConnState("not_running");
          return;
        }

        // Fetch VNC info
        const vncRes = await fetch(`/api/admin/vms/${vm_id}/vnc`, {
          credentials: "include",
        });
        if (!vncRes.ok) {
          const d = await vncRes.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${vncRes.status}`);
        }
        const vncData = await vncRes.json();
        setVncInfo(vncData as VNCInfo);

        // Will transition to "connected" via VNCCanvas onConnect event
        setConnState("connected");
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        setConnState("error");
      }
    }

    load();
  }, [vm_id]);

  // Update VM status while viewing (poll every 10s for running state)
  useEffect(() => {
    if (!vm_id || connState !== "connected") return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/admin/vms/${vm_id}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json() as VMInfo;
          if (data.status !== "running") {
            setDisconnectReason("VM wurde gestoppt oder ist nicht mehr aktiv");
            setConnState("disconnected");
          }
        }
      } catch { /* ignore poll errors */ }
    }, 10_000);
    return () => clearInterval(interval);
  }, [vm_id, connState]);

  function handleDisconnect() {
    setDisconnectReason("Verbindung zum VNC-Server verloren");
    setConnState("disconnected");
  }

  function handleConnect() {
    setDisconnectReason(null);
    setConnState("connected");
  }

  async function handleStart() {
    if (!vm_id) return;
    try {
      const res = await fetch(`/api/admin/vms/${vm_id}/start`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      // Reload to get fresh vnc info
      const vmRes = await fetch(`/api/admin/vms/${vm_id}`, {
        credentials: "include",
      });
      const vmData = await vmRes.json() as VMInfo;
      setVm(vmData);
      if (vmData.status === "running") {
        const vncRes = await fetch(`/api/admin/vms/${vm_id}/vnc`, {
          credentials: "include",
        });
        const vncData = await vncRes.json() as VNCInfo;
        setVncInfo(vncData);
        setConnState("connected");
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ── Render States ────────────────────────────────────────────────────────────

  if (connState === "loading") {
    return (
      <div className="flex flex-col h-screen bg-gray-950 text-white">
        <div className="flex items-center justify-center flex-1 gap-3">
          <Loader2 className="w-7 h-7 animate-spin" />
          <span className="text-gray-400">Verbinde mit VNC-Server…</span>
        </div>
      </div>
    );
  }

  if (connState === "error" || connState === "not_running") {
    return (
      <div className="flex flex-col h-screen bg-gray-950 text-white p-8">
        {/* Toolbar */}
        <div className="flex items-center gap-4 mb-8">
          <button
            onClick={() => navigate("/vms")}
            className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> VMs
          </button>
          {vm && (
            <>
              <span className="text-gray-600">|</span>
              <Monitor className="w-4 h-4 text-gray-400" />
              <span className="font-medium">{vm.name}</span>
            </>
          )}
        </div>

        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-4 max-w-md">
            <div className="flex items-center justify-center w-16 h-16 rounded-full bg-gray-800 mx-auto">
              <AlertCircle className="w-8 h-8 text-red-400" />
            </div>
            <h2 className="text-xl font-semibold">
              {connState === "not_running" ? "VM läuft nicht" : "Fehler"}
            </h2>
            <p className="text-gray-400 text-sm">
              {connState === "not_running"
                ? `Die VM "${vm?.name ?? vm_id}" ist aktuell gestoppt.`
                : error ?? "Unbekannter Fehler"}
            </p>
            {connState === "not_running" && (
              <button
                className="btn btn-primary flex items-center gap-2 mx-auto"
                onClick={handleStart}
              >
                <Play className="w-4 h-4" /> VM starten
              </button>
            )}
            <button
              onClick={() => navigate("/vms")}
              className="text-sm text-gray-400 hover:text-white flex items-center gap-1 mx-auto"
            >
              <ArrowLeft className="w-3.5 h-3.5" /> Zurück zu VMs
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (connState === "disconnected") {
    return (
      <div className="flex flex-col h-screen bg-gray-950 text-white p-8">
        <div className="flex items-center gap-4 mb-8">
          <button onClick={() => navigate("/vms")} className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors">
            <ArrowLeft className="w-4 h-4" /> VMs
          </button>
          {vm && (
            <>
              <span className="text-gray-600">|</span>
              <Monitor className="w-4 h-4 text-gray-400" />
              <span className="font-medium">{vm.name}</span>
            </>
          )}
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-4 max-w-md">
            <div className="flex items-center justify-center w-16 h-16 rounded-full bg-gray-800 mx-auto">
              <WifiOff className="w-8 h-8 text-yellow-400" />
            </div>
            <h2 className="text-xl font-semibold">VNC-Verbindung getrennt</h2>
            <p className="text-gray-400 text-sm">{disconnectReason ?? "Die Verbindung zum VNC-Server wurde getrennt."}</p>
            <button onClick={() => navigate("/vms")} className="text-sm text-gray-400 hover:text-white">
              ← Zurück zu VMs
            </button>
          </div>
        </div>
      </div>
    );
  }

  // connState === "connected"
  if (!vncInfo) return null;

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-white overflow-hidden">
      {/* ── Toolbar ── */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate("/vms")}
            className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> VMs
          </button>
          <span className="text-gray-600">|</span>
          <Monitor className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-medium">{vm?.name}</span>
          <span className="flex items-center gap-1.5 text-xs">
            <span className="w-2 h-2 rounded-full bg-green-500" />
            <span className="text-green-400">Verbunden</span>
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* websockify warning */}
          {!vncInfo.websockify_ok && (
            <span className="flex items-center gap-1.5 text-xs text-yellow-400 mr-2">
              <AlertCircle className="w-3.5 h-3.5" /> websockify prüfen
            </span>
          )}

          {/* Ctrl+Alt+Del */}
          <button
            className="btn btn-sm flex items-center gap-1.5"
            onClick={() => {
              // Access rfb via the VNCCanvas ref — emit via custom event
              window.dispatchEvent(new CustomEvent("vnc-ctrl-alt-del"));
            }}
            title="Ctrl+Alt+Del an VM senden"
          >
            <Keyboard className="w-4 h-4" /> Ctrl+Alt+Del
          </button>

          {/* Vollbild */}
          <button
            className="btn btn-sm flex items-center gap-1.5"
            onClick={() => document.documentElement.requestFullscreen?.()}
            title="Vollbild"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ── VNC Canvas ── */}
      <div className="flex-1 relative overflow-hidden" id="vnc-container">
        <VNCCanvas
          wsUrl={vncInfo.websocket_url}
          onDisconnect={handleDisconnect}
          onConnect={handleConnect}
        />
      </div>
    </div>
  );
}
