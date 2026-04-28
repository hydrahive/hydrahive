import React from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom";
import { NotificationBell } from "@/components/NotificationBell";
import { NotificationQueue } from "@/components/NotificationQueue";
import { SupportWidget } from "@/components/SupportWidget";
import { FloatingCompanion, useCompanionActivation, BlobCreature } from "@/components/FloatingCompanion";
import {
  LayoutDashboard,
  FolderKanban,
  Monitor,
  LogOut,
  Sun,
  Moon,
  MessageSquare,
  RefreshCw,
  Menu,
  X,
  Settings,
  Loader2,
  Brain,
  Shield,
  Lightbulb,
  Rocket,
  Search,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Plug,
  Code,
  ServerCog,
  Bot,
  Server,
  Users,
  Workflow,
  CalendarClock,
  Network,
  ChevronDown,
  Activity,
  Cpu,
  Radar,
  Gauge,
  ShieldCheck,
  Image,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { useEffect, useRef, useState, useCallback, useMemo, type ReactNode } from "react";
import { api, GpuInfo, HeartbeatTaskStatus } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";
import { TourProvider } from "@/components/tours/TourProvider";
import { HeaderSlotCtx } from "@/components/layout/HeaderSlotContext";

// ANSI-Farbcodes aus Log-Zeilen entfernen
const ANSI_RE = /\x1b\[[0-9;]*m/g;
function stripAnsi(s: string): string { return s.replace(ANSI_RE, ""); }

function useUpdateStatus(isAdmin: boolean) {
  const [updating, setUpdating] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [lastCommit, setLastCommit] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Live-Log Modal State — bei Reload wiederherstellen
  const [showLog, setShowLog] = useState(false); // nie aus localStorage restaurieren — verhindert hängendes Modal
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logDone, setLogDone] = useState<boolean | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // localStorage aufräumen falls noch von alter Version gesetzt
  useEffect(() => {
    localStorage.removeItem("hh_update_modal");
  }, []);

  const check = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const s = await api.updateStatus();
      setUpdateAvailable(Boolean(s.available));
      if (s.status === "running") {
        setUpdating(true);
        // Falls Polling schon aktiv (User hat Update getriggert), weiterlaufen lassen
      } else {
        setUpdating(false);
        if (s.commit) setLastCommit(s.commit);
        if (s.status === "error") setError(s.error || "Update fehlgeschlagen");
        else setError(null);
      }
    } catch {
      // status endpoint not critical
    }
  }, [isAdmin]);

  useEffect(() => {
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, [check]);

  function startPolling() {
    if (pollRef.current) return; // bereits aktiv
    let seenLines = 0;
    let finished = false;
    let retries = 0;
    const MAX_RETRIES = 120; // ~4 Minuten bei 2s Intervall

    setShowLog(true);

    pollRef.current = setInterval(async () => {
      if (finished) { clearInterval(pollRef.current!); pollRef.current = null; return; }
      try {
        const res = await fetch("/api/admin/update/status", {
          credentials: "include",
        });
        if (!res.ok) {
          retries++;
          if (retries <= 3) {
            setLogLines(l => [...l, "⏳ Core startet neu..."]);
          }
          if (retries > MAX_RETRIES) {
            finished = true;
            clearInterval(pollRef.current!); pollRef.current = null;
            setLogLines(l => [...l, "[TIMEOUT] Update dauert zu lange oder Core nicht erreichbar"]);
            setLogDone(false);
            setUpdating(false);
          }
          return;
        }
        retries = 0;
        const data = await res.json();
        const logTail: string[] = (data.log_tail || []).map(stripAnsi);
        const logTotal: number = data.log_total || logTail.length;

        // Neue Zeilen anzeigen (logTotal = absolute Zeilenanzahl im Log)
        if (logTotal > seenLines) {
          // Berechne wie viele neue Zeilen seit dem letzten Poll dazugekommen sind
          const newCount = logTotal - seenLines;
          const newLines = logTail.slice(Math.max(0, logTail.length - newCount));
          setLogLines(l => [...l, ...newLines].slice(-500));
          seenLines = logTotal;
        }

        // Status prüfen
        const st = data.status || "";
        if (st === "ok" || st === "error") {
          finished = true;
          clearInterval(pollRef.current!); pollRef.current = null;
          setLogDone(st === "ok");
          setUpdating(false);
          if (data.commit) setLastCommit(data.commit);
          if (st === "error") setError(data.error || "Update fehlgeschlagen");
          else { setError(null); setUpdateAvailable(false); }
        }
      } catch {
        retries++;
        if (retries <= 3) {
          setLogLines(l => [...l, "⏳ Verbindung unterbrochen — warte auf Core..."]);
        }
        if (retries > MAX_RETRIES) {
          finished = true;
          clearInterval(pollRef.current!); pollRef.current = null;
          setLogDone(false);
          setUpdating(false);
        }
      }
    }, 2000);
  }

  const trigger = useCallback(async () => {
    setUpdating(true);
    setError(null);
    setLogLines([]);
    setLogDone(null);

    try {
      await api.updateTrigger();
    } catch (e: unknown) {
      setUpdating(false);
      setLogDone(false);
      setShowLog(true);
      setError(e instanceof Error ? e.message : "Fehler");
      setLogLines(l => [...l, `[ERROR] ${e instanceof Error ? e.message : "Unbekannter Fehler"}`]);
      return;
    }

    startPolling();
  }, []);

  const closeLog = useCallback(() => {
    setShowLog(false);
    setLogLines([]);
    setLogDone(null);
  }, []);

  return { updating, updateAvailable, lastCommit, error, trigger, showLog, logLines, logDone, closeLog };
}

