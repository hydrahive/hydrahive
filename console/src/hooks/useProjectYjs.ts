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
import { useEffect, useMemo, useState } from "react";
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
      setUsers((prev) => (prev.length === 0 ? prev : []));
      return;
    }
    const aw = yjs.awareness;
    const update = () => {
      const names = new Set<string>();
      aw.getStates().forEach((state) => {
        const name = (state?.user as { name?: string } | undefined)?.name;
        if (name) names.add(name);
      });
      const next = Array.from(names).sort();
      // Nur setState wenn sich die User-Liste wirklich geändert hat — awareness
      // feuert auch für Cursor-Moves, das soll KEIN Re-Render auslösen.
      setUsers((prev) => {
        if (prev.length !== next.length) return next;
        for (let i = 0; i < prev.length; i++) if (prev[i] !== next[i]) return next;
        return prev;
      });
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

function buildServerUrl(projectId: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const base = origin.replace(/^http/, "ws");
  return `${base}/api/projects/${encodeURIComponent(projectId)}`;
}

export function useProjectYjs(projectId: string | undefined, username: string | undefined): ProjectYjs | null {
  const [connected, setConnected] = useState(false);

  // #766: Kein expliziter Token-Fetch mehr — der Browser sendet das httpOnly
  // AUTH_COOKIE_NAME Cookie automatisch im WS-Handshake (gleicher Origin).
  // Backend liest den Cookie in collab_ws() und validiert das JWT.
  // useMemo ohne wsToken-dep — wsToken/Logout-Thematik entfällt damit.

  const docAndProvider = useMemo(() => {
    if (!projectId || !username) return null;
    const ydoc = new Y.Doc();
    const ytext = ydoc.getText("composer");
    // y-websocket baut die URL als `${serverUrl}/${roomname}`.
    // Unser Backend-Endpoint ist /api/projects/{id}/collab, daher:
    //   serverUrl = wss://.../api/projects/{id}
    //   roomname  = "collab"
    // Cookie wird vom Browser automatisch im WS-Handshake gesendet.
    const provider = new WebsocketProvider(
      buildServerUrl(projectId),
      "collab",
      ydoc,
      {
        connect: true,
        // #554: Cross-Browser-Sync muss über den HydraHive/pycrdt-Server
        // laufen. y-websocket nutzt sonst im selben Browser zusätzlich
        // BroadcastChannel und kann Backend-Probleme verdecken.
        disableBc: true,
        // #554: 2s Server-Resync ist Pflicht — ohne das fehlen Cross-Browser
        // ydoc-Updates (awareness kommt durch, aber Text nicht). Scroll-Jump-
        // Ursachen (autoFocus, dynamischer Placeholder) sind in ddf18a1
        // separat gefixt; der resyncInterval löst keinen Scroll-Jump mehr aus.
        resyncInterval: 2000,
      },
    );
    // Diagnostik (#554): Sync-Events laut rauslog damit wir bei Sync-
    // Ausfällen sehen was zwischen Client und Server passiert.
    provider.on("status", (evt: { status: string }) => {
      console.debug(`[collab/${projectId}] ws`, evt.status);
    });
    provider.on("sync", (isSynced: boolean) => {
      console.debug(`[collab/${projectId}] synced`, isSynced);
    });
    provider.on("connection-error", (err: Event) => {
      console.warn(`[collab/${projectId}] connection-error`, err);
    });
    provider.on("connection-close", (evt: CloseEvent | null) => {
      console.warn(`[collab/${projectId}] connection-close`, evt?.code, evt?.reason);
    });
    ydoc.on("update", (_update: Uint8Array, origin: unknown) => {
      console.debug(`[collab/${projectId}] ydoc update origin=`, origin);
    });
    return { ydoc, ytext, provider };
  }, [projectId, username]);

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

  // Return-Wert memoizieren damit Consumer (ChatShell, Page) stabile
  // Referenzen bekommen — sonst fliegt jede Parent-Re-Render-Kaskade los
  // und kann in einen Loop laufen.
  return useMemo<ProjectYjs | null>(() => {
    if (!docAndProvider) return null;
    const { ydoc, ytext, provider } = docAndProvider;
    return {
      ydoc,
      ytext,
      provider,
      awareness: provider.awareness,
      connected,
      clearText: () => {
        // #769 fix: origin="client-clear" damit der y-websocket die Änderung
        // als Broadcasting erkennt und nicht als echo der eigenen Mutation.
        // Ohne expliziten Origin verwirft pycrdt die Nachricht auf Senderseite
        // (sie kommt nicht im Awareness-Update an → andere Clients sehen
        // das geleerte Feld nicht).
        ydoc.transact(() => {
          ytext.delete(0, ytext.length);
        }, "client-clear");
      },
    };
  }, [docAndProvider, connected]);
}