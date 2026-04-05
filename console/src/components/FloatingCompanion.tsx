/**
 * FloatingCompanion — Easter-Egg Begleiter in der Console.
 * Schwebt unten rechts, kommentiert was der User macht.
 * Aktivierung: 5x auf Version in Settings klicken → localStorage hh_companion=1
 */
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "@/lib/api";

type Mood = "idle" | "happy" | "think" | "sleep" | "shock" | "love" | "sad";

/** Animated SVG blob creature — changes expression based on mood */
function BlobCreature({ mood, size = 48 }: { mood: Mood; size?: number }) {
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

// Kontext-Prompts je nach Seite
const PAGE_CONTEXTS: Record<string, string> = {
  "/dashboard":     "Der User schaut aufs Dashboard.",
  "/my-agent":      "Der User konfiguriert seinen persönlichen Agenten.",
  "/agents":        "Der User verwaltet seine Agenten.",
  "/projects":      "Der User arbeitet an Projekten.",
  "/settings":      "Der User ist in den Einstellungen.",
  "/hub":           "Der User stöbert im HydraHub nach Plugins.",
  "/brain":         "Der User schaut sich das 3D-Agentennetz an.",
  "/system":        "Der User prüft den Systemstatus.",
  "/prompt-guide":  "Der User lernt bessere Prompts zu schreiben.",
  "/blueprint":     "Der User baut Automatisierungen.",
  "/mcp":           "Der User konfiguriert MCP-Server.",
};

const SYSTEM_PROMPT = `Du bist ein winziger, niedlicher Begleiter in einer Web-Konsole. Du kommentierst kurz und witzig was der User gerade macht.
Regeln:
- Maximal 1 Satz, max 15 Wörter
- Sei süß, supportive und ein bisschen frech
- Nutze gelegentlich Emoticons
- Sprich die Sprache des Users (Deutsch wenn DE, English wenn EN)
- Kein Markdown, kein Code, nur ein kurzer Kommentar
- Du bist ein kleines Wesen das in der Ecke des Bildschirms lebt`;

export function FloatingCompanion() {
  const location = useLocation();
  const [visible, setVisible] = useState(false);
  const [bubble, setBubble] = useState("");
  const [mood, setMood] = useState<Mood>("idle");
  const [showBubble, setShowBubble] = useState(false);
  const lastCommentRef = useRef(0);
  const lastPathRef = useRef("");
  const sleepTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // Check activation
  useEffect(() => {
    setVisible(localStorage.getItem("hh_companion") === "1");
    const handler = () => setVisible(localStorage.getItem("hh_companion") === "1");
    window.addEventListener("storage", handler);
    window.addEventListener("hh-companion-toggle", handler);
    return () => { window.removeEventListener("storage", handler); window.removeEventListener("hh-companion-toggle", handler); };
  }, []);

  // Reagiere auf Seitenwechsel
  useEffect(() => {
    if (!visible) return;
    const path = location.pathname;
    if (path === lastPathRef.current) return;
    lastPathRef.current = path;

    // Throttle: max 1 Kommentar pro 30s
    const now = Date.now();
    if (now - lastCommentRef.current < 30000) return;
    lastCommentRef.current = now;

    // Sleep-Timer reset
    clearTimeout(sleepTimerRef.current);
    setMood("think");

    const context = Object.entries(PAGE_CONTEXTS).find(([p]) => path.startsWith(p))?.[1]
      || "Der User navigiert in der Console.";

    // LLM-Call für Kommentar
    const token = localStorage.getItem("hydrahive_token") || "";
    fetch("/api/agents/personal_admin/message", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        content: `[SYSTEM: ${SYSTEM_PROMPT}]\n\nKontext: ${context}\nGib einen kurzen Kommentar.`,
      }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.response) {
          const text = d.response.slice(0, 80);
          setBubble(text);
          setShowBubble(true);
          // Mood basierend auf Inhalt
          if (text.match(/[♥❤😍🥰]/)) setMood("love");
          else if (text.match(/[!🎉✨⭐]/)) setMood("happy");
          else if (text.match(/[😢😔]/)) setMood("sad");
          else if (text.match(/[😱🫣]/)) setMood("shock");
          else setMood("happy");
          // Bubble nach 6s ausblenden
          setTimeout(() => setShowBubble(false), 6000);
          // Nach 2min Inaktivität schlafen
          sleepTimerRef.current = setTimeout(() => setMood("sleep"), 120000);
        } else {
          setMood("idle");
        }
      })
      .catch(() => setMood("idle"));
  }, [location.pathname, visible]);

  if (!visible) return null;

  return (
    <div className="fixed bottom-4 right-20 z-50 flex flex-col items-end gap-2 pointer-events-none select-none">
      {/* Sprechblase */}
      {showBubble && bubble && (
        <div className="pointer-events-auto max-w-[220px] rounded-2xl rounded-br-sm bg-card border border-border/60 shadow-lg px-3 py-2 text-xs text-foreground animate-in fade-in slide-in-from-bottom-2 duration-300">
          {bubble}
        </div>
      )}
      {/* Companion — animated SVG blob */}
      <div
        className="pointer-events-auto cursor-default transition-all duration-500 hover:scale-110"
        style={{ animation: mood === "sleep" ? "none" : "companion-bob 3s ease-in-out infinite" }}
        title="👋"
        onClick={() => {
          if (mood === "sleep") {
            setMood("happy");
            setBubble("*gähn* ... bin wach!");
            setShowBubble(true);
            setTimeout(() => setShowBubble(false), 4000);
          }
        }}
      >
        <BlobCreature mood={mood} size={48} />
      </div>
      <style>{`
        @keyframes companion-bob {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-6px); }
        }
      `}</style>
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
