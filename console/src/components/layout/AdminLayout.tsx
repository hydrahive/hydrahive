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
  MessageSquare,
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
  Package,
  Store,
  KeyRound,
  Brain,
  Mic,
  ChevronDown,
  Users,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";
import i18n from "@/lib/i18n";

function useUpdateStatus(isAdmin: boolean) {
  const [updating, setUpdating] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [lastCommit, setLastCommit] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const s = await api.updateStatus();
      setUpdateAvailable(Boolean(s.available));
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
          setUpdateAvailable(Boolean(s.available));
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
      setUpdateAvailable(false);
      setError(e instanceof Error ? e.message : "Fehler");
    }
  }, []);

  return { updating, updateAvailable, lastCommit, error, trigger };
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

  type NavItem = { to: string; icon: React.ElementType; label: string; hint: string };
  type NavGroup = { id: string; label?: string; collapsible?: boolean; items: NavItem[] };

  const groupDashboard: NavGroup = {
    id: "dashboard",
    items: [
      { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard"), hint: t("navHint.dashboard") },
    ],
  };

  const groupAssistant: NavGroup = {
    id: "assistant",
    items: [
      { to: "/my-agent", icon: Sparkles, label: t("nav.myAgent"), hint: t("navHint.myAgent") },
    ],
  };

  const groupAgents: NavGroup = {
    id: "agents",
    label: "Agenten & Projekte",
    collapsible: true,
    items: [
      { to: "/agents",              icon: Bot,         label: t("nav.agents"),       hint: t("navHint.agents") },
      { to: "/projects",            icon: FolderKanban,label: t("nav.projects"),     hint: t("navHint.projects") },
      { to: "/tools",               icon: Wrench,      label: t("nav.tools"),        hint: t("navHint.tools") },
      { to: "/tools/skill-packages",icon: Package,     label: t("nav.skillPackages"),hint: t("navHint.skillPackages") },
      { to: "/blueprint",           icon: Workflow,    label: t("nav.blueprint"),    hint: t("navHint.blueprint") },
      { to: "/hub",                 icon: Store,       label: "HydraHub",            hint: "Agenten & Tools installieren" },
    ],
  };

  const groupAnalytics: NavGroup = {
    id: "analytics",
    label: "Analytik",
    collapsible: true,
    items: [
      { to: "/activity",  icon: Activity,   label: t("nav.activity"),  hint: t("navHint.activity") },
      { to: "/usage",     icon: BarChart2,  label: t("nav.usage"),     hint: t("navHint.usage") },
      { to: "/audit",     icon: ShieldCheck,label: t("nav.auditLog"),  hint: t("navHint.auditLog") },
      { to: "/schedules", icon: Calendar,   label: t("nav.schedules"), hint: t("navHint.schedules") },
    ],
  };

  const groupKnowledge: NavGroup = {
    id: "knowledge",
    label: "Wissen & Suche",
    collapsible: true,
    items: [
      { to: "/brain",       icon: Brain,  label: "HydraBrain",    hint: "3D-Graph: Agenten, Tools & Memory" },
      { to: "/search",      icon: Search, label: t("nav.search"), hint: t("navHint.search") },
      { to: "/code-editor", icon: Code2,  label: t("nav.codeEditor"), hint: t("navHint.codeEditor") },
    ],
  };

  const groupNetwork: NavGroup = {
    id: "network",
    label: "Netzwerk",
    collapsible: true,
    items: [
      { to: "/federation",  icon: Globe,  label: t("nav.federation"),  hint: t("navHint.federation") },
      { to: "/voice",       icon: Mic,    label: "Voice",              hint: "Sprachsteuerung — STT, TTS, Agent" },
      { to: "/extensions",  icon: Puzzle, label: t("nav.extensions"),  hint: t("navHint.extensions") },
      { to: "/plugins",     icon: Puzzle, label: "Plugins",            hint: "Plugin-System verwalten" },
    ],
  };

  const groupSystem: NavGroup = {
    id: "system",
    label: t("nav.groupSystem"),
    collapsible: true,
    items: [
      { to: "/config-hub",     icon: Settings,  label: "Setup",          hint: "Zentrale Konfiguration — alles an einem Ort" },
      { to: "/usermanagement", icon: Users,     label: "Usermanagement", hint: "Benutzer, Gruppen & Berechtigungen" },
      { to: "/system",         icon: Server,    label: t("nav.system"),  hint: t("navHint.system") },
      { to: "/secrets",        icon: KeyRound,  label: "Secrets",        hint: "API-Keys & Tokens für Agenten" },
      { to: "/settings",       icon: Settings,  label: t("nav.settings"),hint: t("navHint.settings") },
    ],
  };

  // Gruppen-Berechtigungen: Seiten nach Permissions filtern
  const filterByPerms = (group: NavGroup): NavGroup => {
    if (isAdmin) return group; // Admins sehen alles
    return {
      ...group,
      items: group.items.filter(item => {
        const pageId = item.to.replace(/^\//, "");
        return hasPageAccess(pageId);
      }),
    };
  };

  const allGroups = [groupDashboard, groupAssistant, groupAgents, groupAnalytics, groupKnowledge, groupNetwork, groupSystem];
  const groups: NavGroup[] = allGroups
    .map(filterByPerms)
    .filter(g => g.items.length > 0);

  const nav = groups.flatMap(g => g.items);
  const [dark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { updating, updateAvailable, lastCommit, error: updateError, trigger: triggerUpdate } = useUpdateStatus(isAdmin);
  const coreOnline = useCoreConnection();
  const showDeploymentPanel = isAdmin && (updating || Boolean(updateError) || updateAvailable);
  const deploymentUrgent = updating || Boolean(updateError) || updateAvailable;

  const NAV_OPEN_GROUP_KEY = "hh_nav_open_group";

  function getGroupIdForPath(pathname: string) {
    for (const g of [groupAgents, groupAnalytics, groupKnowledge, groupNetwork, groupSystem]) {
      if (g.items.some(({ to }) => pathname === to || pathname.startsWith(`${to}/`))) return g.id;
    }
    return null;
  }

  const [openGroupId, setOpenGroupId] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(NAV_OPEN_GROUP_KEY);
      if (["agents", "analytics", "knowledge", "network", "system"].includes(stored ?? "")) return stored;
    } catch {
      // ignore
    }
    return getGroupIdForPath(location.pathname) ?? "workspace";
  });

  const toggleGroup = useCallback((groupId: string) => {
    setOpenGroupId((prev) => {
      const next = prev === groupId ? null : groupId;
      try {
        if (next) localStorage.setItem(NAV_OPEN_GROUP_KEY, next);
        else localStorage.removeItem(NAV_OPEN_GROUP_KEY);
      } catch {
        // ignore
      }
      return next;
    });
  }, []);

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
              <a href="https://hydrahive.org" target="_blank" rel="noopener noreferrer"
                className="text-[0.65rem] text-cyan-400/70 hover:text-cyan-300 transition-colors tracking-wide">
                hydrahive.org
              </a>
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

      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1.5">
        {groups.map((group) => {
          const collapsed = Boolean(group.collapsible && openGroupId !== group.id);
          const toggle = group.collapsible ? () => toggleGroup(group.id) : undefined;
          const hasActiveChild = group.items.some(({ to }) => location.pathname === to || location.pathname.startsWith(`${to}/`));

          return (
            <div key={group.id}>
              {group.label && group.collapsible ? (
                <button
                  type="button"
                  onClick={toggle}
                  aria-expanded={!collapsed}
                  className={cn(
                    "flex w-full items-center justify-between px-3 py-2 mb-0.5 rounded-xl text-xs font-medium transition-colors select-none",
                    hasActiveChild
                      ? "bg-orange-500/15 text-orange-300 border border-orange-500/30"
                      : "text-[hsl(var(--sidebar-muted))] hover:text-[hsl(var(--sidebar-foreground))] hover:bg-white/5 border border-transparent"
                  )}
                >
                  <span>{group.label}</span>
                  <ChevronDown className={cn("h-3.5 w-3.5 transition-transform duration-200", collapsed && "-rotate-90")} />
                </button>
              ) : group.label ? (
                <p className="px-3 mb-1 text-xs font-medium text-[hsl(var(--sidebar-muted))] select-none">
                  {group.label}
                </p>
              ) : null}

              {!collapsed && (
                <div className="space-y-0.5 mb-1.5 ml-1">
                  {group.items.map(({ to, icon: Icon, label, hint }) => (
                    <NavLink
                      key={to}
                      to={to}
                      title={hint}
                      className={({ isActive }) => cn(
                        "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                        isActive
                          ? "bg-orange-500/20 text-orange-200 border border-orange-500/30 font-medium"
                          : "text-[hsl(var(--sidebar-foreground))] hover:bg-white/8 border border-transparent"
                      )}
                    >
                      <Icon className="h-4 w-4 flex-shrink-0" />
                      <span className="truncate">{label}</span>
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="border-t border-[hsl(var(--sidebar-border))] p-3">
        {showDeploymentPanel && (
          <div className="mb-3 rounded-2xl border border-red-400/30 bg-gradient-to-br from-red-500/15 via-red-500/10 to-rose-500/10 p-3 text-xs text-[hsl(var(--sidebar-foreground))] shadow-[0_0_0_1px_rgba(248,113,113,0.12),0_18px_40px_rgba(239,68,68,0.18)] backdrop-blur">
            <div className="flex items-center justify-between gap-3">
              <span className="font-extrabold tracking-[0.18em] text-red-200">{deploymentUrgent ? t("layout.updateAlertTitle") : t("layout.deployment")}</span>
              <span className={cn("status-pill", deploymentUrgent ? "bg-red-500/20 text-red-100" : "status-pill-ok")}>
                {updating ? t("layout.running") : updateAvailable ? t("layout.updateAvailable") : t("layout.ready")}
              </span>
            </div>
            <p className="mt-2 text-[hsl(var(--sidebar-muted))]">
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

  // Bottom-Nav für Mobile — die 5 wichtigsten Punkte
  const bottomNavItems = [
    { to: "/dashboard", icon: LayoutDashboard, label: t("nav.dashboard") },
    { to: "/projects",  icon: FolderKanban,    label: t("nav.projects") },
    { to: "/my-agent",  icon: Sparkles,        label: t("nav.myAgent") },
    { to: "/agents",    icon: Bot,             label: t("nav.agents") },
    { to: "/settings",  icon: Settings,        label: t("nav.settings") },
  ];

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

        {/* Content — Extra Padding unten auf Mobile für Bottom-Nav */}
        <div className="px-3 py-3 pb-20 sm:px-4 sm:py-4 md:px-6 md:py-6 lg:flex-1 lg:overflow-y-auto lg:px-8 lg:py-8 lg:pb-8">
          <Outlet />
        </div>
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
            <span className="text-[0.6rem] font-medium">Mehr</span>
          </button>
        </div>
      </nav>

      <SupportWidget />
    </div>
  );
}
