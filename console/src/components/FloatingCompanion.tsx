/**
 * FloatingCompanion — Easter-Egg Begleiter in der Console.
 * Schwebt unten rechts, kommentiert was der User macht.
 * Aktivierung: 5x auf Version in Settings klicken → localStorage hh_companion=1
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";

type Mood = "idle" | "happy" | "think" | "sleep" | "shock" | "love" | "sad";

/** Animated SVG blob creature — changes expression based on mood */
export function BlobCreature({ mood, size = 48 }: { mood: Mood; size?: number }) {
  const eyes: Record<Mood, string> = {
    idle:  "M16 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm16 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z",
    happy: "M14.5 18.5q1.5-2 3 0m13-0q1.5-2 3 0",
    think: "M16 19a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm16-1a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
    sleep: "M14 18h4m12 0h4",
    shock: "M16 20a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm16 0a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
    love:  "M14 18l2-2 2 2-2 2Zm12 0l2-2 2 2-2 2Z",
    sad:   "M16 20a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm16 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z",
  };
  const mouth: Record<Mood, string> = {
    idle:  "M20 26q4 3 8 0",
    happy: "M18 25q6 6 12 0",
    think: "M21 27h6",
    sleep: "M20 26q4 2 8 0",
    shock: "M22 27a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z",
    love:  "M18 25q6 6 12 0",
    sad:   "M20 28q4-3 8 0",
  };
  const bodyColor = mood === "sleep" ? "#6366f1" : mood === "sad" ? "#8b5cf6" : "#a78bfa";
  const cheekOpacity = mood === "happy" || mood === "love" ? 0.4 : 0;

  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Body — wobbly blob */}
      <ellipse cx="24" cy="26" rx="18" ry="16" fill={bodyColor} opacity="0.9">
        <animate attributeName="ry" values="16;17;16" dur="2s" repeatCount="indefinite" />
        <animate attributeName="rx" values="18;17.5;18" dur="2s" repeatCount="indefinite" />
      </ellipse>
      {/* Cheeks */}
      <circle cx="12" cy="24" r="3" fill="#f472b6" opacity={cheekOpacity} />
      <circle cx="36" cy="24" r="3" fill="#f472b6" opacity={cheekOpacity} />
      {/* Eyes */}
      <path d={eyes[mood]} fill="white" stroke="white" strokeWidth="0.5" />
      {/* Mouth */}
      <path d={mouth[mood]} stroke="white" strokeWidth="1.5" strokeLinecap="round" fill={mood === "shock" ? "white" : "none"} />
      {/* Zzz for sleep */}
      {mood === "sleep" && (
        <text x="36" y="12" fontSize="8" fill="white" opacity="0.6" fontFamily="monospace">
          <animate attributeName="opacity" values="0.6;0.2;0.6" dur="2s" repeatCount="indefinite" />
          z
        </text>
      )}
      {/* Sparkle for love */}
      {mood === "love" && (
        <>
          <text x="4" y="10" fontSize="8" fill="#f472b6">✦</text>
          <text x="38" y="8" fontSize="6" fill="#f472b6">✦</text>
        </>
      )}
    </svg>
  );
}

// Kontext-Prompts je nach Seite (English — LLM übersetzt selbst in die User-Sprache)
const PAGE_CONTEXTS: Record<string, string> = {
  "/dashboard":     "The user is looking at the dashboard.",
  "/my-agent":      "The user is configuring their personal agent.",
  "/agents":        "The user is managing their agents.",
  "/projects":      "The user is working on projects.",
  "/settings":      "The user is in the settings.",
  "/hub":           "The user is browsing Extensions for plugins and skills.",
  "/brain":         "The user is looking at System Knowledge (3D agent network).",
  "/system":        "The user is checking the system status.",
  "/prompt-guide":  "The user is learning to write better prompts.",
  "/blueprint":     "The user is building automations.",
  "/mcp":           "The user is configuring MCP servers.",
};

