import { createContext, useContext, useState, ReactNode } from "react";
interface AuthUser { username: string; token: string; }
interface AuthCtx  { user: AuthUser|null; isAuthenticated: boolean; login(u:string,p:string): Promise<void>; logout(): void; }
const Ctx = createContext<AuthCtx|null>(null);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser|null>(() => {
    const t=localStorage.getItem("octopos_token"), u=localStorage.getItem("octopos_user");
    return t&&u ? {token:t,username:u} : null;
  });
  async function login(username: string, password: string) {
    const res = await fetch("/api/auth/login", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({username,password}) });
    if (!res.ok) { const e=await res.json().catch(()=>({})); throw new Error(e.detail||"Login fehlgeschlagen"); }
    const data = await res.json();
    setUser({username, token:data.access_token});
    localStorage.setItem("octopos_token", data.access_token);
    localStorage.setItem("octopos_user", username);
  }
  function logout() { setUser(null); localStorage.removeItem("octopos_token"); localStorage.removeItem("octopos_user"); }
  return <Ctx.Provider value={{user, isAuthenticated:!!user, login, logout}}>{children}</Ctx.Provider>;
}
export function useAuth() { const ctx=useContext(Ctx); if(!ctx) throw new Error("useAuth ausserhalb AuthProvider"); return ctx; }
