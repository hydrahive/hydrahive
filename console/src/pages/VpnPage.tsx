import { useEffect, useState } from "react";
import { Network, Wifi, WifiOff, RefreshCw, Key, Power, PowerOff, Copy, Check } from "lucide-react";
import { api, VpnStatus, VpnPeer } from "@/lib/api";

function StatusBadge({ connected, state }: { connected: boolean; state?: string }) {
  if (connected) return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-500/20 text-green-400 border border-green-500/30">
      <Wifi size={11} /> Verbunden
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-700 text-zinc-400 border border-zinc-600">
      <WifiOff size={11} /> {state || "Getrennt"}
    </span>
  );
}

function PeerRow({ peer }: { peer: VpnPeer }) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-zinc-800/60 border border-zinc-700/50">
      <div className="flex items-center gap-3">
        <span className={`w-2 h-2 rounded-full ${peer.online ? "bg-green-400" : "bg-zinc-600"}`} />
        <div>
          <div className="text-sm font-medium text-zinc-200">{peer.hostname}</div>
          <div className="text-xs text-zinc-500">{peer.ip} · {peer.os}</div>
        </div>
      </div>
      <span className={`text-xs ${peer.online ? "text-green-400" : "text-zinc-500"}`}>
        {peer.online ? "Online" : "Offline"}
      </span>
    </div>
  );
}