export function FloatingCompanion() {
  const location = useLocation();
  const [visible, setVisible] = useState(() => localStorage.getItem("hh_companion") === "1");
  const [bubble, setBubble] = useState("");
  const [mood, setMood] = useState<Mood>("idle");
  const [showBubble, setShowBubble] = useState(false);
  const [dockEl, setDockEl] = useState<HTMLElement | null>(null);
  const lastCommentRef = useRef(0);
  const lastPathRef = useRef("");
  const [wander, setWander] = useState({ x: 0, y: 0 });
  const [state, setState] = useState<{
    happy: number; hunger: number; energy: number; is_sleeping: boolean;
    mood: Mood; age_days: number; interactions_total: number;
    pet_count: number; feed_count: number;
  } | null>(null);

  // State vom Server ziehen — authoritative Mood-Quelle
  const fetchState = () => {
    const token = localStorage.getItem("hydrahive_token") || "";
    if (!token) return;
    fetch("/api/agents/personal_admin/tamagotchi/state", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        setState(d);
        // Wenn kein Kommentar gerade aktiv → Server-Mood übernehmen
        if (!showBubble) setMood(d.mood as Mood);
      })
      .catch(() => {});
  };

  useEffect(() => {
    if (!visible) return;
    fetchState();
    const t = setInterval(fetchState, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  // Click = streicheln
  const interact = (kind: "pet" | "feed" | "sleep" | "wake") => {
    const token = localStorage.getItem("hydrahive_token") || "";
    if (!token) return;
    fetch("/api/agents/personal_admin/tamagotchi/interact", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ kind }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        setState(d);
        setMood(d.mood as Mood);
        if (kind === "pet") {
          setBubble("♥");
          setShowBubble(true);
          setTimeout(() => setShowBubble(false), 1500);
        } else if (kind === "feed") {
          setBubble("*nom nom* 🍪");
          setShowBubble(true);
          setTimeout(() => setShowBubble(false), 2500);
        }
      })
      .catch(() => {});
  };

  // Zufälliges Wandern — alle 2-4s neue Position
  useEffect(() => {
    if (!visible) return;
    const move = () => {
      if (mood === "sleep") { setWander({ x: 0, y: 0 }); return; }
      setWander({
        x: Math.round((Math.random() - 0.5) * 16),
        y: Math.round((Math.random() - 0.5) * 12),
      });
    };
    move();
    const t = setInterval(move, 2000 + Math.random() * 2000);
    return () => clearInterval(t);
  }, [visible, mood]);

  // Check activation
  useEffect(() => {
    setVisible(localStorage.getItem("hh_companion") === "1");
    const handler = () => setVisible(localStorage.getItem("hh_companion") === "1");
    window.addEventListener("storage", handler);
    window.addEventListener("hh-companion-toggle", handler);
    return () => { window.removeEventListener("storage", handler); window.removeEventListener("hh-companion-toggle", handler); };
  }, []);

  // Dock-Element suchen — nach Toggle braucht AdminLayout einen Render-Zyklus
  useEffect(() => {
    if (!visible) { setDockEl(null); return; }
    const find = () => setDockEl(document.getElementById("companion-dock"));
    find();
    // Falls Dock noch nicht da: kurz warten bis AdminLayout gerendert hat
    const t1 = setTimeout(find, 50);
    const t2 = setTimeout(find, 200);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [visible]);

  // Zentraler Kommentar-Trigger
  function triggerComment(context: string) {
    const now = Date.now();
    if (now - lastCommentRef.current < 30000) return; // Throttle 30s
    lastCommentRef.current = now;

    setMood("think");

    const token = localStorage.getItem("hydrahive_token") || "";
    const lang = document.documentElement.lang || (navigator.language?.startsWith("de") ? "de" : "en");
    fetch("/api/agents/personal_admin/tamagotchi", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ context, lang }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.comment) {
          const text = d.comment.slice(0, 80);
          setBubble(text);
          setShowBubble(true);
          if (text.match(/[♥❤😍🥰]/)) setMood("love");
          else if (text.match(/[!🎉✨⭐]/)) setMood("happy");
          else if (text.match(/[😢😔]/)) setMood("sad");
          else if (text.match(/[😱🫣]/)) setMood("shock");
          else setMood("happy");
          setTimeout(() => setShowBubble(false), 6000);
          // Sleep state ist Server-Wahrheit (siehe fetchState / derive_mood) —
          // kein clientseitiger Sleep-Timer mehr. Fix #738.
        } else {
          setMood("idle");
        }
      })
      .catch(() => setMood("idle"));
  }

  // Trigger: Seitenwechsel (immer)
  useEffect(() => {
    if (!visible) return;
    const path = location.pathname;
    if (path === lastPathRef.current) return;
    lastPathRef.current = path;
    const context = Object.entries(PAGE_CONTEXTS).find(([p]) => path.startsWith(p))?.[1]
      || "The user is navigating in the console.";
    triggerComment(context);
  }, [location.pathname, visible]);

  // Trigger: Chat-Nachricht gesendet (~30% Chance)
  useEffect(() => {
    if (!visible) return;
    const handler = (e: Event) => {
      if (Math.random() > 0.3) return; // nur 30% der Nachrichten kommentieren
      const detail = (e as CustomEvent).detail || {};
      const preview = (detail.text || "").slice(0, 60);
      triggerComment(`The user sent a chat message: "${preview}"`);
    };
    window.addEventListener("hh-chat-sent", handler);
    return () => window.removeEventListener("hh-chat-sent", handler);
  }, [visible]);

  // Trigger: Update fertig
  useEffect(() => {
    if (!visible) return;
    const handler = () => triggerComment("A system update was just completed.");
    window.addEventListener("hh-update-done", handler);
    return () => window.removeEventListener("hh-update-done", handler);
  }, [visible]);

  // Trigger: Fehler
  useEffect(() => {
    if (!visible) return;
    const handler = (e: Event) => {
      const msg = (e as CustomEvent).detail?.message || "ein Fehler";
      triggerComment(`An error occurred: ${msg}`);
    };
    window.addEventListener("hh-error", handler);
    return () => window.removeEventListener("hh-error", handler);
  }, [visible]);

  // Trigger: Erster Besuch heute
  useEffect(() => {
    if (!visible) return;
    const today = new Date().toISOString().slice(0, 10);
    const lastVisit = localStorage.getItem("hh_companion_last_visit");
    if (lastVisit !== today) {
      localStorage.setItem("hh_companion_last_visit", today);
      setTimeout(() => triggerComment("The user logged in for the first time today. Greet them!"), 2000);
    }
  }, [visible]);

  // Trigger: Zufällig alle 3-5 Minuten ein idle-Kommentar
  useEffect(() => {
    if (!visible) return;
    const interval = setInterval(() => {
      if (mood === "sleep") return;
      if (Math.random() > 0.4) return; // 40% Chance alle 3 Min
      triggerComment("It's quiet right now. The user is there but not doing anything special. Say something nice or funny.");
    }, 180000);
    return () => clearInterval(interval);
  }, [visible, mood]);

  if (!visible) return null;

  const companionEl = (
    <>
      {/* Sprechblase */}
      {showBubble && bubble && (
        <div className={dockEl
          ? "absolute bottom-full right-0 mb-2 z-50 min-w-[120px] max-w-[220px] w-max whitespace-normal break-words rounded-2xl rounded-br-sm bg-card border border-border/60 shadow-lg px-3 py-2 text-xs leading-relaxed text-foreground animate-in fade-in slide-in-from-bottom-2 duration-300"
          : "pointer-events-auto min-w-[120px] max-w-[220px] w-max whitespace-normal break-words rounded-2xl rounded-br-sm bg-card border border-border/60 shadow-lg px-3 py-2 text-xs leading-relaxed text-foreground animate-in fade-in slide-in-from-bottom-2 duration-300"
        }>
          {bubble}
        </div>
      )}
      {/* Companion — animated SVG blob */}
      <div
        className="pointer-events-auto cursor-default hover:scale-110"
        style={{
          transform: `translate(${wander.x}px, ${wander.y}px)`,
          transition: "transform 1.8s cubic-bezier(0.25, 0.1, 0.25, 1)",
        }}
        title={state
          ? `Happy ${Math.round(state.happy)} · Hunger ${Math.round(state.hunger)} · Energy ${Math.round(state.energy)}\nAge: ${state.age_days}d · Pets: ${state.pet_count} · Feeds: ${state.feed_count}\nClick: streicheln · Rechtsklick: füttern`
          : "👋"}
        onClick={() => {
          if (mood === "sleep" || state?.is_sleeping) {
            interact("wake");
            return;
          }
          interact("pet");
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          interact("feed");
        }}
      >
        <BlobCreature mood={mood} size={dockEl ? 32 : 48} />
      </div>
    </>
  );

  // Wenn companion-dock im Sidebar existiert → dort reinrendern
  if (dockEl) {
    return createPortal(
      <div className="relative flex flex-col items-center">{companionEl}</div>,
      dockEl
    );
  }

  // Fallback: fixed bottom-right (kurz nach Toggle, bevor Dock gerendert ist)
  return (
    <div className="fixed bottom-4 right-20 z-50 flex flex-col items-end gap-2 pointer-events-none select-none">
      {companionEl}
    </div>
  );
}

/** Aktivierungs-Helper: 5x auf ein Element klicken → Toggle */
export function useCompanionActivation() {
  const clickCount = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  return () => {
    clickCount.current++;
    clearTimeout(timerRef.current);
    if (clickCount.current >= 5) {
      clickCount.current = 0;
      const current = localStorage.getItem("hh_companion") === "1";
      localStorage.setItem("hh_companion", current ? "0" : "1");
      window.dispatchEvent(new Event("hh-companion-toggle"));
    }
    timerRef.current = setTimeout(() => { clickCount.current = 0; }, 2000);
  };
}
