import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

export function SetupPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== password2) { setError(t("setup.passwordMismatch")); return; }
    setLoading(true); setError("");
    try {
      await api.post("/setup", { username, password });
      navigate("/login");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Setup");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-xl bg-primary flex items-center justify-center mx-auto">
            <span className="text-primary-foreground font-bold text-xl">O</span>
          </div>
          <h1 className="text-2xl font-semibold">{t("setup.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("setup.subtitle")}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("setup.username")}</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_.-]/g, ""))}
              placeholder={t("setup.usernamePlaceholder")}
              required
              autoFocus
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <p className="text-xs text-muted-foreground">{t("setup.usernameHint")}</p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("setup.password")}</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={t("setup.passwordPlaceholder")}
              required
              minLength={8}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("setup.confirmPassword")}</label>
            <input
              type="password"
              value={password2}
              onChange={e => setPassword2(e.target.value)}
              placeholder={t("setup.confirmPasswordPlaceholder")}
              required
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <button
            type="submit"
            disabled={loading || !username || !password || !password2}
            className="w-full py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {loading ? t("setup.settingUp") : t("setup.setupBtn")}
          </button>
        </form>

        <p className="text-xs text-center text-muted-foreground">
          {t("setup.footer")}
        </p>
      </div>
    </div>
  );
}
