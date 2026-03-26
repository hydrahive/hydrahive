import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }

export type AgentCat = "user" | "boss" | "specialist" | "system" | "worker";

/** Visuelle Agent-Kategorie aus ID + optionalem Typ ableiten */
export function agentCategory(id: string, type?: string): AgentCat {
  const base = id.replace(/_[0-9a-f]{8}$/, ""); // UUID-Session-Suffix entfernen
  if (type === "boss" || base.includes("boss") || base.includes("_main")) return "boss";
  if (type === "worker" || base.includes("worker") || base.includes("coder") || base.includes("researcher")) return "worker";
  if (base.startsWith("personal_")) return "user";
  if (base.endsWith("_specialist")) return "specialist";
  if (base === "monitor_agent" || base === "notify_agent" || base.includes("support")) return "system";
  return "specialist";
}

/** Farbklassen je Kategorie */
export const AGENT_COLORS: Record<AgentCat, {
  badge: string;       // Badge-Pill
  border: string;      // Box-Rahmen
  bg: string;          // Box-Hintergrund-Tint
  label: string;       // Anzeigetext
}> = {
  user:       { label: "user",       badge: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border border-blue-500/30",     border: "border-blue-300   dark:border-blue-700",   bg: "bg-blue-50/40   dark:bg-blue-950/20"   },
  boss:       { label: "boss",       badge: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30", border: "border-amber-300  dark:border-amber-700",  bg: "bg-amber-50/40  dark:bg-amber-950/20"  },
  specialist: { label: "specialist", badge: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30", border: "border-emerald-300 dark:border-emerald-700", bg: "bg-emerald-50/40 dark:bg-emerald-950/20" },
  system:     { label: "system",     badge: "bg-slate-500/15 text-slate-600 dark:text-slate-400 border border-slate-500/30", border: "border-slate-300  dark:border-slate-600",  bg: "bg-slate-50/40  dark:bg-slate-900/20"  },
  worker:     { label: "worker",     badge: "bg-purple-500/15 text-purple-600 dark:text-purple-400 border border-purple-500/30", border: "border-purple-300 dark:border-purple-700", bg: "bg-purple-50/40 dark:bg-purple-950/20" },
};
