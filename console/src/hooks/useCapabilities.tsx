import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

interface Capability {
  name: string;
  installed: boolean;
  configured: boolean;
  active: boolean;
  status: "active" | "configured" | "installed" | "not_installed";
}

interface CapabilitiesCtx {
  capabilities: Record<string, Capability>;
  loading: boolean;
  refresh: () => void;
}

const Ctx = createContext<CapabilitiesCtx>({ capabilities: {}, loading: true, refresh: () => {} });

export function CapabilitiesProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  const [capabilities, setCapabilities] = useState<Record<string, Capability>>({});
  const [loading, setLoading] = useState(true);

  function load() {
    if (!isAuthenticated) return;
    api.get<{ capabilities: Capability[] }>("/capabilities")
      .then(d => {
        const map: Record<string, Capability> = {};
        for (const c of d.capabilities) map[c.name] = c;
        setCapabilities(map);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, [isAuthenticated]);

  return <Ctx.Provider value={{ capabilities, loading, refresh: load }}>{children}</Ctx.Provider>;
}

export function useCapabilities() {
  return useContext(Ctx);
}

/** Shortcut: ist ein Feature mindestens installiert+konfiguriert? */
export function useFeatureAvailable(name: string): boolean {
  const { capabilities } = useCapabilities();
  const cap = capabilities[name];
  return cap ? cap.configured || cap.active : false;
}
