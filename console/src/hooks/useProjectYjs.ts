/**
 * useProjectYjs — Collaborative Composer Hook (#554 H6/H7/H10)
 *
 * Öffnet einen WebSocket zum Backend-Yjs-Server (/api/projects/{id}/collab),
 * liefert ein geteiltes Y.Text ("composer") + awareness zurück. Reconnect,
 * Cleanup und Lifecycle managed y-websocket selbst.
 *
 * Verwendung:
 *   const yjs = useProjectYjs(projectId, currentUsername);
 *   if (yjs) useEffect(() => { yjs.ytext.observe(...); ... }, [yjs]);
 *
 * Hook gibt `null` zurück solange kein Projekt aktiv ist (z.B. während
 * Setup-Flows) — Caller muss damit umgehen können.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";

// Deterministic candy hue per username — passt zu den PresenceAvatar-
// Farben oben im ChatShell, damit Avatar-Kreis UND Cursor-Marker im
// Composer dieselbe Farbe pro User haben.
const PRESENCE_HUE_VARS = [
  "--candy-violet",
  "--candy-pink",
  "--candy-cyan",
  "--candy-lime",
  "--candy-amber",
] as const;

export function presenceHueVar(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PRESENCE_HUE_VARS[h % PRESENCE_HUE_VARS.length];
}

/**
 * Liefert die aktuellen awareness-User-Namen (alle inklusive self) als
 * stable-sorted Array. Für die Header-Presence-Leiste (#554 H12, schließt
 * #730 I). Deduplicated — mehrere Tabs eines Users zählen nur einmal.
 */
export function useAwarenessUsers(yjs: ProjectYjs | null): string[] {
  const [users, setUsers] = useState<string[]>([]);
  useEffect(() => {
    if (!yjs) {
      setUsers([]);
      return;
    }
    const aw = yjs.awareness;
    const update = () => {
      const names = new Set<string>();
      aw.getStates().forEach((state) => {
        const name = (state?.user as { name?: string } | undefined)?.name;
        if (name) names.add(name);
      });
      setUsers(Array.from(names).sort());
    };
    update();
    aw.on("change", update);
    return () => {
      aw.off("change", update);
    };
  }, [yjs]);
  return users;
}

export type ProjectYjs = {
  ydoc: Y.Doc;
  ytext: Y.Text;
  provider: WebsocketProvider;
  awareness: WebsocketProvider["awareness"];
  connected: boolean;
  /** Alles lokal leeren + als Update broadcasten. Nutzen wir nach Send. */
  clearText: () => void;
};

function getAuthToken(): string {
  try {
    return localStorage.getItem("hydrahive_token") || "";
  } catch {
    return "";
  }
}

function buildServerUrl(projectId: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const base = origin.replace(/^http/, "ws");
  return `${base}/api/projects/${encodeURIComponent(projectId)}`;
}

export function useProjectYjs(projectId: string | undefined, username: string | undefined): ProjectYjs | null {
  const [connected, setConnected] = useState(false);
  const tokenRef = useRef<string>(getAuthToken());
  const docAndProvider = useMemo(() => {
    if (!projectId) return null;
    const token = tokenRef.current || getAuthToken();
    if (!token) return null;
    const ydoc = new Y.Doc();
    const ytext = ydoc.getText("composer");
    // y-websocket bildet die URL als `${serverUrl}/${roomname}?${params}`.
    // Unser Backend-Endpoint ist /api/projects/{id}/collab, daher:
    //   serverUrl = wss://.../api/projects/{id}
    //   roomname  = "collab"
    // Resultat: wss://.../api/projects/{id}/collab?token=...
    // Innerhalb des FastAPI-Endpoints setzen wir channel.path = project_id,
    // daraus wird der pycrdt-Roomname → ein SQLiteYStore pro Projekt.
    const provider = new WebsocketProvider(
      buildServerUrl(projectId),
      "collab",
      ydoc,
      {
        connect: true,
        params: { token },
      },
    );
    return { ydoc, ytext, provider };
  }, [projectId]);

  useEffect(() => {
    if (!docAndProvider) return;
    const { provider, ydoc } = docAndProvider;
    const onStatus = (evt: { status: "connected" | "disconnected" | "connecting" }) => {
      setConnected(evt.status === "connected");
    };
    provider.on("status", onStatus);
    if (username) {
      // Awareness-Identität + deterministische Candy-Hue-Variable setzen.
      // Cursor-Marker + Header-Avatar lesen daraus die Farbe; so matchen die
      // beiden Darstellungen per User (#730 I + #554 H10).
      provider.awareness.setLocalStateField("user", {
        name: username,
        hue: presenceHueVar(username),
      });
    }
    return () => {
      provider.off("status", onStatus);
      try {
        provider.awareness.setLocalState(null);
      } catch { /* already gone */ }
      provider.destroy();
      ydoc.destroy();
    };
  }, [docAndProvider, username]);

  if (!docAndProvider) return null;
  const { ydoc, ytext, provider } = docAndProvider;

  const clearText = () => {
    // transaction-wrap, damit Delete + Notify atomar laufen.
    ydoc.transact(() => {
      ytext.delete(0, ytext.length);
    });
  };

  return {
    ydoc,
    ytext,
    provider,
    awareness: provider.awareness,
    connected,
    clearText,
  };
}
