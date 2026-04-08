/**
 * NotificationQueue — renders the active notification from the priority queue (#479)
 *
 * Position: top-right, fixed. Shows 1 notification at a time.
 * Color-coded by priority: green=low, blue=medium, orange=high, red=immediate.
 * Fold count badge when count > 1.
 */

import { useNotifications, type NotificationPriority } from "@/lib/notifications";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

const PRIORITY_STYLES: Record<NotificationPriority, { border: string; bg: string; text: string; badge: string }> = {
  low:       { border: "border-emerald-400/40", bg: "bg-emerald-500/15",  text: "text-emerald-200", badge: "bg-emerald-500/30 text-emerald-100" },
  medium:    { border: "border-blue-400/40",    bg: "bg-blue-500/15",     text: "text-blue-200",    badge: "bg-blue-500/30 text-blue-100" },
  high:      { border: "border-orange-400/40",  bg: "bg-orange-500/15",   text: "text-orange-200",  badge: "bg-orange-500/30 text-orange-100" },
  immediate: { border: "border-red-400/40",     bg: "bg-red-500/15",      text: "text-red-200",     badge: "bg-red-500/30 text-red-100" },
};

export function NotificationQueue() {
  const { active, queueLength, dismiss } = useNotifications();
  const [visible, setVisible] = useState(false);
  const [currentId, setCurrentId] = useState<string | null>(null);

  // Animate in/out
  useEffect(() => {
    if (active) {
      if (active.id !== currentId) {
        // New notification — fade in
        setVisible(false);
        const t = setTimeout(() => { setVisible(true); setCurrentId(active.id); }, 50);
        return () => clearTimeout(t);
      }
      // Same notification (fold update) — stay visible
      setVisible(true);
    } else {
      setVisible(false);
      const t = setTimeout(() => setCurrentId(null), 300);
      return () => clearTimeout(t);
    }
  }, [active, currentId]);

  if (!active && !currentId) return null;

  const display = active;
  if (!display) return null;

  const styles = PRIORITY_STYLES[display.priority];

  return (
    <div
      className={[
        "fixed top-4 right-4 z-[200] max-w-sm w-full pointer-events-auto",
        "transition-all duration-300 ease-out",
        visible ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-3",
      ].join(" ")}
    >
      <div
        className={[
          "rounded-2xl border px-4 py-3 shadow-xl backdrop-blur-sm",
          styles.border,
          styles.bg,
        ].join(" ")}
      >
        <div className="flex items-start gap-3">
          {/* Icon */}
          {display.icon && (
            <span className="mt-0.5 flex-shrink-0 text-base">{display.icon}</span>
          )}

          {/* Message + fold count */}
          <div className="min-w-0 flex-1">
            <p className={["text-sm font-medium", styles.text].join(" ")}>
              {display.message}
              {display.count > 1 && (
                <span className={["ml-2 inline-flex items-center rounded-full px-1.5 py-0.5 text-[0.65rem] font-bold", styles.badge].join(" ")}>
                  x{display.count}
                </span>
              )}
            </p>
          </div>

          {/* Dismiss + queue indicator */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {queueLength > 0 && (
              <span className="text-[0.6rem] text-muted-foreground/70">
                +{queueLength}
              </span>
            )}
            <button
              onClick={dismiss}
              className="rounded-lg p-1 text-muted-foreground/60 hover:text-foreground transition-colors"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
