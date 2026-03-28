import React from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom";
import { NotificationBell } from "@/components/NotificationBell";
import { SupportWidget } from "@/components/SupportWidget";
import {
  LayoutDashboard,
  Bot,
  Activity,
  FolderKanban,
  Server,
  Wrench,
  LogOut,
  ShieldCheck,
  Sun,
  Moon,
  Sparkles,
  RefreshCw,
  Menu,
  X,
  Settings,
  BarChart2,
  Search,
  Calendar,
  Loader2,
  Globe,
  Code2,
  Puzzle,
  Workflow,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import i18n from "@/lib/i18n";

function useUpdateStatus(isAdmin: boolean) {
  const [updating, setUpdating] = useState(false);
  const [lastCommit, setLastCommit] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const s = await api.updateStatus();
      if (s.status === "running") {
        setUpdating(true);
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

  const trigger = useCallback(async () => {
    setUpdating(true);
    setError(null);
    try {
      await api.updateTrigger();
      const poll = setInterval(async () => {
        try {
          const s = await api.updateStatus();
          if (s.status !== "running") {
            clearInterval(poll);
            setUpdating(false);
            if (s.commit) setLastCommit(s.commit);
            if (s.status === "error") setError(s.error || "Update fehlgeschlagen");
          }
        } catch {
          clearInterval(poll);
          setUpdating(false);
        }
      }, 3000);
    } catch (e: unknown) {
      setUpdating(false);
      setError(e instanceof Error ? e.message : "Fehler");
    }
  }, []);

  return { updating, lastCommit, error, trigger };
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
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  type NavItem = { to: string; icon: React.ElementType; label: string; hint: string };
  type NavGroup = { label?: string; items: NavItem[] };

  const groupWorkspace: NavGroup = {
    items: [
      { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard"), hint: t("navHint.dashboard") },
      { to: "/my-agent",  icon: Sparkles,        label: t("nav.myAgent"),   hint: t("navHint.myAgent") },
      { to: "/projects",  icon: FolderKanban,    label: t("nav.projects"),  hint: t("navHint.projects") },
      { to: "/tools",     icon: Wrench,          label: t("nav.tools"),     hint: t("navHint.tools") },
    ],
  };

  const groupOperations: NavGroup = {
    label: t("nav.groupOperations"),
    items: [
      { to: "/agents",   icon: Bot,      label: t("nav.agents"),   hint: t("navHint.agents") },
      { to: "/activity", icon: Activity, label: t("nav.activity"), hint: t("navHint.activity") },
      { to: "/usage",    icon: BarChart2,label: t("nav.usage"),    hint: t("navHint.usage") },
      { to: "/search",     icon: Search,    label: t("nav.search"),     hint: t("navHint.search") },
      { to: "/extensions", icon: Puzzle,    label: t("nav.extensions"), hint: t("navHint.extensions") },
      { to: "/code-editor",icon: Code2,     label: t("nav.codeEditor"), hint: t("navHint.codeEditor") },
      { to: "/schedules",  icon: Calendar,  label: t("nav.schedules"),  hint: t("navHint.schedules") },
      { to: "/federation", icon: Globe,     label: t("nav.federation"), hint: t("navHint.federation") },
      { to: "/butler",     icon: Workflow,  label: t("nav.butler"),     hint: t("navHint.butler") },
    ],
  };

  const groupSystem: NavGroup = {
    label: t("nav.groupSystem"),
    items: [
      { to: "/system",   icon: Server,     label: t("nav.system"),   hint: t("navHint.system") },
      { to: "/audit",    icon: ShieldCheck,label: t("nav.auditLog"), hint: t("navHint.auditLog") },
      { to: "/settings", icon: Settings,   label: t("nav.settings"), hint: t("navHint.settings") },
    ],
  };

  const groups: NavGroup[] = isAdmin
    ? [groupWorkspace, groupOperations, groupSystem]
    : [groupWorkspace];

  const nav = groups.flatMap(g => g.items);
  const [dark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { updating, lastCommit, error: updateError, trigger: triggerUpdate } = useUpdateStatus(isAdmin);
  const coreOnline = useCoreConnection();

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
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,hsl(var(--primary)),hsl(var(--accent)))] text-sm font-bold text-primary-foreground shadow-lg shadow-black/20">
              O
            </div>
            <div>
              <p className="text-[0.7rem] uppercase tracking-[0.24em] text-[hsl(var(--sidebar-muted))]">{t("layout.controlFabric")}</p>
              <h1 className="text-lg font-semibold text-[hsl(var(--sidebar-foreground))]">HydraHive</h1>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="rounded-xl p-2 text-[hsl(var(--sidebar-muted))] hover:bg-white/10 hover:text-[hsl(var(--sidebar-foreground))] lg:hidden"
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
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-4">
        {groups.map((group, gi) => (
          <div key={gi}>
            {group.label && (
              <p className="px-2 mb-1 text-[0.6rem] uppercase tracking-[0.2em] text-[hsl(var(--sidebar-muted))] select-none">
                {group.label}
              </p>
            )}
            <div className="space-y-1">
              {group.items.map(({ to, icon: Icon, label, hint }) => (
                <NavLink
                  key={to}
                  to={to}
                  title={hint}
                  className={({ isActive }) => cn("nav-item", isActive && "nav-item-active")}
                >
                  <Icon className="h-4 w-4 flex-shrink-0" />
                  <span className="truncate">{label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-[hsl(var(--sidebar-border))] p-3">
        {isAdmin && (
          <div className="mb-3 rounded-2xl border border-white/10 bg-white/5 p-3 text-xs text-[hsl(var(--sidebar-foreground))]">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">{t("layout.deployment")}</span>
              <span className={cn("status-pill", updating ? "bg-white/10 text-[hsl(var(--sidebar-foreground))]" : "status-pill-ok")}>{updating ? t("layout.running") : t("layout.ready")}</span>
            </div>
            <p className="mt-2 text-[hsl(var(--sidebar-muted))]">
              {lastCommit ? t("layout.lastCommit", { commit: lastCommit }) : t("layout.noCommit")}
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

  return (
    <div className="app-shell lg:grid lg:h-screen lg:grid-cols-[18rem_minmax(0,1fr)] lg:overflow-hidden">
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

      <main className="relative min-w-0 lg:flex lg:h-screen lg:flex-col lg:overflow-hidden">
        <div className="sticky top-0 z-20 border-b border-border/60 bg-[hsl(var(--shell))/0.82] px-4 py-4 backdrop-blur md:px-6 lg:px-8">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                className="rounded-2xl border bg-card/70 p-2.5 text-foreground shadow-sm lg:hidden"
              >
                <Menu className="h-5 w-5" />
              </button>
              <div>
                <p className="text-[0.7rem] uppercase tracking-[0.24em] text-muted-foreground">{t("layout.operationsConsole")}</p>
                <h2 className="text-xl font-semibold tracking-tight">{activeItem?.label ?? "HydraHive"}</h2>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="status-pill">{activeItem?.hint ?? t("layout.systemView")}</span>
              <span className={cn("status-pill", updating ? "bg-accent/15 text-accent" : "status-pill-ok")}>
                {updating ? t("layout.updateActive") : t("layout.systemReady")}
              </span>
              <NotificationBell />
            </div>
          </div>
        </div>

        <div className="px-4 py-4 md:px-6 md:py-6 lg:flex-1 lg:overflow-y-auto lg:px-8 lg:py-8">
          <Outlet />
        </div>
      </main>
      <SupportWidget />
    </div>
  );
}
