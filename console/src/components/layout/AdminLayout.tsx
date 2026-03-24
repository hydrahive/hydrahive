import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Bot,
  FolderKanban,
  Server,
  Wrench,
  Users,
  LogOut,
  ShieldCheck,
  Archive,
  Sun,
  Moon,
  Sparkles,
  RefreshCw,
  Menu,
  X,
  Settings,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";

const navAll = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard", hint: "Lagebild" },
  { to: "/my-agent", icon: Sparkles, label: "Mein Agent", hint: "Persoenlicher Kanal" },
  { to: "/projects", icon: FolderKanban, label: "Projekte", hint: "Queues und Chats" },
  { to: "/tools", icon: Wrench, label: "Tools", hint: "Aktionen und Skills" },
];

const navAdmin = [
  { to: "/agents",   icon: Bot,        label: "Agenten",          hint: "Runtime und Profile" },
  { to: "/system",   icon: Server,     label: "System",           hint: "Host und Dienste" },
  { to: "/users",    icon: Users,      label: "Benutzer",         hint: "Accounts und Rollen" },
  { to: "/audit",    icon: ShieldCheck,label: "Audit-Log",        hint: "Nachvollziehbarkeit" },
  { to: "/backup",   icon: Archive,    label: "Backup",           hint: "Snapshots und Restore" },
  { to: "/settings", icon: Settings,   label: "Einstellungen",    hint: "LLM, MCP, Gitea, VPN" },
];

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
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const nav = isAdmin ? [...navAll, ...navAdmin] : navAll;
  const [dark, toggleDark] = useDarkMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { updating, lastCommit, error: updateError, trigger: triggerUpdate } = useUpdateStatus(isAdmin);

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
              <p className="text-[0.7rem] uppercase tracking-[0.24em] text-[hsl(var(--sidebar-muted))]">Control Fabric</p>
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
            <span className="font-medium">{user?.username ?? "unbekannt"}</span>
            {isAdmin && <span className="rounded-full bg-white/10 px-2 py-0.5 text-[0.65rem] uppercase tracking-[0.18em]">admin</span>}
          </div>
          <p className="mt-1 text-xs text-[hsl(var(--sidebar-muted))]">Hybrid-Konsole fuer Agenten, Runtime und Systeme.</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
        {nav.map(({ to, icon: Icon, label, hint }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => cn("nav-item", isActive && "nav-item-active")}
          >
            <Icon className="h-4 w-4 flex-shrink-0" />
            <div className="min-w-0">
              <div className="truncate">{label}</div>
              <div className="truncate text-xs text-[hsl(var(--sidebar-muted))]">{hint}</div>
            </div>
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-[hsl(var(--sidebar-border))] p-3">
        {isAdmin && (
          <div className="mb-3 rounded-2xl border border-white/10 bg-white/5 p-3 text-xs text-[hsl(var(--sidebar-foreground))]">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Deployment</span>
              <span className={cn("status-pill", updating ? "bg-white/10 text-[hsl(var(--sidebar-foreground))]" : "status-pill-ok")}>{updating ? "laeuft" : "bereit"}</span>
            </div>
            <p className="mt-2 text-[hsl(var(--sidebar-muted))]">
              {lastCommit ? `Letzter Stand ${lastCommit}` : "Kein Commit-Stand vorhanden"}
            </p>
            {updateError && <p className="mt-2 text-[#ffd0d0]">{updateError}</p>}
            <button
              onClick={triggerUpdate}
              disabled={updating}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl bg-white/10 px-3 py-2 font-medium text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", updating && "animate-spin")} />
              {updating ? "Update laeuft" : "Update ausloesen"}
            </button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={toggleDark}
            className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:bg-white/10"
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {dark ? "Hell" : "Dunkel"}
          </button>
          <button
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-[hsl(var(--sidebar-foreground))] transition hover:border-red-300/20 hover:bg-red-500/10 hover:text-red-100"
          >
            <LogOut className="h-4 w-4" />
            Ende
          </button>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="app-shell lg:grid lg:h-screen lg:grid-cols-[18rem_minmax(0,1fr)] lg:overflow-hidden">
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
                <p className="text-[0.7rem] uppercase tracking-[0.24em] text-muted-foreground">Operations Console</p>
                <h2 className="text-xl font-semibold tracking-tight">{activeItem?.label ?? "HydraHive"}</h2>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="status-pill">{activeItem?.hint ?? "Systemansicht"}</span>
              <span className={cn("status-pill", updating ? "bg-accent/15 text-accent" : "status-pill-ok")}>
                {updating ? "Update aktiv" : "System bereit"}
              </span>
            </div>
          </div>
        </div>

        <div className="px-4 py-4 md:px-6 md:py-6 lg:flex-1 lg:overflow-y-auto lg:px-8 lg:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
