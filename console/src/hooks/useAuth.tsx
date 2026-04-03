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
  const [user, setUser] = useState<AuthUser | null>(() => {
    const t = localStorage.getItem("hydrahive_token");
    const u = localStorage.getItem("hydrahive_user");
    const r = localStorage.getItem("hydrahive_role") ?? "user";
    const g = localStorage.getItem("hydrahive_group") ?? "standard";
    let p: GroupPermissions = ALL_PERMS;
    try { p = JSON.parse(localStorage.getItem("hydrahive_permissions") || "null") ?? ALL_PERMS; } catch { /* */ }
    return t && u ? { token: t, username: u, role: r, group: g, permissions: p } : null;
  });

  async function login(username: string, password: string) {
    // Clear stale token before login so pending requests with old token
    // won't trigger logout after we receive a fresh token
    localStorage.removeItem("hydrahive_token");
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || "Login fehlgeschlagen");
    }
    const data  = await res.json();
    const token = data.access_token;
    const role  = data.role  ?? "user";
    const group = data.group ?? "standard";

    const permissions = data.permissions ?? ALL_PERMS;
    setUser({ username: data.username ?? username, token, role, group, permissions });
    localStorage.setItem("hydrahive_token", token);
    localStorage.setItem("hydrahive_user",  username);
    localStorage.setItem("hydrahive_role",  role);
    localStorage.setItem("hydrahive_group", group);
    localStorage.setItem("hydrahive_permissions", JSON.stringify(permissions));
  }

  function logout() {
    setUser(null);
    localStorage.removeItem("hydrahive_token");
    localStorage.removeItem("hydrahive_user");
    localStorage.removeItem("hydrahive_role");
    localStorage.removeItem("hydrahive_group");
    localStorage.removeItem("hydrahive_permissions");
    sessionStorage.removeItem("hh_wizard_done");
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
