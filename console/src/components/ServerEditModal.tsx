/**
 * ServerEditModal.tsx — Modal für Create/Update eines Remote-Servers.
 *
 * Wiederverwendet von:
 * - AgentsPage (Legacy agents-legacy/servers)
 * - TargetSystemsPage (#584-B)
 *
 * Props-only, kein globaler State. Selbständig i18n-fähig.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { X, Save } from "lucide-react";

export interface RemoteServer {
  id: string;
  name: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  description: string;
  has_ssh_key?: boolean;
}

export interface ServerEditPayload {
  name: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  description: string;
  use_wks_key?: boolean;
}

interface Props {
  server: RemoteServer | null;
  onSave: (srv: ServerEditPayload) => void;
  onClose: () => void;
}

export function ServerEditModal({ server, onSave, onClose }: Props) {
  const { t } = useTranslation();
  const [name,    setName]    = useState(server?.name || "");
  const [ip,      setIp]      = useState(server?.ip || "");
  const [sshUser, setSshUser] = useState(server?.ssh_user || "root");
  const [sshPort, setSshPort] = useState(server?.ssh_port || 22);
  const [desc,    setDesc]    = useState(server?.description || "");
  const [useWksKey, setUseWksKey] = useState(false);

  const isEdit = !!server;

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center">
      <div className="bg-card border rounded-2xl shadow-2xl max-w-lg w-full mx-4 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">
            {isEdit
              ? t("serverEditModal.titleEdit", { defaultValue: "Server bearbeiten" })
              : t("serverEditModal.titleNew",  { defaultValue: "Neuer Server" })}
          </h2>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-muted">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              {t("serverEditModal.name", { defaultValue: "Name" })}
            </label>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder={t("serverEditModal.namePlaceholder", { defaultValue: "Mein Home-Server" })}
              className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">
                {t("serverEditModal.ipHost", { defaultValue: "IP / Hostname" })}
              </label>
              <input value={ip} onChange={e => setIp(e.target.value)} placeholder="192.168.1.100"
                className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("serverEditModal.sshUser", { defaultValue: "SSH-User" })}
                </label>
                <input value={sshUser} onChange={e => setSshUser(e.target.value)} placeholder="root"
                  className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("serverEditModal.port", { defaultValue: "Port" })}
                </label>
                <input type="number" value={sshPort} onChange={e => setSshPort(Number(e.target.value))}
                  className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
              </div>
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              {t("serverEditModal.description", { defaultValue: "Beschreibung" })}
            </label>
            <input value={desc} onChange={e => setDesc(e.target.value)}
              placeholder={t("serverEditModal.descPlaceholder", { defaultValue: "Home-Lab Ubuntu Server" })}
              className="w-full mt-1 rounded-lg border bg-background px-3 py-2 text-sm" />
          </div>
          {!isEdit && (
            <label className="flex items-center gap-2 cursor-pointer rounded-lg border bg-muted/30 px-3 py-2">
              <input type="checkbox" checked={useWksKey} onChange={e => setUseWksKey(e.target.checked)} className="rounded" />
              <div>
                <span className="text-sm font-medium">
                  {t("serverEditModal.useWksKey", { defaultValue: "Bestehenden WKS-Key verwenden" })}
                </span>
                <p className="text-xs text-muted-foreground">
                  {t("serverEditModal.useWksKeyHint", { defaultValue: "Nutzt den SSH-Key aus \"Mein Agent → WKS\" statt einen neuen zu generieren. Sinnvoll wenn der Key dort schon auf dem Ziel-Server eingerichtet ist." })}
                </p>
              </div>
            </label>
          )}
        </div>
        <div className="flex justify-end pt-2">
          <button
            onClick={() => onSave({ name, ip, ssh_user: sshUser, ssh_port: sshPort, description: desc, use_wks_key: useWksKey })}
            className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <Save size={14} />
            {isEdit
              ? t("serverEditModal.save", { defaultValue: "Speichern" })
              : useWksKey
                ? t("serverEditModal.createWithWksKey", { defaultValue: "Server anlegen (WKS-Key)" })
                : t("serverEditModal.createWithNewKey", { defaultValue: "Server anlegen + Key generieren" })}
          </button>
        </div>
      </div>
    </div>
  );
}
