import React from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom";
import { NotificationBell } from "@/components/NotificationBell";
import { SupportWidget } from "@/components/SupportWidget";
import { FloatingCompanion, useCompanionActivation } from "@/components/FloatingCompanion";
import {
  LayoutDashboard,
  Bot,
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
  Workflow,
  Package,
  Brain,
  Users,
  Shield,
  Lightbulb,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Plug,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import i18n from "@/lib/i18n";

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
      const token = localStorage.getItem("hydrahive_token") || "";
      try {
        const res = await fetch("/api/admin/update/status", {
          headers: { Authorization: `Bearer ${token}` },
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
  const location = useLocation();
  const companionTap = useCompanionActivation();

  type NavItem = { to: string; icon: React.ElementType; label: string; hint: string; adminOnly?: boolean };

  const allNavItems: NavItem[] = [
    { to: "/dashboard",      icon: LayoutDashboard, label: t("nav.dashboard"),       hint: t("navHint.dashboard") },
    { to: "/my-agent",       icon: Bot,             label: t("nav.myAgent"),         hint: t("navHint.myAgent") },
    { to: "/agents",         icon: Users,           label: t("nav.agents"),          hint: t("navHint.agents") },
    { to: "/projects",       icon: FolderKanban,    label: t("nav.projects"),        hint: t("navHint.projects") },
    { to: "/blueprint",      icon: Workflow,        label: t("nav.blueprint"),       hint: t("navHint.blueprint") },
    { to: "/hub",            icon: Package,         label: t("nav.hydraHub"),        hint: t("navHint.hydraHub") },
    { to: "/brain",          icon: Brain,           label: t("nav.hydraBrain"),      hint: t("navHint.hydraBrain") },
    { to: "/system",         icon: Monitor,         label: t("nav.system"),          hint: t("navHint.system") },
    { to: "/usermanagement", icon: Shield,          label: t("nav.usermanagement"),  hint: t("navHint.usermanagement") },
    { to: "/settings",       icon: Settings,        label: t("nav.settings"),        hint: t("navHint.settings") },
    { to: "/mcp",            icon: Plug,            label: t("nav.mcp", { defaultValue: "MCP-Server" }), hint: t("navHint.mcp", { defaultValue: "Model Context Protocol Server verwalten" }) },
    { to: "/prompt-guide",   icon: Lightbulb,       label: t("nav.promptGuide"),     hint: t("navHint.promptGuide", { defaultValue: "KI-Tipps für bessere Prompts" }) },
  ];

  const nav = allNavItems.filter((item) => {
    if (isAdmin) return true;
    const pageId = item.to.replace(/^\//, "");
    return hasPageAccess(pageId);
  });

  const [dark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { updating, updateAvailable, lastCommit, error: updateError, trigger: triggerUpdate, showLog, logLines, logDone, closeLog } = useUpdateStatus(isAdmin);
  const coreOnline = useCoreConnection();
  const showDeploymentPanel = isAdmin && (updating || Boolean(updateError) || updateAvailable);
  const deploymentUrgent = updating || Boolean(updateError) || updateAvailable;

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const activeItem = useMemo(
    () => nav.find((item) => location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)) ?? nav[0],
    [location.pathname, nav],
  );

  const sidebar = (
    <aside className="app-sidebar">
      <div className="border-b border-[hsl(var(--sidebar-border))] px-5 py-5">
        <div className="flex items-center justify-center relative">
            <img src="/hydrahive-logo.png" alt="HydraHive"
              className="h-[120px] w-[120px] rounded-2xl"
              style={{ animation: "pulse-glow 3s ease-in-out infinite" }} />
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="absolute right-0 top-0 rounded-xl p-2 text-[hsl(var(--sidebar-muted))] hover:bg-white/10 hover:text-[hsl(var(--sidebar-foreground))] lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-[hsl(var(--sidebar-foreground))]">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{user?.username ?? t("layout.unknown")}</span>
            {isAdmin && <span className="rounded-full bg-white/10 px-2 py-0.5 text-[0.65rem] uppercase tracking-[0.18em]">admin</span>}
          </div>
          <p className="mt-1 text-xs text-[hsl(var(--sidebar-muted))]">{t("layout.hybridConsole")}</p>
        </div>
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/5 p-4 text-[hsl(var(--sidebar-foreground))] shadow-sm">
          <p className="text-[0.62rem] uppercase tracking-[0.24em] text-[hsl(var(--sidebar-muted))]">
            {t("layout.assistantKicker")}
          </p>
          <div className="mt-2 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold leading-tight">{t("layout.assistantName")}</h2>
              <p className="mt-1 text-xs text-[hsl(var(--sidebar-muted))]">{t("layout.assistantSubtitle")}</p>
            </div>
            <span className="rounded-full bg-emerald-400/15 px-2.5 py-1 text-[0.65rem] font-medium uppercase tracking-[0.18em] text-emerald-300">
              {t("layout.assistantStatus")}
            </span>
          </div>
          <button
            type="button"
            onClick={() => navigate("/my-agent")}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-2xl bg-white/10 px-3 py-2 font-medium text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/15"
          >
            <MessageSquare className="h-4 w-4" />
            {t("layout.assistantChatOpen")}
          </button>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        {nav.map(({ to, icon: Icon, label, hint }) => (
          <NavLink
            key={to}
            to={to}
            title={hint}
            className={({ isActive }) => cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
              isActive
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4 flex-shrink-0" />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-[hsl(var(--sidebar-border))] p-3">
        {/* Versionsanzeige — immer sichtbar für Admins (enthält Companion-Aktivierung) */}
        {isAdmin && !showDeploymentPanel && lastCommit && (
          <p className="mb-2 text-[10px] text-[hsl(var(--sidebar-muted))] cursor-default text-center" onClick={companionTap}>
            {t("layout.lastCommit", { commit: lastCommit })}
          </p>
        )}
        {showDeploymentPanel && (
          <div className="mb-3 rounded-2xl border border-red-400/30 bg-gradient-to-br from-red-500/15 via-red-500/10 to-rose-500/10 p-3 text-xs text-[hsl(var(--sidebar-foreground))] shadow-[0_0_0_1px_rgba(248,113,113,0.12),0_18px_40px_rgba(239,68,68,0.18)] backdrop-blur">
            <div className="flex items-center justify-between gap-3">
              <span className="font-extrabold tracking-[0.18em] text-red-200">{deploymentUrgent ? t("layout.updateAlertTitle") : t("layout.deployment")}</span>
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

        <div className="grid grid-cols-3 gap-2">
          <button
            onClick={toggleDark}
            className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/10"
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {dark ? t("layout.light") : t("layout.dark")}
          </button>
          <button
            onClick={() => i18n.changeLanguage(i18n.language === "de" ? "en" : "de")}
            className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/10"
          >
            {i18n.language === "de" ? "EN" : "DE"}
          </button>
          <button
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:border-red-300/20 hover:bg-red-500/10 hover:text-red-100"
          >
            <LogOut className="h-4 w-4" />
            {t("layout.logout")}
          </button>
        </div>
      </div>
    </aside>
  );

  // Bottom-Nav für Mobile
  const bottomNavItems = [
    { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard") },
    { to: "/my-agent",  icon: Bot,             label: t("nav.myAgent") },
    { to: "/agents",    icon: Users,           label: t("nav.agents") },
    { to: "/projects",  icon: FolderKanban,    label: t("nav.projects") },
  ];

  // Auto-scroll für Update-Log
  const updateLogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (updateLogRef.current) updateLogRef.current.scrollTop = updateLogRef.current.scrollHeight;
  }, [logLines]);

  return (
    <div className="app-shell lg:grid lg:h-screen lg:grid-cols-[18rem_minmax(0,1fr)] lg:overflow-hidden">
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
                  {logDone === null
                    ? t("layout.updateRunning")
                    : logDone
                      ? t("layout.updateSuccess", { defaultValue: "Update abgeschlossen" })
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
            <div
              ref={updateLogRef}
              className="bg-black/50 rounded-lg p-4 font-mono text-xs text-green-400 h-64 overflow-y-auto"
            >
              {logLines.length === 0 && logDone === null && (
                <div className="text-muted-foreground">{t("layout.updateWaiting", { defaultValue: "Warte auf Log-Output..." })}</div>
              )}
              {logLines.map((l, i) => <div key={i}>{l || "\u00a0"}</div>)}
            </div>
            {logDone !== null && (
              <div className="flex justify-end gap-2">
                {logDone && (
                  <p className="text-sm text-green-500 flex-1 self-center">
                    {t("layout.updateReloadHint", { defaultValue: "Seite wird gleich neu geladen..." })}
                  </p>
                )}
                <button
                  className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
                  onClick={() => { closeLog(); if (logDone) window.location.reload(); }}
                >
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
            <p className="text-lg font-semibold">
              {updating ? t("layout.restartingUpdate") : t("layout.restartingCore")}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{t("layout.restartingWait")}</p>
          </div>
        </div>
      )}

      <div className="hidden lg:block lg:h-screen lg:overflow-hidden">
        <div className="sticky top-0 h-screen">{sidebar}</div>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/45 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)}>
          <div className="h-full w-[18rem]" onClick={(e) => e.stopPropagation()}>
            {sidebar}
          </div>
        </div>
      )}

      <main className="relative min-w-0 flex h-screen flex-col overflow-hidden">
        {/* Header */}
        <div className="sticky top-0 z-20 border-b border-border/60 bg-[hsl(var(--shell))/0.82] px-4 py-3 backdrop-blur md:px-6 lg:px-8">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              {/* Hamburger: auf Mobile versteckt (Bottom-Nav übernimmt), auf Tablet sichtbar, auf Desktop unsichtbar */}
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                className="rounded-2xl border bg-card/70 p-2 text-foreground shadow-sm lg:hidden"
                aria-label="Menü öffnen"
              >
                <Menu className="h-5 w-5" />
              </button>
              <div className="min-w-0">
                <p className="hidden text-[0.7rem] uppercase tracking-[0.24em] text-muted-foreground sm:block">{t("layout.operationsConsole")}</p>
                <h2 className="truncate text-lg font-semibold tracking-tight sm:text-xl">{activeItem?.label ?? "HydraHive"}</h2>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {isAdmin && (
                <button
                  type="button"
                  onClick={triggerUpdate}
                  disabled={updating}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-sm font-medium transition",
                    updateAvailable || updateError || updating
                      ? "border-red-300/30 bg-red-500/15 text-red-100 hover:bg-red-500/20"
                      : "border-white/10 bg-white/5 text-[hsl(var(--sidebar-foreground))] hover:bg-white/10",
                    updating && "cursor-not-allowed opacity-70",
                  )}
                  title={updating ? t("layout.updateRunning") : updateAvailable ? t("layout.updateAvailable") : t("layout.triggerUpdate")}
                >
                  <RefreshCw className={cn("h-4 w-4", updating && "animate-spin")} />
                  <span className="hidden sm:inline">{updating ? t("layout.updateRunning") : t("layout.triggerUpdate")}</span>
                </button>
              )}
              {/* Hint-Pill nur ab md */}
              <span className="status-pill hidden md:inline-flex">{activeItem?.hint ?? t("layout.systemView")}</span>
              {/* Status-Pill nur ab sm */}
              <span className={cn("status-pill hidden sm:inline-flex", updating ? "bg-accent/15 text-accent" : "status-pill-ok")}>
                {updating ? t("layout.updateActive") : t("layout.systemReady")}
              </span>
              <NotificationBell />
            </div>
          </div>
        </div>

        {/* Content — Chat-Routen bekommen vollen Platz ohne Padding */}
        {location.pathname.match(/^\/(chat\/|agents\/[^/]+\/chat)/) ? (
          <div className="flex-1 min-h-0 overflow-hidden pb-14 lg:pb-0">
            <Outlet />
          </div>
        ) : (
          <div className="px-3 py-3 pb-20 sm:px-4 sm:py-4 md:px-6 md:py-6 lg:flex-1 lg:overflow-y-auto lg:px-8 lg:py-8 lg:pb-8">
            <Outlet />
          </div>
        )}
      </main>

      {/* Bottom-Navigation — auf Mobile und Tablet (< lg), Desktop hat die Sidebar */}
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
                  isActive
                    ? "text-primary"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className={cn("h-5 w-5 shrink-0", isActive && "text-primary")} />
                <span className={cn("text-[0.6rem] font-medium truncate max-w-[52px] text-center", isActive ? "text-primary" : "text-muted-foreground")}>
                  {label}
                </span>
              </NavLink>
            );
          })}
          {/* Mehr-Button öffnet Drawer */}
          <button
            onClick={() => setMobileOpen(true)}
            className="flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl text-muted-foreground hover:text-foreground transition-colors"
          >
            <Menu className="h-5 w-5 shrink-0" />
            <span className="text-[0.6rem] font-medium">{t("nav.more")}</span>
          </button>
        </div>
      </nav>

      <SupportWidget />
      <FloatingCompanion />
    </div>
  );
}
