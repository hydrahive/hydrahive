import { createContext, useContext, useEffect, useState, ReactNode } from "react";

interface AuthUser { username: string; token: string; role: string; }
interface AuthCtx  {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login(u: string, p: string): Promise<void>;
  logout(): void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const t = localStorage.getItem("octopos_token");
    const u = localStorage.getItem("octopos_user");
    const r = localStorage.getItem("octopos_role") ?? "user";
    return t && u ? { token: t, username: u, role: r } : null;
  });

  async function login(username: string, password: string) {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || "Login fehlgeschlagen");
    }
    const data = await res.json();
    const token = data.access_token;
    const role  = data.role ?? "user";

    setUser({ username: data.username ?? username, token, role });
    localStorage.setItem("octopos_token", token);
    localStorage.setItem("octopos_user", username);
    localStorage.setItem("octopos_role", role);
  }

  function logout() {
    setUser(null);
    localStorage.removeItem("octopos_token");
    localStorage.removeItem("octopos_user");
    localStorage.removeItem("octopos_role");
  }

  useEffect(() => {
    const onAuthExpired = () => logout();
    window.addEventListener("octopos-auth-expired", onAuthExpired);
    return () => window.removeEventListener("octopos-auth-expired", onAuthExpired);
  }, []);

  return (
    <Ctx.Provider value={{
      user,
      isAuthenticated: !!user,
      isAdmin: user?.role === "admin",
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
