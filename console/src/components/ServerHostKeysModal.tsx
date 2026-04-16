/**
 * ServerHostKeysModal.tsx — #674-B Sprint 3
 *
 * Admin-Modal für SSH-Host-Key-Pinning pro Server. Nutzt #674-A-Endpoints:
 *   GET    /admin/servers/{id}/hostkeys
 *   POST   /admin/servers/{id}/verify-hostkey    { fingerprint_sha256, action }
 *   DELETE /admin/servers/{id}/hostkeys/{fingerprint}
 *   GET    /admin/servers/{id}/test              (Discovery: erfasst Keys)
 *
 * Rescan läuft über den bestehenden Test-Endpoint — /test scannt den Host
 * per ssh-keyscan und speichert neue Keys als status=unverified.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X, ShieldCheck, ShieldAlert, ShieldQuestion, ShieldX } from "lucide-react";
import { api } from "@/lib/api";

export type HostKeyStatus = "verified" | "unverified" | "changed" | "unknown";

export interface HostKeyEntry {
  fingerprint_sha256: string;
  algorithm:          string;
  status:             "verified" | "unverified";
  verified_at?:       string | null;
  verified_by?:       string | null;
  verified_method?:   string | null;
}

export interface HostKeysResponse {
  server_id:        string;
  status:           HostKeyStatus;
  ip?:              string;
  ssh_port?:        number;
  ssh_user?:        string;
  last_checked?:    string | null;
  host_keys:        HostKeyEntry[];
  enforcement_mode: "warn" | "strict";
}

export function HostStatusBadge({ status }: { status: HostKeyStatus }) {
  const { t } = useTranslation();
  const cls = {
    verified:   "bg-green-500/10 text-green-500",
    unverified: "bg-amber-500/10 text-amber-500",
    changed:    "bg-red-500/10 text-red-500",
    unknown:    "bg-muted text-muted-foreground",
  }[status];
  const Icon = {
    verified:   ShieldCheck,
    unverified: ShieldQuestion,
    changed:    ShieldX,
    unknown:    ShieldAlert,
  }[status];
  const label = t(`targetSystems.hostkeys.statusBadge.${status}`, { defaultValue: status });
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${cls}`}>
      <Icon size={12} />
      {label}
    </span>
  );
}

function KeyStatusBadge({ status }: { status: "verified" | "unverified" }) {
  const { t } = useTranslation();
  const cls = status === "verified"
    ? "bg-green-500/10 text-green-500"
    : "bg-amber-500/10 text-amber-500";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${cls}`}>
      {t(`targetSystems.hostkeys.statusBadge.${status}`, { defaultValue: status })}
    </span>
  );
}

export interface ServerHostKeysModalProps {
  serverId: string;
  onClose: () => void;
  onStatusChanged?: (status: HostKeyStatus) => void;
}

export function ServerHostKeysModal({ serverId, onClose, onStatusChanged }: ServerHostKeysModalProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<HostKeysResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState<string>("");

  async function reload() {
    setLoading(true);
    setErr("");
    try {
      const d = await api.get<HostKeysResponse>(`/admin/servers/${serverId}/hostkeys`);
      setData(d);
      onStatusChanged?.(d.status);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId]);

  async function approve(fp: string) {
    setBusy(fp);
    setErr("");
    try {
      await api.post(`/admin/servers/${serverId}/verify-hostkey`, {
        fingerprint_sha256: fp,
        action: "approve",
      });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    } finally {
      setBusy("");
    }
  }

  async function remove(fp: string) {
    if (!confirm(t("targetSystems.hostkeys.confirmRemove", {
      defaultValue: "Diesen Host-Key wirklich entfernen?",
    }))) return;
    setBusy(fp);
    setErr("");
    try {
      await api.delete(`/admin/servers/${serverId}/hostkeys/${encodeURIComponent(fp)}`);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    } finally {
      setBusy("");
    }
  }

  async function rescan() {
    setLoading(true);
    setErr("");
    try {
      await api.get(`/admin/servers/${serverId}/test`);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-card rounded-xl max-w-2xl w-full max-h-[85vh] overflow-hidden flex flex-col border shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <div>
            <h2 className="font-semibold text-sm">
              {t("targetSystems.hostkeys.title", { defaultValue: "SSH Host-Keys" })}
            </h2>
            <p className="text-xs text-muted-foreground font-mono mt-0.5">{serverId}</p>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label={t("common.close", { defaultValue: "Schließen" })}
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4 space-y-3">
          {err && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm">
              {err}
            </div>
          )}

          {loading && !data ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="animate-spin h-4 w-4" />
              {t("common.loading", { defaultValue: "Lade…" })}
            </div>
          ) : data ? (
            <>
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-3 text-xs flex-wrap">
                  <HostStatusBadge status={data.status} />
                  <span className="text-muted-foreground">
                    {t("targetSystems.hostkeys.enforcement", { defaultValue: "Modus" })}:
                    <span className="ml-1 font-mono">
                      {t(`targetSystems.hostkeys.enforcementMode.${data.enforcement_mode}`, {
                        defaultValue: data.enforcement_mode,
                      })}
                    </span>
                  </span>
                  {data.last_checked && (
                    <span className="text-muted-foreground">
                      {t("targetSystems.hostkeys.lastChecked", { defaultValue: "zuletzt geprüft" })}
                      {": "}
                      {new Date(data.last_checked).toLocaleString()}
                    </span>
                  )}
                </div>
                <button
                  onClick={rescan}
                  disabled={loading}
                  className="text-xs text-primary hover:underline disabled:opacity-50"
                >
                  {loading
                    ? t("targetSystems.hostkeys.scanning", { defaultValue: "Scanne…" })
                    : t("targetSystems.hostkeys.rescan", { defaultValue: "Neu scannen" })}
                </button>
              </div>

              {data.status === "changed" && (
                <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3 text-xs">
                  {t("targetSystems.hostkeys.changedWarning", {
                    defaultValue: "Host-Key unterscheidet sich vom gespeicherten — möglicher MITM-Verdacht. Neuen Fingerprint prüfen, bevor genehmigt wird.",
                  })}
                </div>
              )}

              {data.host_keys.length === 0 ? (
                <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                  {t("targetSystems.hostkeys.empty", {
                    defaultValue: "Keine Host-Keys erfasst. Klicke auf „Neu scannen\", um sie zu erfassen.",
                  })}
                </div>
              ) : (
                <ul className="space-y-2">
                  {data.host_keys.map((hk) => (
                    <li key={hk.fingerprint_sha256} className="rounded-lg border p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <KeyStatusBadge status={hk.status} />
                            <span className="text-xs font-mono text-muted-foreground">{hk.algorithm}</span>
                          </div>
                          <p className="text-xs font-mono break-all mt-1">{hk.fingerprint_sha256}</p>
                          {hk.verified_at && hk.status === "verified" && (
                            <p className="text-xs text-muted-foreground mt-1">
                              {t("targetSystems.hostkeys.approvedBy", { defaultValue: "genehmigt von" })}
                              {" "}
                              <span className="font-mono">{hk.verified_by || "—"}</span>
                              {" — "}
                              {new Date(hk.verified_at).toLocaleString()}
                            </p>
                          )}
                        </div>
                        <div className="flex gap-3 shrink-0">
                          {hk.status !== "verified" && (
                            <button
                              onClick={() => approve(hk.fingerprint_sha256)}
                              disabled={busy === hk.fingerprint_sha256}
                              className="text-xs text-green-500 hover:underline disabled:opacity-50"
                            >
                              {t("targetSystems.hostkeys.approve", { defaultValue: "Genehmigen" })}
                            </button>
                          )}
                          <button
                            onClick={() => remove(hk.fingerprint_sha256)}
                            disabled={busy === hk.fingerprint_sha256}
                            className="text-xs text-destructive hover:underline disabled:opacity-50"
                          >
                            {t("targetSystems.hostkeys.remove", { defaultValue: "Entfernen" })}
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
