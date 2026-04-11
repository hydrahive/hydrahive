/**
 * useProjectSubscribe — SSE-basierter Subscribe-Hook fuer Projekt-Events (#553)
 *
 * Verbindet sich per GET fetch + ReadableStream mit /api/projects/{id}/subscribe.
 * Parst _typing, _presence, _turn Events und stellt sie als React-State bereit.
 * Reconnect mit exponentiellem Backoff bei Verbindungsverlust.
 */
import { useCallback, useEffect, useRef, useState } from "react";

// ── Types ──────────────────────────────────────────────────────────────────

export interface SubscribeState {
  /** Users die gerade tippen (username -> true) */
  typingUsers: Map<string, boolean>;
  /** Online-User im Projekt */
  onlineUsers: string[];
  /** Wer hat gerade den Turn? */
  turnOwner: string | null;
  /** Ist die SSE-Verbindung aktiv? */
  isConnected: boolean;
}

// ── Stale-Timeout fuer Typing ──────────────────────────────────────────────

const TYPING_STALE_MS = 4_000;  // 4s ohne Update -> nicht mehr "tippt"
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

// ── Hook ───────────────────────────────────────────────────────────────────

export function useProjectSubscribe(projectId: string | undefined): SubscribeState {
  const [typingUsers, setTypingUsers] = useState<Map<string, boolean>>(() => new Map());
  const [onlineUsers, setOnlineUsers] = useState<string[]>([]);
  const [turnOwner, setTurnOwner] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  // Typing-Stale-Timers pro User
  const typingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // Abort-Controller fuer Cleanup
  const abortRef = useRef<AbortController | null>(null);
  // Reconnect-Versuch
  const retryCount = useRef(0);
  const mountedRef = useRef(true);

  const clearTypingTimer = useCallback((user: string) => {
    const existing = typingTimers.current.get(user);
    if (existing) {
      clearTimeout(existing);
      typingTimers.current.delete(user);
    }
  }, []);

  const handleTypingEvent = useCallback((user: string, active: boolean) => {
    clearTypingTimer(user);

    if (active) {
      // Stale-Timer: nach 4s automatisch auf false setzen
      const timer = setTimeout(() => {
        if (!mountedRef.current) return;
        setTypingUsers(prev => {
          const next = new Map(prev);
          next.delete(user);
          return next;
        });
        typingTimers.current.delete(user);
      }, TYPING_STALE_MS);
      typingTimers.current.set(user, timer);

      setTypingUsers(prev => {
        const next = new Map(prev);
        next.set(user, true);
        return next;
      });
    } else {
      setTypingUsers(prev => {
        const next = new Map(prev);
        next.delete(user);
        return next;
      });
    }
  }, [clearTypingTimer]);

  useEffect(() => {
    if (!projectId) return;
    mountedRef.current = true;

    let cancelled = false;

    async function connect() {
      const token = localStorage.getItem("hydrahive_token") || "";
      if (!token || cancelled) return;

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch(`/api/projects/${projectId}/subscribe`, {
          method: "GET",
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });

        if (!res.ok) {
          // Permanente Fehler: kein Retry
          if (res.status === 401 || res.status === 403 || res.status === 404) return;
          throw new Error(`HTTP ${res.status}`);
        }

        setIsConnected(true);
        retryCount.current = 0;

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done || cancelled) break;

          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() ?? "";

          for (const part of parts) {
            for (const line of part.split("\n")) {
              if (!line.startsWith("data: ")) continue;
              try {
                const raw = JSON.parse(line.slice(6));

                if (raw._typing) {
                  handleTypingEvent(raw._typing.user, raw._typing.active);
                } else if (raw._presence) {
                  setOnlineUsers(raw._presence.users || []);
                } else if (raw._turn !== undefined) {
                  setTurnOwner(raw._turn?.owner ?? null);
                }
                // Regulaere Stream-Events (text, tool_call etc.) ignorieren wir hier —
                // die kommen ueber den eigenen /message/stream Kanal
              } catch {
                // JSON-Parse-Fehler bei Keepalive-Comments ignorieren
              }
            }
          }
        }
      } catch (err: unknown) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
      } finally {
        setIsConnected(false);
      }

      // Reconnect mit exponentiellem Backoff
      if (!cancelled && mountedRef.current) {
        const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, retryCount.current), RECONNECT_MAX_MS);
        retryCount.current++;
        await new Promise(r => setTimeout(r, delay));
        if (!cancelled && mountedRef.current) connect();
      }
    }

    connect();

    return () => {
      cancelled = true;
      mountedRef.current = false;
      abortRef.current?.abort();
      // Alle Typing-Timer aufraeumen
      for (const timer of typingTimers.current.values()) {
        clearTimeout(timer);
      }
      typingTimers.current.clear();
    };
  }, [projectId, handleTypingEvent]);

  return { typingUsers, onlineUsers, turnOwner, isConnected };
}
