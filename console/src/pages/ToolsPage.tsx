import { useEffect, useState } from "react";
import { Wrench, RefreshCw, ShieldCheck, Code2 } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

interface ToolSchema {
  name:                 string;
  description:          string;
  permissions_required: string[];
  parameters: {
    type:       string;
    properties: Record<string, { type: string; description?: string; enum?: string[]; default?: unknown }>;
    required?:  string[];
  };
}

export function ToolsPage() {
  const { t } = useTranslation();
  const [tools,     setTools]     = useState<Record<string, ToolSchema>>({});
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [expanded,  setExpanded]  = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try {
      const data = await api.get<Record<string, ToolSchema>>("/tools");
      setTools(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally { setLoading(false); setRefreshing(false); }
  }

  useEffect(() => { load(); }, []);
  function refresh() { setRefreshing(true); load(); }
  function toggle(id: string) { setExpanded(e => e === id ? null : id); }

  const toolList = Object.entries(tools);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("tools.title")}</h1>
          <p className="text-xs text-muted-foreground">{t("pageDesc.tools")}</p>
          <p className="text-sm text-muted-foreground">
            {toolList.length !== 1
              ? t("tools.subtitlePlural", { count: toolList.length })
              : t("tools.subtitle", { count: toolList.length })}
          </p>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {t("tools.refresh")}
        </button>
      </div>

      {error && <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">{error}</div>}

      <div className="mb-6 rounded-xl border bg-muted/30 p-4 space-y-2">
        <h3 className="text-sm font-semibold">{t("tools.infoTitle")}</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">{t("tools.infoText")}</p>
      </div>

      {loading && (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="bg-card border rounded-lg p-4 animate-pulse h-16" />)}
        </div>
      )}

      {!loading && toolList.length === 0 && (
        <div className="bg-card border rounded-lg p-12 text-center">
          <Wrench className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
          <p className="text-sm text-muted-foreground">{t("tools.noTools")}</p>
        </div>
      )}

      {!loading && toolList.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {toolList.map(([id, tool]) => {
            const isOpen = expanded === id;
            const params = Object.entries(tool.parameters?.properties ?? {});
            const required = tool.parameters?.required ?? [];

            return (
              <div key={id} className="rounded-xl border overflow-hidden bg-card">
                <button
                  onClick={() => toggle(id)}
                  className="w-full flex items-start gap-2 p-3 text-left hover:bg-accent/50 transition-colors"
                >
                  <div className="w-6 h-6 rounded-md bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Wrench className="h-3.5 w-3.5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-xs">{tool.name}</span>
                      <code className="text-xs text-muted-foreground bg-muted px-1 py-0.5 rounded">{id}</code>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{tool.description}</p>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    {tool.permissions_required.length > 0 && (
                      <div className="flex items-center gap-0.5 text-xs text-orange-500">
                        <ShieldCheck className="h-3 w-3" />
                      </div>
                    )}
                    <span className="text-muted-foreground text-xs">{isOpen ? "▲" : "▼"}</span>
                  </div>
                </button>

                {isOpen && (
                  <div className="px-3 pb-3 space-y-3 border-t">
                    <p className="text-xs text-muted-foreground pt-2">{tool.description}</p>

                    {tool.permissions_required.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1">
                          <ShieldCheck className="h-3 w-3" />{t("tools.requiredPermissions")}
                        </p>
                        <div className="flex flex-wrap gap-1">
                          {tool.permissions_required.map(p => (
                            <span key={p} className="text-xs bg-orange-50 text-orange-700 border border-orange-200 px-1.5 py-0.5 rounded">
                              {p}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {params.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1">
                          <Code2 className="h-3 w-3" />{t("tools.parameters")}
                        </p>
                        <div className="space-y-1">
                          {params.map(([pname, pdef]) => (
                            <div key={pname} className="flex items-start gap-2 text-xs bg-muted/50 rounded px-2 py-1.5">
                              <div className="flex-1">
                                <span className="font-medium">{pname}</span>
                                {required.includes(pname) && (
                                  <span className="text-destructive ml-1">*</span>
                                )}
                                <span className="text-muted-foreground ml-2">{pdef.type}</span>
                                {pdef.enum && <span className="text-muted-foreground ml-1">({pdef.enum.join(" | ")})</span>}
                              </div>
                              {pdef.description && (
                                <span className="text-muted-foreground line-clamp-1">{pdef.description}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {t("tools.footer")}
      </p>
    </div>
  );
}
