import { useState, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { useTranslation } from "react-i18next";

export function LoginPage() {
  const { t } = useTranslation();
  const { login } = useAuth(); const navigate = useNavigate();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error,    setError]    = useState("");
  const [loading,  setLoading]  = useState(false);
  async function handleSubmit(e: FormEvent) {
    e.preventDefault(); setError(""); setLoading(true);
    try { await login(username, password); navigate("/dashboard"); }
    catch (err) { setError(err instanceof Error ? err.message : t("common.error")); }
    finally { setLoading(false); }
  }
  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-2">
          <img src="/hydrahive-logo.png" alt="HydraHive" className="w-16 h-16 mx-auto" />
          <h1 className="text-2xl font-semibold">HydraHive Console</h1>
          <p className="text-sm text-muted-foreground">{t("login.subtitle")}</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4 bg-card border rounded-lg p-6">
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="u">{t("login.username")}</label>
            <input id="u" type="text" value={username} onChange={e=>setUsername(e.target.value)}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" required autoFocus />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="p">{t("login.password")}</label>
            <input id="p" type="password" value={password} onChange={e=>setPassword(e.target.value)}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary" required />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
            {loading ? t("login.loggingIn") : t("login.loginBtn")}
          </button>
        </form>
      </div>
    </div>
  );
}
