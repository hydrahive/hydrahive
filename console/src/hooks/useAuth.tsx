import { createContext, useContext, useEffect, useState, ReactNode } from "react";

interface GroupPermissions { pages: string[]; tools: string[]; plugins: string[]; agents: string[]; }
interface AuthUser { username: string; token: string; role: string; group: string; permissions?: GroupPermissions; }
interface AuthCtx  {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  permissions: GroupPermissions;
  hasPageAccess(pageId: string): boolean;
  login(u: string, p: string): Promise<void>;
  logout(): void;
}

const ALL_PERMS: GroupPermissions = { pages: ["*"], tools: ["*"], plugins: ["*"], agents: ["*"] };

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // #770: Kein localStorage fuer Token mehr. Auth-State kommt nach Mount
  // von GET /auth/me (Cookie-auth) — kein Bearer noetig.
  const [user, setUser] = useState<AuthUser | null>(null);

  // Nach Mount: Cookie-basierte /auth/me-Abfrage fuer Wiederherstellung
  // des Auth-State nach Page-Reload (F5).
  useEffect(() => {
    let cancelled = false;
    async function restore() {
      try {
        const res = await fetch("/api/auth/me", { credentials: "include" });
        if (cancelled || !res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        // Token nicht mehr aus /auth/me — wir speichern ihn nur in React-State.
        // login() setzt den Cookie, danach holt restore() den State.
        // Fuer die Session brauchen wir keinen persistenten Token mehr.
        setUser({
          username: data.username,
          token: "",  // nicht mehr persistent
          role: data.role,
          group: data.group ?? "standard",
          permissions: data.permissions ?? ALL_PERMS,
        });
      } catch { /* offline / not logged in */ }
    }
    restore();
    return () => { cancelled = true; };
  }, []);

  async function login(username: string, password: string) {
    // #770: Cookie-Pfad + Body-Token fuer CLI/MCP. Browser nutzt Cookie.
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || "Login fehlgeschlagen");
    }
    const data  = await res.json();
    const role  = data.role  ?? "user";
    const group = data.group ?? "standard";
    const permissions = data.permissions ?? ALL_PERMS;

    // #770: Token NICHT mehr in localStorage. Nur React-State + httpOnly-Cookie.
    setUser({ username: data.username ?? username, token: data.access_token ?? "", role, group, permissions });
  }

  function logout() {
    setUser(null);
    // localStorage fuer Token/User/Role/Group/Permissions aufraeumen — aber
    // since #770 werden die nicht mehr geschrieben, nur noch alte Eintraege
    // cleanern (Migration fuer bestehende Browser-Sessions).
    localStorage.removeItem("hydrahive_token");
    localStorage.removeItem("hydrahive_user");
    localStorage.removeItem("hydrahive_role");
    localStorage.removeItem("hydrahive_group");
    localStorage.removeItem("hydrahive_permissions");
    sessionStorage.removeItem("hh_wizard_done");
    // Cookie-Logout: Backend + Cookie loeschen
    fetch("/api/auth/logout", { method: "POST", credentials: "include" }).catch(() => {});
  }

  useEffect(() => {
    const onAuthExpired = () => logout();
    window.addEventListener("hydrahive-auth-expired", onAuthExpired);
    return () => window.removeEventListener("hydrahive-auth-expired", onAuthExpired);
  }, []);

  const perms = user?.permissions ?? ALL_PERMS;
  const isAdmin = user?.role === "admin";

  function hasPageAccess(pageId: string): boolean {
    if (isAdmin) return true;
    const pages = perms.pages ?? [];
    return pages.includes("*") || pages.includes(pageId);
  }

  return (
    <Ctx.Provider value={{
      user,
      isAuthenticated: !!user,
      isAdmin,
      permissions: perms,
      hasPageAccess,
      login,
      logout,
    }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth ausserhalb AuthProvider");
  return ctx;
}