function useCoreConnection() {
  const [online, setOnline] = useState(true);
  const fails = React.useRef(0);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    async function check() {
      try {
        await api.health();
        if (fails.current > 0) {
          // Core wieder erreichbar — Seite neu laden für frische Assets
          window.location.reload();
          return;
        }
        timer = setTimeout(check, 6_000);
      } catch {
        fails.current += 1;
        if (fails.current >= 2) setOnline(false); // erst nach 2 Fehlern Overlay
        timer = setTimeout(check, 2_000);
      }
    }

    timer = setTimeout(check, 3_000); // erster Check früh damit Overlay schnell erscheint
    return () => clearTimeout(timer);
  }, []);

  return online;
}

function useDarkMode() {
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem("theme");
    if (stored) return stored === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  return [dark, () => setDark((d) => !d)] as const;
}

export function AdminLayout() {
  const { t } = useTranslation();
  const { user, isAdmin, hasPageAccess, logout } = useAuth();
  const navigate = useNavigate();

  // ── Header Info-Bar State (Layout V2) ───────────────────────────
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [gpu, setGpu] = useState<GpuInfo | null>(null);
  const [heartbeatTasks, setHeartbeatTasks] = useState<HeartbeatTaskStatus[]>([]);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [coreHealthy, setCoreHealthy] = useState<boolean | null>(null);
  const runtime = status?.runtime as Record<string, any> | undefined;
  const running = runtime ? Object.values(runtime).filter((a: any) => a.status === "running").length : 0;
  const agents = status?.discovery?.count ?? null;
  const projects = status?.projects?.count ?? null;
  const gpuList = gpu?.available && gpu.gpus ? gpu.gpus : [];
  const hottestGpu = gpuList.length > 0 ? [...gpuList].sort((a, b) => (b.temp_c ?? -1) - (a.temp_c ?? -1))[0] : null;
  const runningHeartbeats = heartbeatTasks.length;

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [s, h, g] = await Promise.all([
          api.status().catch(() => null),
          api.heartbeatTasks().catch(() => ({ tasks: [] })),
          api.gpuInfo().catch(() => null),
        ]);
        try {
          const h = await api.health();
          setCoreHealthy(h?.status === "ok");
        } catch { setCoreHealthy(false); }
        if (!alive) return;
        setStatus(s);
        if (g) setGpu(g);
        setHeartbeatTasks(h.tasks ?? []);
        setLastUpdated(new Date());
      } catch { /* non-critical */ }
    }
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const location = useLocation();
  const companionTap = useCompanionActivation();
  const [companionActive, setCompanionActive] = useState(() => localStorage.getItem("hh_companion") === "1");
  useEffect(() => {
    const handler = () => setCompanionActive(localStorage.getItem("hh_companion") === "1");
    window.addEventListener("storage", handler);
    window.addEventListener("hh-companion-toggle", handler);
    return () => { window.removeEventListener("storage", handler); window.removeEventListener("hh-companion-toggle", handler); };
  }, []);

  type NavItem = { to: string; icon: React.ElementType; label: string; hint: string; adminOnly?: boolean };
  type NavGroup = { id: string; label: string; items: NavItem[] };

  const navGroups: NavGroup[] = [
    {
      id: "main",
      label: "",
      items: [
        { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard"),  hint: t("navHint.dashboard") },
        { to: "/projects",  icon: FolderKanban,    label: t("nav.projects"),   hint: t("navHint.projects") },
      ],
    },
    {
      id: "ai",
      label: t("nav.group.ai", { defaultValue: "KI & Agenten" }),
      items: [
        { to: "/agents",    icon: Bot,      label: t("nav.agents",    { defaultValue: "Agenten" }),  hint: t("navHint.agents",    { defaultValue: "Alle Agenten verwalten" }), adminOnly: true },
        { to: "/blueprint", icon: Workflow, label: t("nav.blueprint"),                               hint: t("navHint.blueprint") },
        { to: "/proactive", icon: CalendarClock, label: t("nav.proactive", { defaultValue: "Proaktiv" }), hint: t("navHint.proactive", { defaultValue: "Autonome Background-Tasks" }), adminOnly: true },
        { to: "/dream",     icon: Moon,         label: t("nav.dream",     { defaultValue: "AutoDream" }),   hint: t("navHint.dream",     { defaultValue: "Memory-Konsolidierung & Nacht-Tasks" }), adminOnly: true },
        { to: "/teams",     icon: Users,    label: t("nav.teams",     { defaultValue: "Teams" }),    hint: t("navHint.teams",     { defaultValue: "Agent-Teams und Rollen verwalten" }), adminOnly: true },
        { to: "/session-history", icon: MessageSquare, label: t("nav.sessionHistory"),                           hint: t("navHint.sessionHistory"), adminOnly: true },
      ],
    },
    {
      id: "knowledge",
      label: t("nav.group.knowledge", { defaultValue: "Wissen & Tools" }),
      items: [
        { to: "/brain",       icon: Brain,     label: t("nav.hydraBrain"),                                      hint: t("navHint.hydraBrain") },
        { to: "/search",      icon: Search,    label: t("nav.search",       { defaultValue: "Web-Suche" }),      hint: t("navHint.search",       { defaultValue: "SearXNG Web-Suche" }) },
        { to: "/prompt-guide",icon: Lightbulb, label: t("nav.promptGuide"),                                     hint: t("navHint.promptGuide",  { defaultValue: "KI-Tipps für bessere Prompts" }) },
      ],
    },
    {
      id: "infra",
      label: t("nav.group.infra", { defaultValue: "Infrastruktur" }),
      items: [
        { to: "/system",         icon: Monitor,     label: t("nav.system"),                                              hint: t("navHint.system"), adminOnly: true },
        { to: "/target-systems", icon: ServerCog,   label: t("nav.targetSystems", { defaultValue: "Zielsysteme" }),      hint: t("navHint.targetSystems", { defaultValue: "WKS und Remote-Server" }), adminOnly: true },
        { to: "/vms",            icon: Server,      label: t("nav.vms",           { defaultValue: "Virtuelle Server" }), hint: "VMs erstellen und verwalten", adminOnly: true },
        { to: "/voice",          icon: MessageSquare, label: t("nav.voice"),                                             hint: t("navHint.voice"), adminOnly: true },
        { to: "/federation",     icon: Network,     label: t("nav.federation"),                                          hint: t("navHint.federation"), adminOnly: true },
      ],
    },
    {
      id: "extensions",
      label: t("nav.group.extensions", { defaultValue: "Erweiterungen" }),
      items: [
        { to: "/extensions",       icon: Rocket,       label: t("nav.extensions"),                                   hint: t("navHint.extensions"), adminOnly: true },
        { to: "/mcp",                icon: Plug,         label: t("nav.mcp",       { defaultValue: "MCP-Server" }),    hint: t("navHint.mcp", { defaultValue: "MCP-Server verwalten" }) },
        { to: "/schedules",          icon: CalendarClock, label: t("nav.schedules"),                                   hint: t("navHint.schedules"), adminOnly: true },
        { to: "/jobs",               icon: Gauge,        label: t("nav.jobs"),                                      hint: t("navHint.jobs"), adminOnly: true },
        { to: "/media",              icon: Image,        label: t("nav.mediaGallery", { defaultValue: "Media-Galerie" }),  hint: t("navHint.mediaGallery", { defaultValue: "Generierte Bilder, Videos & Audio" }), adminOnly: true },
      ],
    },
    {
      id: "admin",
      label: t("nav.group.admin", { defaultValue: "Administration" }),
      items: [
        { to: "/usermanagement", icon: Shield,   label: t("nav.usermanagement"), hint: t("navHint.usermanagement") },
        { to: "/settings",       icon: Settings, label: t("nav.settings"),       hint: t("navHint.settings") },
        { to: "/playground",     icon: Code,     label: "API Playground",        hint: "API-Endpoints testen", adminOnly: true },
        { to: "/audit",          icon: ShieldCheck, label: t("nav.audit", { defaultValue: "Audit Log" }), hint: t("navHint.audit", { defaultValue: "System-Audit-Protokoll" }), adminOnly: true },
      ],
    },
  ];

  // Gruppen filtern: Items nach Rechten, leere Gruppen entfernen
  const nav = navGroups.map(g => ({
    ...g,
    items: g.items.filter(item => {
      if (isAdmin) return true;
      const pageId = item.to.replace(/^\//, "").split("?")[0];
      return hasPageAccess(pageId);
    }),
  })).filter(g => g.items.length > 0);

  // Klapp-State pro Gruppe (in localStorage)
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("hh_nav_groups") ?? "{}"); } catch { return {}; }
  });

  function isGroupOpen(groupId: string, groupItems: NavItem[]): boolean {
    // Gruppe ist offen wenn: explizit geöffnet, aktive Route drin, oder erste Gruppe (main)
    if (groupId === "main") return true;
    if (openGroups[groupId] !== undefined) return openGroups[groupId];
    return groupItems.some(item => {
      const itemPath = item.to.split("?")[0];
      return location.pathname === itemPath || location.pathname.startsWith(`${itemPath}/`);
    });
  }

  function toggleGroup(groupId: string) {
    setOpenGroups(prev => {
      const next = { ...prev, [groupId]: !isGroupOpen(groupId, nav.find(g => g.id === groupId)?.items ?? []) };
      localStorage.setItem("hh_nav_groups", JSON.stringify(next));
      return next;
    });
  }

  // Bei Routenwechsel: Gruppe mit aktiver Route öffnen
  useEffect(() => {
    nav.forEach(g => {
      const hasActive = g.items.some(item => {
        const itemPath = item.to.split("?")[0];
        return location.pathname === itemPath || location.pathname.startsWith(`${itemPath}/`);
      });
      if (hasActive && openGroups[g.id] === false) {
        setOpenGroups(prev => {
          const next = { ...prev, [g.id]: true };
          localStorage.setItem("hh_nav_groups", JSON.stringify(next));
          return next;
        });
      }
    });
  }, [location.pathname]);

  const [headerSlot, setHeaderSlot] = useState<ReactNode>(null);
  const headerSlotCtx = useMemo(() => ({ setSlot: setHeaderSlot }), []);

  const [dark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  // Array of refs for each dropdown — lets us detect outside clicks correctly
  const dropdownRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      // Close if click is outside ALL open dropdowns
      const open = openDropdown;
      if (!open) return;
      const ref = dropdownRefs.current[open];
      if (ref && !ref.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [openDropdown]);

  useEffect(() => { setOpenDropdown(null); }, [location.pathname]);
  const { updating, updateAvailable, lastCommit, error: updateError, trigger: triggerUpdate, showLog, logLines, logDone, closeLog } = useUpdateStatus(isAdmin);
  const coreOnline = useCoreConnection();
  const showDeploymentPanel = isAdmin;
  const deploymentUrgent = updating || Boolean(updateError) || updateAvailable;

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const activeItem = useMemo(() => {
    const allItems = nav.flatMap(g => g.items);
    return allItems.find(item => {
      const itemPath = item.to.split("?")[0];
      return location.pathname === itemPath || location.pathname.startsWith(`${itemPath}/`);
    }) ?? allItems[0];
  }, [location.pathname, nav]);

  const sidebar = (
    <aside className="app-sidebar">
      <div className="flex items-center justify-between h-[96px] px-4 border-b border-[hsl(var(--sidebar-border))]">
        <button
          type="button"
          onClick={() => setMobileOpen(false)}
          className="lg:hidden rounded-xl p-2 text-[hsl(var(--sidebar-muted))] hover:bg-white/10 hover:text-[hsl(var(--sidebar-foreground))]"
          aria-label="Close menu"
        >
          <X className="h-5 w-5" />
        </button>
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <img
            src="/hydrahive-logo.png"
            alt="HydraHive"
            className="h-20 w-20 rounded-2xl"
            style={{ animation: "pulse-glow 3s ease-in-out infinite" }}
          />
          <span className="font-semibold text-base text-[hsl(var(--sidebar-foreground))] truncate">HydraHive</span>
        </div>
        <button
          type="button"
          onClick={() => navigate("/my-agent")}
          className="rounded-xl p-2 text-[hsl(var(--sidebar-muted))] hover:bg-white/10 hover:text-[hsl(var(--sidebar-foreground))]"
          title={t("layout.assistantChatOpen")}
        >
          <MessageSquare className="h-4 w-4" />
        </button>
      </div>
      {lastCommit && (
        <p className="px-4 pb-1 text-[10px] text-[hsl(var(--sidebar-muted))] cursor-default" onClick={companionTap}>
          {t("layout.lastCommit", { commit: lastCommit })}
        </p>
      )}

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
        {nav.map(group => {
          const open = isGroupOpen(group.id, group.items);
          return (
            <div key={group.id}>
              {group.label && (
                <button
                  type="button"
                  onClick={() => toggleGroup(group.id)}
                  className="flex w-full items-center justify-between px-3 py-1.5 text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-[hsl(var(--sidebar-muted))] hover:text-[hsl(var(--sidebar-foreground))] transition-colors"
                >
                  {group.label}
                  <ChevronDown className={cn("h-3 w-3 transition-transform duration-200", open ? "rotate-0" : "-rotate-90")} />
                </button>
              )}
              {open && (
                <div className="space-y-0.5">
                  {group.items.map(({ to, icon: Icon, label, hint }) => {
                    const itemPath = to.split("?")[0];
                    const tourId = to === "/projects" ? "nav-projects"
                      : to === "/settings" ? "nav-settings"
                      : itemPath === "/hub" ? "nav-extensions"
                      : undefined;
                    return (
                      <NavLink
                        key={to}
                        to={to}
                        title={hint}
                        data-tour={tourId}
                        className={({ isActive }) => cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                          group.label ? "pl-4" : "",
                          isActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                      >
                        <Icon className="h-4 w-4 flex-shrink-0" />
                        <span className="truncate">{label}</span>
                      </NavLink>
                    );
                  })}
                </div>
              )}
              {group.label && <div className="mt-1 border-t border-white/5" />}
            </div>
          );
        })}
      </nav>

      <div className="border-t border-[hsl(var(--sidebar-border))] p-3">
        {showDeploymentPanel && (
          <div className="mb-3 rounded-2xl border border-red-400/30 bg-gradient-to-br from-red-500/15 via-red-500/10 to-rose-500/10 p-3 text-xs text-[hsl(var(--sidebar-foreground))] shadow-[0_0_0_1px_rgba(248,113,113,0.12),0_18px_40px_rgba(239,68,68,0.18)] backdrop-blur">
            <div className="flex items-center justify-between gap-3">
              <span className="font-extrabold tracking-[0.08em] text-red-200">{deploymentUrgent ? t("layout.updateAlertTitle") : t("layout.deployment")}</span>
              <span className={cn("status-pill", deploymentUrgent ? "bg-red-500/20 text-red-100" : "status-pill-ok")}>
                {updating ? t("layout.running") : updateAvailable ? t("layout.updateAvailable") : t("layout.ready")}
              </span>
            </div>
            <p className="mt-2 text-[hsl(var(--sidebar-muted))] cursor-default" onClick={companionTap}>
              {updating
                ? t("layout.updateAlertDetail")
                : updateAvailable
                  ? t("layout.updateAlertDetailAvailable", { commit: lastCommit ?? t("layout.noCommit") })
                  : lastCommit
                    ? t("layout.lastCommit", { commit: lastCommit })
                    : t("layout.noCommit")}
            </p>
            {updateError && <p className="mt-2 text-[#ffd0d0]">{updateError}</p>}
            <button
              onClick={triggerUpdate}
              disabled={updating}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl bg-white/10 px-3 py-2 font-medium text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", updating && "animate-spin")} />
              {updating ? t("layout.updateRunning") : t("layout.triggerUpdate")}
            </button>
          </div>
        )}

      </div>
    </aside>
  );

  // Bottom-Nav für Mobile
  // v2: Vereinfachte Bottom-Nav — nur Dashboard + Projekte
  const bottomNavItems = [
    { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard") },
    { to: "/projects",  icon: FolderKanban,    label: t("nav.projects") },
    { to: "/system",    icon: Monitor,         label: t("nav.system") },
    { to: "/settings",  icon: Settings,        label: t("nav.settings") },
  ];

  // Auto-scroll für Update-Log
  const updateLogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (updateLogRef.current) updateLogRef.current.scrollTop = updateLogRef.current.scrollHeight;
  }, [logLines]);

  return (
    <HeaderSlotCtx.Provider value={headerSlotCtx}>
    <TourProvider>

    {/* ── LAYOUT V2: Full-width header, no sidebar ─────────────────── */}

    {/* Update Live-Log Modal */}
    {showLog && (
      <div className="fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm flex items-center justify-center">
        <div className="bg-card border rounded-2xl shadow-2xl max-w-2xl w-full mx-4 p-6 space-y-4">
          <div className="flex items-center gap-3">
            {logDone === null && <Loader2 className="w-5 h-5 animate-spin text-primary shrink-0" />}
            {logDone === true && <CheckCircle className="w-5 h-5 text-green-500 shrink-0" />}
            {logDone === false && <XCircle className="w-5 h-5 text-red-500 shrink-0" />}
            <div>
              <h2 className="text-base font-semibold">
                {logDone === null ? t("layout.updateRunning")
                  : logDone ? t("layout.updateSuccess", { defaultValue: "Update abgeschlossen" })
                  : t("layout.updateFailed", { defaultValue: "Update fehlgeschlagen" })}
              </h2>
              <p className="text-sm text-muted-foreground">HydraHive Self-Update</p>
            </div>
          </div>
          {logDone === null && (
            <div className="text-amber-400 text-sm font-medium flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {t("layout.updateDoNotClose", { defaultValue: "Bitte nicht schließen — Update läuft..." })}
            </div>
          )}
          <div ref={updateLogRef} className="bg-black/50 rounded-lg p-4 font-mono text-xs text-green-400 h-64 overflow-y-auto">
            {logLines.length === 0 && logDone === null && (
              <div className="text-muted-foreground">{t("layout.updateWaiting", { defaultValue: "Warte auf Log-Output..." })}</div>
            )}
            {logLines.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
          </div>
          {logDone !== null && (
            <div className="flex justify-end gap-2">
              {logDone && <p className="text-sm text-green-500 flex-1 self-center">{t("layout.updateReloadHint", { defaultValue: "Seite wird gleich neu geladen..." })}</p>}
              <button className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
                onClick={() => { closeLog(); if (logDone) window.location.reload(); }}>
                {logDone ? t("layout.updateReload", { defaultValue: "Neu laden" }) : t("layout.updateClose", { defaultValue: "Schließen" })}
              </button>
            </div>
          )}
        </div>
      </div>
    )}

    {!coreOnline && (
      <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-background/95 backdrop-blur-sm">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
        <div className="text-center">
          <p className="text-lg font-semibold">{updating ? t("layout.restartingUpdate") : t("layout.restartingCore")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t("layout.restartingWait")}</p>
        </div>
      </div>
    )}

    {/* ── FULL-VIEWPORT FLEX COLUMN (header + main fill 100vh) ──────── */}
    <div className="flex flex-col overflow-hidden" style={{ height: "100dvh" }}>

    {/* ── HORIZONTAL HEADER ────────────────────────────────────────── */}
    <div className="flex-shrink-0 z-40 flex flex-col bg-[hsl(var(--shell))/0.92] backdrop-blur-xl border-b border-border/50">

      {/* Row 1: Logo + Nav + Right Controls */}
      <div className="flex items-center gap-4 px-4 lg:px-6 h-14">

        {/* Logo + Name */}
        <button
          type="button"
          onClick={() => navigate("/dashboard")}
          className="flex items-center gap-2.5 shrink-0 rounded-xl hover:bg-white/5 transition-colors px-1 py-0.5"
        >
          <img
            src="/hydrahive-logo.png"
            alt="HydraHive"
            className="h-8 w-8 rounded-lg"
            style={{ animation: "pulse-glow 3s ease-in-out infinite" }}
          />
          <span className="font-semibold text-sm text-[hsl(var(--sidebar-foreground))] hidden sm:block">HydraHive</span>
        </button>

        {/* Vertical divider */}
        <div className="hidden lg:block w-px h-6 bg-white/10 shrink-0" />

        {/* Main Navigation — horizontal pills */}
        <nav className="hidden lg:flex items-center gap-0.5 flex-1 min-w-0">
          {nav.map(g => {
            const hasActive = g.items.some(item => {
              const p = item.to.split("?")[0];
              return location.pathname === p || location.pathname.startsWith(`${p}/`);
            });
            if (g.id === "main") {
              // Main items: Dashboard + Projects as direct pills
              return g.items.map(item => {
                const Icon = item.icon;
                const p = item.to.split("?")[0];
                const isActive = location.pathname === p || location.pathname.startsWith(`${p}/`);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    title={item.hint}
                    className={cn(
                      "flex items-center gap-2 rounded-xl px-3 py-1.5 text-sm font-medium transition-colors shrink-0",
                      isActive
                        ? "bg-primary/15 text-primary border border-primary/25"
                        : "text-muted-foreground hover:bg-white/8 hover:text-foreground"
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" />
                    {item.label}
                  </NavLink>
                );
              });
            }
            // Grouped items with dropdown
            return (
              <div key={g.id} className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setOpenDropdown(openDropdown === g.id ? null : g.id)}
                  className={cn(
                    "flex items-center gap-1 rounded-xl px-3 py-1.5 text-sm font-medium transition-colors",
                    hasActive ? "bg-primary/15 text-primary border border-primary/25" : "text-muted-foreground hover:bg-white/8 hover:text-foreground"
                  )}
                >
                  {g.label}
                  <ChevronDown className={cn("h-3 w-3 transition-transform duration-150", openDropdown === g.id && "rotate-180")} />
                </button>
                {openDropdown === g.id && (
                  <div ref={el => { dropdownRefs.current[g.id] = el; }} className="absolute left-0 top-full z-50 mt-1 min-w-[180px] rounded-xl border border-border/60 bg-card shadow-xl py-1 backdrop-blur overflow-visible">
                    {g.items.map(item => {
                      const Icon = item.icon;
                      const p = item.to.split("?")[0];
                      const isActive = location.pathname === p || location.pathname.startsWith(`${p}/`);
                      return (
                        <NavLink
                          key={item.to}
                          to={item.to}
                          onClick={() => setOpenDropdown(null)}
                          className={cn(
                            "flex items-center gap-2.5 px-4 py-2 text-sm transition hover:bg-accent/10",
                            isActive ? "text-primary font-medium" : "text-foreground"
                          )}
                        >
                          <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                          {item.label}
                        </NavLink>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        {/* Right Controls */}
        <div className="flex items-center gap-1 shrink-0 ml-auto">
          {/* Chat button — always visible, goes to my-agent */}
          <button
            type="button"
            onClick={() => navigate("/my-agent")}
            className="hidden sm:flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium bg-primary/15 text-primary border border-primary/25 hover:bg-primary/25 transition-colors"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Chat
          </button>

          <button
            type="button"
            onClick={toggleDark}
            className="rounded-xl p-2 text-muted-foreground transition hover:text-foreground hover:bg-white/8"
            title={dark ? t("layout.light") : t("layout.dark")}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <LanguageSwitcher compact />
          <button
            type="button"
            onClick={() => { logout(); navigate("/login"); }}
            className="rounded-xl p-2 text-muted-foreground transition hover:text-red-400 hover:bg-red-500/10"
            title={t("layout.logout")}
          >
            <LogOut className="h-4 w-4" />
          </button>
          <NotificationBell />

          {/* Mobile hamburger */}
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="lg:hidden rounded-xl p-2 text-muted-foreground transition hover:text-foreground hover:bg-white/8"
            aria-label="Menü öffnen"
          >
            <Menu className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* Row 2: Info Widgets Bar — clickable pills */}
      <div className="flex items-center gap-2 px-4 lg:px-6 h-10 overflow-x-auto scrollbar-none border-t border-white/5">

        {/* Core Status — not clickable, just indicator */}
        <div className={cn("flex items-center gap-1.5 shrink-0 text-xs font-medium rounded-full px-2.5 py-1 select-none cursor-default",
          coreHealthy === false ? "bg-red-500/15 text-red-400 border border-red-500/30" : coreHealthy === true ? "bg-green-500/15 text-green-400 border border-green-500/30" : "bg-muted text-muted-foreground border border-border/50"
        )}>
          <span className={cn("h-1.5 w-1.5 rounded-full", coreHealthy === false ? "bg-red-400 animate-pulse" : coreHealthy === true ? "bg-green-400" : "bg-muted-foreground")} />
          {coreHealthy === false ? t("dashboard.coreOffline") : coreHealthy === true ? t("dashboard.coreOnline") : "..."}
        </div>

        {/* Agents — clickable */}
        {agents != null && (
          <button type="button" onClick={() => navigate("/agents")}
            className="flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 bg-violet-500/15 text-violet-400 border border-violet-500/25 hover:bg-violet-500/25 transition-colors cursor-pointer">
            <Bot className="h-3 w-3" />
            Agents: {agents}
          </button>
        )}

        {/* Projects — clickable */}
        {projects != null && (
          <button type="button" onClick={() => navigate("/projects")}
            className="flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 hover:bg-cyan-500/25 transition-colors cursor-pointer">
            <FolderKanban className="h-3 w-3" />
            Projects: {projects}
          </button>
        )}

        {/* Runtime — clickable */}
        {running > 0 && (
          <button type="button" onClick={() => navigate("/system")}
            className="flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 bg-lime-500/15 text-lime-400 border border-lime-500/25 hover:bg-lime-500/25 transition-colors cursor-pointer">
            <Activity className="h-3 w-3" />
            Runtime: {running}
          </button>
        )}

        {/* GPU Temp — clickable */}
        {hottestGpu && (hottestGpu.temp_c ?? 0) > 0 && (
          <button type="button" onClick={() => navigate("/system")}
            className={cn("flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 border transition-colors cursor-pointer",
              (hottestGpu.temp_c ?? 0) >= 80
                ? "bg-amber-500/15 text-amber-400 border-amber-500/25 hover:bg-amber-500/25"
                : "bg-amber-500/10 text-amber-400/70 border-amber-500/15 hover:bg-amber-500/20"
            )}>
            <Cpu className="h-3 w-3" />
            GPU: {hottestGpu.temp_c}°C
          </button>
        )}

        {/* Heartbeats — clickable */}
        {runningHeartbeats > 0 && (
          <button type="button" onClick={() => navigate("/system")}
            className="flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 bg-muted text-muted-foreground border border-border/50 hover:bg-muted/80 transition-colors cursor-pointer">
            <Radar className="h-3 w-3" />
            HB: {runningHeartbeats}
          </button>
        )}

        {/* Update — clickable badge triggers update */}
        {isAdmin && (
          <button type="button" onClick={triggerUpdate} disabled={updating}
            className={cn("flex items-center gap-1.5 shrink-0 text-xs rounded-full px-2.5 py-1 border transition-colors cursor-pointer disabled:cursor-not-allowed",
              updating
                ? "bg-amber-500/20 text-amber-400 border-amber-500/35"
                : updateAvailable
                  ? "bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/35"
                  : "bg-muted/40 text-muted-foreground border-border hover:bg-muted/70"
            )}>
            <RefreshCw className={cn("h-3 w-3", updating && "animate-spin")} />
            {updating ? t("layout.updateRunning") : updateAvailable ? t("layout.updateAvailable") : t("layout.triggerUpdate")}
          </button>
        )}

        {/* Spacer */}
        <div className="flex-1 min-w-0" />

        {/* Last sync */}
        {lastUpdated && (
          <span className="text-[10px] text-muted-foreground shrink-0 hidden md:block select-none">
            {t("dashboard.updateSync", { time: lastUpdated.toLocaleTimeString("de-DE") })}
          </span>
        )}
      </div>
    </div>

    {/* ── MAIN CONTENT ─────────────────────────────────────────────── */}
    <main className="relative min-w-0 flex-1 min-h-0 flex flex-col overflow-hidden">

      {/* Page header with slot (tabs) — only on non-chat routes */}
      {!location.pathname.match(/^\/(chat\/|agents\/[^/]+\/chat)/) && (
        <div className="px-3 lg:px-5 pt-3 lg:pt-4 pb-0">
          {activeItem && (
            <div className="mb-3">
              <h1 className="text-lg font-semibold tracking-tight text-foreground leading-tight">
                {activeItem.label}
              </h1>
              {activeItem.hint && (
                <p className="mt-0.5 text-sm text-muted-foreground leading-snug max-w-xl">
                  {activeItem.hint}
                </p>
              )}
            </div>
          )}
          {headerSlot && <div className="mb-4">{headerSlot}</div>}
        </div>
      )}

      {/* Content area */}
      {location.pathname.match(/^\/(chat\/|agents\/[^/]+\/chat)/) ? (
        <div className="flex-1 min-h-0 flex flex-col">
          <Outlet />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-3 lg:px-5 lg:pt-0 pb-20 lg:pb-5">
          <Outlet />
        </div>
      )}
    </main>

    </div>{/* end full-viewport flex column */}

    {/* ── BOTTOM NAV (mobile only) ──────────────────────────────────── */}
    <nav className="fixed bottom-0 left-0 right-0 z-30 border-t border-border/60 bg-background/95 backdrop-blur-sm lg:hidden">
      <div className="flex items-center justify-around px-1 py-1 safe-area-inset-bottom">
        {bottomNavItems.map(({ to, icon: Icon, label }) => {
          const isActive = location.pathname === to || location.pathname.startsWith(`${to}/`);
          return (
            <NavLink
              key={to}
              to={to}
              className={cn(
                "flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl transition-colors min-w-0",
                isActive ? "text-primary" : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon className={cn("h-5 w-5 shrink-0", isActive && "text-primary")} />
              <span className={cn("text-[0.6rem] font-medium truncate max-w-[52px] text-center", isActive ? "text-primary" : "text-muted-foreground")}>
                {label}
              </span>
            </NavLink>
          );
        })}
        <button
          onClick={() => setMobileOpen(true)}
          className="flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl text-muted-foreground hover:text-foreground transition-colors"
        >
          <Menu className="h-5 w-5 shrink-0" />
          <span className="text-[0.6rem] font-medium">{t("nav.more")}</span>
        </button>
      </div>
    </nav>

    {/* Mobile Drawer */}
    {mobileOpen && (
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)}>
        <div className="absolute left-0 top-0 bottom-0 w-[260px] bg-[hsl(var(--sidebar))] border-r border-[hsl(var(--sidebar-border))] shadow-2xl overflow-y-auto" onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-between h-14 px-4 border-b border-[hsl(var(--sidebar-border))]">
            <div className="flex items-center gap-2.5">
              <img src="/hydrahive-logo.png" alt="HydraHive" className="h-8 w-8 rounded-lg" style={{ animation: "pulse-glow 3s ease-in-out infinite" }} />
              <span className="font-semibold text-sm text-[hsl(var(--sidebar-foreground))]">HydraHive</span>
            </div>
            <button onClick={() => setMobileOpen(false)} className="p-2 rounded-xl text-[hsl(var(--sidebar-muted))] hover:bg-white/10 hover:text-[hsl(var(--sidebar-foreground))]">
              <X className="h-4 w-4" />
            </button>
          </div>
          <nav className="p-3 space-y-1">
            {nav.map(g => (
              <div key={g.id}>
                {g.label && (
                  <p className="px-3 py-1.5 text-[0.65rem] font-semibold uppercase tracking-[0.1em] text-[hsl(var(--sidebar-muted))]">{g.label}</p>
                )}
                {g.items.map(item => {
                  const Icon = item.icon;
                  const p = item.to.split("?")[0];
                  const isActive = location.pathname === p || location.pathname.startsWith(`${p}/`);
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      onClick={() => setMobileOpen(false)}
                      className={cn(
                        "flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors",
                        isActive ? "bg-primary/15 text-primary" : "text-[hsl(var(--sidebar-muted))] hover:bg-white/8 hover:text-[hsl(var(--sidebar-foreground))]"
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {item.label}
                    </NavLink>
                  );
                })}
                {g.label && <div className="mt-1 border-t border-white/5" />}
              </div>
            ))}
          </nav>
        </div>
      </div>
    )}

    <NotificationQueue />
    <SupportWidget />
    <FloatingCompanion />
    </TourProvider>
    </HeaderSlotCtx.Provider>
  );

}