export function VpnPage() {
  const [status,      setStatus]      = useState<VpnStatus | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState("");
  const [authKey,     setAuthKey]     = useState("");
  const [loginServer, setLoginServer] = useState("");
  const [hostname,    setHostname]    = useState("");
  const [connecting,  setConnecting]  = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [hsKey,       setHsKey]       = useState("");
  const [hsKeyCopied, setHsKeyCopied] = useState(false);
  const [genKey,      setGenKey]      = useState(false);
  const [refreshing,  setRefreshing]  = useState(false);

  async function load() {
    try {
      const d = await api.vpnStatus();
      setStatus(d);
      if (d.login_server && d.login_server !== "https://controlplane.tailscale.com") {
        setLoginServer(d.login_server);
      }
      if (d.hostname) setHostname(d.hostname);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Laden");
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }

  async function handleConnect() {
    setConnecting(true); setError("");
    try {
      await api.vpnConnect({
        auth_key: authKey.trim() || undefined,
        login_server: loginServer.trim() || undefined,
        hostname: hostname.trim() || undefined,
      });
      setAuthKey("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verbindungsfehler");
    } finally { setConnecting(false); }
  }

  async function handleDisconnect() {
    setDisconnecting(true); setError("");
    try {
      await api.vpnDown();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Trennen");
    } finally { setDisconnecting(false); }
  }

  async function handleGenKey() {
    setGenKey(true); setError("");
    try {
      const d = await api.vpnHeadscaleAuthkey();
      setHsKey(d.auth_key);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Auth-Key Fehler");
    } finally { setGenKey(false); }
  }

  function copyKey() {
    navigator.clipboard.writeText(hsKey);
    setHsKeyCopied(true);
    setTimeout(() => setHsKeyCopied(false), 2000);
  }

  if (loading) return (
    <div className="p-6 text-zinc-500 text-sm">Lade VPN-Status...</div>
  );

  const notInstalled = !status || status.mode === "none";
  const isHeadscale  = status?.mode === "headscale";

  return (
    <div className="p-6 max-w-2xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Network size={22} className="text-blue-400" />
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">VPN</h1>
            <p className="text-xs text-zinc-500">
              {isHeadscale ? "Headscale (self-hosted)" : "Tailscale"}
            </p>
          </div>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="p-1.5 rounded-lg hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors">
          <RefreshCw size={15} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">{error}</div>
      )}

      {/* Status Card */}
      <div className="rounded-xl border border-zinc-700/60 bg-zinc-900/60 divide-y divide-zinc-700/40">
        <div className="p-4 flex items-center justify-between">
          <span className="text-sm text-zinc-400">Status</span>
          {notInstalled
            ? <span className="text-xs text-zinc-500">Nicht installiert — Installer erneut ausführen</span>
            : <StatusBadge connected={status.connected} state={status.backend_state} />
          }
        </div>
        {status?.tailscale_ip && (
          <div className="p-4 flex items-center justify-between">
            <span className="text-sm text-zinc-400">Tailscale-IP</span>
            <span className="text-sm font-mono text-zinc-200">{status.tailscale_ip}</span>
          </div>
        )}
        {isHeadscale && (
          <div className="p-4 flex items-center justify-between">
            <span className="text-sm text-zinc-400">Headscale</span>
            <span className={`text-xs ${status.headscale_running ? "text-green-400" : "text-red-400"}`}>
              {status.headscale_running ? "läuft" : "gestoppt"}
            </span>
          </div>
        )}
        {status?.login_server && (
          <div className="p-4 flex items-center justify-between">
            <span className="text-sm text-zinc-400">Koordinator</span>
            <span className="text-xs font-mono text-zinc-400 truncate max-w-xs">{status.login_server}</span>
          </div>
        )}
      </div>

      {/* Connect / Disconnect */}
      {!notInstalled && (
        <div className="rounded-xl border border-zinc-700/60 bg-zinc-900/60 p-4 space-y-3">
          <h2 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
            <Key size={14} /> Auth-Key
          </h2>

          {isHeadscale && (
            <p className="text-xs text-zinc-500">
              Headscale-Modus: Auth-Key über den Button unten generieren oder
              manuell mit <code className="bg-zinc-800 px-1 rounded">headscale preauthkeys create</code> erstellen.
            </p>
          )}
          {!isHeadscale && (
            <p className="text-xs text-zinc-500">
              Auth-Key in den{" "}
              <a href="https://login.tailscale.com/admin/settings/keys" target="_blank" rel="noreferrer"
                className="text-blue-400 hover:underline">Tailscale Admin-Einstellungen</a>{" "}
              generieren und hier einfügen.
            </p>
          )}

          {status?.configured && !status?.connected && (
            <p className="text-xs text-amber-400/80 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              Tailscale ist gestoppt aber bereits konfiguriert — einfach "Verbinden" klicken ohne neuen Key.
            </p>
          )}
          <input
            type="password"
            value={authKey}
            onChange={e => setAuthKey(e.target.value)}
            placeholder={status?.configured ? "Leer lassen für Reconnect, oder neuen Key eingeben" : "tskey-auth-..."}
            className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
          />

          {isHeadscale && (
            <input
              type="text"
              value={loginServer}
              onChange={e => setLoginServer(e.target.value)}
              placeholder="http://YOUR-VM-IP:8089"
              className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
            />
          )}

          <input
            type="text"
            value={hostname}
            onChange={e => setHostname(e.target.value)}
            placeholder={`Hostname (optional, z.B. hydrahive-server)`}
            className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
          />

          <div className="flex gap-2">
            <button onClick={handleConnect} disabled={connecting}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors">
              <Power size={14} />
              {connecting ? "Verbinde..." : "Verbinden"}
            </button>
            {status?.connected && (
              <button onClick={handleDisconnect} disabled={disconnecting}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-zinc-200 text-sm font-medium transition-colors">
                <PowerOff size={14} />
                {disconnecting ? "Trenne..." : "Trennen"}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Headscale: Auth-Key für neue Nodes generieren */}
      {isHeadscale && status?.connected && (
        <div className="rounded-xl border border-zinc-700/60 bg-zinc-900/60 p-4 space-y-3">
          <h2 className="text-sm font-medium text-zinc-300">Neuen Node einladen</h2>
          <p className="text-xs text-zinc-500">
            Generiert einen wiederverwendbaren Auth-Key (90 Tage) für weitere HydraHive-Nodes.
          </p>
          <button onClick={handleGenKey} disabled={genKey}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-zinc-200 text-sm transition-colors">
            <Key size={13} />
            {genKey ? "Generiere..." : "Auth-Key generieren"}
          </button>
          {hsKey && (
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 rounded-lg bg-zinc-800 text-xs text-zinc-300 font-mono truncate border border-zinc-700">
                {hsKey}
              </code>
              <button onClick={copyKey}
                className="p-2 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-colors">
                {hsKeyCopied ? <Check size={13} className="text-green-400" /> : <Copy size={13} />}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Peers */}
      {status?.connected && (status.peers?.length ?? 0) > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-400">
            Verbundene Peers ({status.peers!.length})
          </h2>
          <div className="space-y-1.5">
            {status.peers!.map(p => <PeerRow key={p.id} peer={p} />)}
          </div>
        </div>
      )}

      {status?.connected && (status.peers?.length ?? 0) === 0 && (
        <div className="p-4 rounded-xl border border-zinc-700/40 bg-zinc-900/40 text-center">
          <p className="text-sm text-zinc-500">Keine weiteren Nodes verbunden</p>
          <p className="text-xs text-zinc-600 mt-1">
            {isHeadscale
              ? "Auth-Key generieren und auf weiteren HydraHive-Instanzen eingeben"
              : "Weitere Nodes mit demselben Tailscale-Account verbinden"}
          </p>
        </div>
      )}
    </div>
  );
}
