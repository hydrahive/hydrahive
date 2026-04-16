/**
 * TargetSystemsPage.tsx — Zielsysteme (#584-B)
 *
 * Admin-Page: Root-/Remote-Server + WKS verwalten und Projekten many-to-many
 * zuweisen. Nutzt die #584-A-Backend-API:
 *   GET/PUT /projects/{id}/targets
 *   GET /admin/servers (+ POST/PUT/DELETE/test/pubkey)
 *   GET /admin/wks
 *
 * Preservation-Garantie beim PUT: beide Listen (servers+wks) werden immer
 * komplett rebuildet, damit eine Server-Änderung nie die WKS-Liste löscht
 * und umgekehrt.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ServerCog, Plus, Pencil, Trash2, Check, Loader2, Monitor,
  FolderKanban, Copy, AlertCircle, AlertTriangle, ShieldAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { ServerEditModal, type RemoteServer, type ServerEditPayload } from "@/components/ServerEditModal";

// ─────────────────────────────────────────── Types

interface AdminWksEntry {
  username: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  configured: boolean;
  has_ssh_key: boolean;
}

interface ProjectSummary {
  id: string;
  name: string;
  description: string;
}

interface ProjectTargetServerAssignment {
  server_id: string;
  name: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  role: string;
  note: string;
  has_ssh_key: boolean;
  stale?: boolean;
}

interface ProjectTargetWksAssignment {
  username: string;
  ip: string;
  ssh_user: string;
  ssh_port: number;
  role: string;
  note: string;
  has_ssh_key: boolean;
}

interface ProjectTargetsResponse {
  project_id: string;
  etag: string;
  servers: ProjectTargetServerAssignment[];
  wks: ProjectTargetWksAssignment[];
}

interface TargetsConflict {
  currentEtag: string;
  message: string;
}

interface ProjectTargetsPutBody {
  servers: { server_id: string; role: string; note: string }[];
  wks:     { username: string; role: string; note: string }[];
}

// ─────────────────────────────────────────── Helpers

function buildPutBody(current: ProjectTargetsResponse | undefined): ProjectTargetsPutBody {
  return {
    servers: (current?.servers ?? []).map(s => ({
      server_id: s.server_id, role: s.role, note: s.note,
    })),
    wks: (current?.wks ?? []).map(w => ({
      username: w.username, role: w.role, note: w.note,
    })),
  };
}

// ─────────────────────────────────────────── Page

type TabId = "servers" | "wks" | "projects";

export function TargetSystemsPage() {
  const { t } = useTranslation();
  const { isAdmin } = useAuth();

  const [activeTab, setActiveTab] = useState<TabId>("servers");
  const [servers, setServers] = useState<RemoteServer[]>([]);
  const [wksEntries, setWksEntries] = useState<AdminWksEntry[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [targetsByProject, setTargetsByProject] = useState<Record<string, ProjectTargetsResponse>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  // #676: Per-Projekt Konflikt-State. Key = project_id.
  const [conflicts, setConflicts] = useState<Record<string, TargetsConflict>>({});

  async function loadAll() {
    setLoading(true);
    setError("");
    setConflicts({});
    try {
      const [serversRes, wksRes, projectsRes] = await Promise.all([
        api.get<{ servers: RemoteServer[] }>("/admin/servers"),
        api.get<{ wks: AdminWksEntry[] }>("/admin/wks"),
        api.get<Record<string, { name: string; description: string }>>("/projects"),
      ]);
      setServers(serversRes.servers);
      setWksEntries(wksRes.wks);

      const projList: ProjectSummary[] = Object.entries(projectsRes).map(
        ([id, p]) => ({ id, name: p.name || id, description: p.description || "" })
      );
      projList.sort((a, b) => a.name.localeCompare(b.name));
      setProjects(projList);

      // Targets parallel nachladen — Fehler pro Projekt nicht fatal
      const results = await Promise.all(
        projList.map(p =>
          api.get<ProjectTargetsResponse>(`/projects/${p.id}/targets`)
            .then(r => [p.id, r] as const)
            .catch(() => [p.id, null] as const)
        )
      );
      const map: Record<string, ProjectTargetsResponse> = {};
      for (const [pid, r] of results) {
        if (r) map[pid] = r;
      }
      setTargetsByProject(map);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { if (isAdmin) loadAll(); }, [isAdmin]);

  // ── Assignment-Mutations (zentral, damit Preservation garantiert ist)

  async function putTargets(projectId: string, body: ProjectTargetsPutBody) {
    setSaving(projectId);
    setError("");
    const etag = targetsByProject[projectId]?.etag ?? "";
    try {
      const resp = await api.putWithHeaders<ProjectTargetsResponse>(
        `/projects/${projectId}/targets`, body, {"If-Match": etag},
      );
      setTargetsByProject(prev => ({ ...prev, [projectId]: resp }));
      // #676: Erfolgreicher Save räumt einen evtl. vorherigen Konflikt weg
      setConflicts(prev => {
        if (!(projectId in prev)) return prev;
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
    } catch (e) {
      const err = e as Error & { status?: number; detail?: { message?: string; current_etag?: string } };
      if (err && (err.status === 409 || err.status === 428) && err.detail && typeof err.detail === "object") {
        // #676: 428 (fehlendes If-Match) + 409 (stale) landen im selben
        // Inline-Reload-Banner. Semantik für den Admin ist identisch:
        // Client-State veraltet, bitte neu laden.
        setConflicts(prev => ({
          ...prev,
          [projectId]: {
            currentEtag: err.detail?.current_etag || "",
            message: err.detail?.message || t("targetSystems.conflictDesc", {
              defaultValue: "Projekt-Targets wurden seit dem Laden geändert.",
            }),
          },
        }));
      } else {
        setError(err instanceof Error ? err.message : t("common.error", { defaultValue: "Fehler" }));
      }
    } finally {
      setSaving(null);
    }
  }

  async function upsertServerAssignment(projectId: string, serverId: string, role: string, note: string) {
    const body = buildPutBody(targetsByProject[projectId]);
    body.servers = body.servers.filter(s => s.server_id !== serverId);
    body.servers.push({ server_id: serverId, role, note });
    await putTargets(projectId, body);
  }

  async function removeServerAssignment(projectId: string, serverId: string) {
    const body = buildPutBody(targetsByProject[projectId]);
    body.servers = body.servers.filter(s => s.server_id !== serverId);
    await putTargets(projectId, body);
  }

  async function upsertWksAssignment(projectId: string, username: string, role: string, note: string) {
    const body = buildPutBody(targetsByProject[projectId]);
    body.wks = body.wks.filter(w => w.username !== username);
    body.wks.push({ username, role, note });
    await putTargets(projectId, body);
  }

  async function removeWksAssignment(projectId: string, username: string) {
    const body = buildPutBody(targetsByProject[projectId]);
    body.wks = body.wks.filter(w => w.username !== username);
    await putTargets(projectId, body);
  }

  // ── Reverse-Lookup (Server/WKS → welche Projekte?)

  const serverToProjects = useMemo(() => {
    const map: Record<string, { projectId: string; role: string; note: string }[]> = {};
    for (const [pid, t] of Object.entries(targetsByProject)) {
      for (const s of t.servers) {
        if (!map[s.server_id]) map[s.server_id] = [];
        map[s.server_id].push({ projectId: pid, role: s.role, note: s.note });
      }
    }
    return map;
  }, [targetsByProject]);

  const wksToProjects = useMemo(() => {
    const map: Record<string, { projectId: string; role: string; note: string }[]> = {};
    for (const [pid, t] of Object.entries(targetsByProject)) {
      for (const w of t.wks) {
        if (!map[w.username]) map[w.username] = [];
        map[w.username].push({ projectId: pid, role: w.role, note: w.note });
      }
    }
    return map;
  }, [targetsByProject]);

  // ── Access Guard

  if (!isAdmin) {
    return (
      <div className="p-6 max-w-2xl">
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-6 flex items-start gap-3">
          <ShieldAlert className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
          <div>
            <h2 className="text-base font-semibold text-destructive">
              {t("targetSystems.accessDenied.title", { defaultValue: "Admin-Zugriff erforderlich" })}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("targetSystems.accessDenied.desc", {
                defaultValue: "Diese Seite verwaltet Infrastruktur-Zuweisungen und ist nur für Admins zugänglich.",
              })}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <ServerCog className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-semibold">
            {t("targetSystems.title", { defaultValue: "Zielsysteme" })}
          </h1>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          {t("targetSystems.subtitle", {
            defaultValue: "WKS und Root-/Remote-Server verwalten und Projekten zuweisen.",
          })}
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2 flex items-center gap-2">
          <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b flex gap-1">
        <TabButton active={activeTab === "servers"}  onClick={() => setActiveTab("servers")}
          icon={<ServerCog className="h-4 w-4" />}
          label={t("targetSystems.tabs.servers",  { defaultValue: "Root-Server" })} />
        <TabButton active={activeTab === "wks"}      onClick={() => setActiveTab("wks")}
          icon={<Monitor className="h-4 w-4" />}
          label={t("targetSystems.tabs.wks",      { defaultValue: "WKS" })} />
        <TabButton active={activeTab === "projects"} onClick={() => setActiveTab("projects")}
          icon={<FolderKanban className="h-4 w-4" />}
          label={t("targetSystems.tabs.projects", { defaultValue: "Projekte" })} />
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground p-6">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">{t("common.loading", { defaultValue: "Lade…" })}</span>
        </div>
      ) : (
        <>
          {activeTab === "servers" && (
            <ServersView
              servers={servers}
              projects={projects}
              serverToProjects={serverToProjects}
              saving={saving}
              onReloadServers={loadAll}
              onUpsert={upsertServerAssignment}
              onRemove={removeServerAssignment}
            />
          )}
          {activeTab === "wks" && (
            <WksView
              wksEntries={wksEntries}
              projects={projects}
              wksToProjects={wksToProjects}
              saving={saving}
              onUpsert={upsertWksAssignment}
              onRemove={removeWksAssignment}
            />
          )}
          {activeTab === "projects" && (
            <ProjectsView
              projects={projects}
              servers={servers}
              wksEntries={wksEntries}
              targetsByProject={targetsByProject}
              saving={saving}
              conflicts={conflicts}
              onReload={loadAll}
              onUpsertServer={upsertServerAssignment}
              onRemoveServer={removeServerAssignment}
              onUpsertWks={upsertWksAssignment}
              onRemoveWks={removeWksAssignment}
            />
          )}
        </>
      )}
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string;
}) {
  return (
    <button onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
        active ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
      }`}>
      {icon}{label}
    </button>
  );
}

// ─────────────────────────────────────────── Tab: Servers

function ServersView(props: {
  servers: RemoteServer[];
  projects: ProjectSummary[];
  serverToProjects: Record<string, { projectId: string; role: string; note: string }[]>;
  saving: string | null;
  onReloadServers: () => void;
  onUpsert: (pid: string, sid: string, role: string, note: string) => Promise<void>;
  onRemove: (pid: string, sid: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { servers, projects, serverToProjects, saving, onReloadServers, onUpsert, onRemove } = props;
  const [editing, setEditing] = useState<RemoteServer | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [pubKey, setPubKey] = useState("");
  const [expandedServer, setExpandedServer] = useState<string | null>(null);

  async function addServer(srv: ServerEditPayload) {
    try {
      const d = await api.post<{ public_key: string }>("/admin/servers", srv);
      setPubKey(d.public_key || "");
      setShowAdd(false);
      onReloadServers();
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    }
  }

  async function updateServer(id: string, srv: ServerEditPayload) {
    try {
      await api.put(`/admin/servers/${id}`, srv);
      setEditing(null);
      onReloadServers();
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    }
  }

  async function deleteServer(id: string) {
    if (!confirm(t("common.confirmDelete", { defaultValue: "Wirklich löschen?" }))) return;
    try {
      await api.delete(`/admin/servers/${id}`);
      onReloadServers();
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }));
    }
  }

  async function testServer(id: string) {
    setTestResult(prev => ({ ...prev, [id]: { ok: false, msg: t("targetSystems.servers.testing", { defaultValue: "Teste…" }) } }));
    try {
      const d = await api.get<{ ok: boolean; error?: string; output?: string }>(`/admin/servers/${id}/test`);
      setTestResult(prev => ({ ...prev, [id]: {
        ok: d.ok,
        msg: d.ok
          ? t("targetSystems.servers.testOk", { defaultValue: "Verbunden!" })
          : (d.error || t("common.error", { defaultValue: "Fehler" })),
      } }));
    } catch (e) {
      setTestResult(prev => ({ ...prev, [id]: {
        ok: false, msg: e instanceof Error ? e.message : t("common.error", { defaultValue: "Fehler" }),
      } }));
    }
  }

  async function showPubKey(id: string) {
    try {
      const d = await api.get<{ public_key: string }>(`/admin/servers/${id}/pubkey`);
      setPubKey(d.public_key);
    } catch {
      setPubKey(t("targetSystems.servers.noKey", { defaultValue: "Kein Key vorhanden" }));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {t("targetSystems.servers.listDesc", { defaultValue: "Root-/Remote-Server als SSH-Ziele registrieren und Projekten zuweisen." })}
        </p>
        <button onClick={() => setShowAdd(true)}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">
          <Plus size={14} /> {t("common.new", { defaultValue: "Neu" })}
        </button>
      </div>

      {pubKey && (
        <div className="rounded-xl border bg-muted/30 p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold">
              {t("targetSystems.servers.pubKeyHint", { defaultValue: "Public Key — auf dem Ziel-Server in ~/.ssh/authorized_keys eintragen:" })}
            </p>
            <button onClick={() => navigator.clipboard.writeText(pubKey)}
              className="flex items-center gap-1 text-xs text-primary hover:underline">
              <Copy size={12} />{t("common.copy", { defaultValue: "Kopieren" })}
            </button>
          </div>
          <pre className="text-xs bg-black/30 rounded-lg p-3 font-mono break-all select-all">{pubKey}</pre>
          <button onClick={() => setPubKey("")} className="text-xs text-muted-foreground hover:text-foreground">
            {t("common.close", { defaultValue: "Schließen" })}
          </button>
        </div>
      )}

      {servers.length === 0 ? (
        <div className="rounded-xl border border-dashed bg-muted/20 p-8 text-center text-sm text-muted-foreground">
          {t("targetSystems.servers.empty", { defaultValue: "Noch keine Server registriert. Klicke auf Neu um einen SSH-Zielserver hinzuzufügen." })}
        </div>
      ) : (
        <div className="space-y-3">
          {servers.map(srv => {
            const assigned = serverToProjects[srv.id] || [];
            const isExpanded = expandedServer === srv.id;
            return (
              <div key={srv.id} className="rounded-xl border bg-card">
                <div className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm">
                      {srv.name}{" "}
                      <span className="text-muted-foreground font-mono text-xs">
                        ({srv.ssh_user}@{srv.ip}:{srv.ssh_port})
                      </span>
                    </p>
                    {srv.description && (
                      <p className="text-xs text-muted-foreground mt-0.5">{srv.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      srv.has_ssh_key ? "bg-green-500/10 text-green-500" : "bg-amber-500/10 text-amber-500"
                    }`}>
                      {srv.has_ssh_key
                        ? t("targetSystems.servers.hasKey", { defaultValue: "Key vorhanden" })
                        : t("targetSystems.servers.noKey",  { defaultValue: "Kein Key" })}
                    </span>
                    <button onClick={() => showPubKey(srv.id)} className="text-xs text-primary hover:underline">
                      {t("targetSystems.servers.keyBtn", { defaultValue: "Key" })}
                    </button>
                    <button onClick={() => testServer(srv.id)} className="text-xs text-primary hover:underline">
                      {t("targetSystems.servers.testBtn", { defaultValue: "Test" })}
                    </button>
                    <button onClick={() => setEditing(srv)} className="text-muted-foreground hover:text-foreground">
                      <Pencil size={14} />
                    </button>
                    <button onClick={() => deleteServer(srv.id)} className="text-muted-foreground hover:text-destructive">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {testResult[srv.id] && (
                  <p className={`px-4 pb-2 text-xs ${testResult[srv.id].ok ? "text-green-500" : "text-red-500"}`}>
                    {testResult[srv.id].msg}
                  </p>
                )}

                <div className="border-t px-4 py-3 bg-muted/20">
                  <button onClick={() => setExpandedServer(isExpanded ? null : srv.id)}
                    className="text-xs font-semibold text-muted-foreground uppercase tracking-wide hover:text-foreground">
                    {t("targetSystems.servers.assignmentsHeader", { defaultValue: "Projekt-Zuweisungen" })}
                    <span className="ml-1 text-primary">({assigned.length})</span>
                  </button>
                  {isExpanded && (
                    <AssignmentsList
                      kind="server"
                      assigned={assigned}
                      projects={projects}
                      saving={saving}
                      onUpsert={(pid, role, note) => onUpsert(pid, srv.id, role, note)}
                      onRemove={(pid) => onRemove(pid, srv.id)}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {(showAdd || editing) && (
        <ServerEditModal
          server={editing}
          onSave={(srv) => editing ? updateServer(editing.id, srv) : addServer(srv)}
          onClose={() => { setShowAdd(false); setEditing(null); }}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────── Tab: WKS

function WksView(props: {
  wksEntries: AdminWksEntry[];
  projects: ProjectSummary[];
  wksToProjects: Record<string, { projectId: string; role: string; note: string }[]>;
  saving: string | null;
  onUpsert: (pid: string, username: string, role: string, note: string) => Promise<void>;
  onRemove: (pid: string, username: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { wksEntries, projects, wksToProjects, saving, onUpsert, onRemove } = props;
  const [expandedWks, setExpandedWks] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-dashed bg-muted/20 px-4 py-2">
        <p className="text-xs text-muted-foreground">
          {t("targetSystems.wks.configHint", {
            defaultValue: "WKS wird unter Mein Agent → WKS konfiguriert. Hier werden WKS nur Projekten zugewiesen.",
          })}
        </p>
      </div>

      {wksEntries.length === 0 ? (
        <div className="rounded-xl border border-dashed bg-muted/20 p-8 text-center text-sm text-muted-foreground">
          {t("targetSystems.wks.empty", { defaultValue: "Keine User vorhanden." })}
        </div>
      ) : (
        <div className="space-y-3">
          {wksEntries.map(wks => {
            const assigned = wksToProjects[wks.username] || [];
            const isExpanded = expandedWks === wks.username;
            const disabled = !wks.configured;
            return (
              <div key={wks.username} className={`rounded-xl border bg-card ${disabled ? "opacity-60" : ""}`}>
                <div className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm">
                      {wks.username}
                      {wks.configured && (
                        <span className="text-muted-foreground font-mono text-xs ml-1">
                          ({wks.ssh_user}@{wks.ip}:{wks.ssh_port})
                        </span>
                      )}
                    </p>
                    {!wks.configured && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {t("targetSystems.wks.notConfigured", { defaultValue: "WKS nicht eingerichtet" })}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      wks.configured ? "bg-green-500/10 text-green-500" : "bg-muted text-muted-foreground"
                    }`}>
                      {wks.configured
                        ? t("targetSystems.wks.configuredBadge", { defaultValue: "Konfiguriert" })
                        : t("targetSystems.wks.notConfiguredBadge", { defaultValue: "Nicht konfiguriert" })}
                    </span>
                    {wks.has_ssh_key && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-500">
                        {t("targetSystems.servers.hasKey", { defaultValue: "Key vorhanden" })}
                      </span>
                    )}
                  </div>
                </div>

                {wks.configured && (
                  <div className="border-t px-4 py-3 bg-muted/20">
                    <button onClick={() => setExpandedWks(isExpanded ? null : wks.username)}
                      className="text-xs font-semibold text-muted-foreground uppercase tracking-wide hover:text-foreground">
                      {t("targetSystems.servers.assignmentsHeader", { defaultValue: "Projekt-Zuweisungen" })}
                      <span className="ml-1 text-primary">({assigned.length})</span>
                    </button>
                    {isExpanded && (
                      <AssignmentsList
                        kind="wks"
                        assigned={assigned}
                        projects={projects}
                        saving={saving}
                        onUpsert={(pid, role, note) => onUpsert(pid, wks.username, role, note)}
                        onRemove={(pid) => onRemove(pid, wks.username)}
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────── AssignmentsList (shared by Servers/WKS tabs)

function AssignmentsList(props: {
  kind: "server" | "wks";
  assigned: { projectId: string; role: string; note: string }[];
  projects: ProjectSummary[];
  saving: string | null;
  onUpsert: (projectId: string, role: string, note: string) => Promise<void>;
  onRemove: (projectId: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { assigned, projects, saving, onUpsert, onRemove } = props;
  const [addProjectId, setAddProjectId] = useState("");
  const [addRole, setAddRole] = useState("");
  const [addNote, setAddNote] = useState("");

  const assignedProjectIds = new Set(assigned.map(a => a.projectId));
  const availableProjects = projects.filter(p => !assignedProjectIds.has(p.id));

  async function handleAdd() {
    if (!addProjectId) return;
    await onUpsert(addProjectId, addRole.trim(), addNote.trim());
    setAddProjectId(""); setAddRole(""); setAddNote("");
  }

  return (
    <div className="mt-3 space-y-2">
      {assigned.length === 0 && (
        <p className="text-xs text-muted-foreground italic">
          {t("targetSystems.assign.none", { defaultValue: "Keinem Projekt zugewiesen." })}
        </p>
      )}
      {assigned.map(a => {
        const proj = projects.find(p => p.id === a.projectId);
        return (
          <div key={a.projectId} className="flex items-center gap-2 text-xs bg-background rounded-md px-3 py-2 border">
            <span className="font-medium min-w-[8rem]">{proj?.name || a.projectId}</span>
            <EditableAssignmentFields
              role={a.role}
              note={a.note}
              disabled={saving === a.projectId}
              onSave={(role, note) => onUpsert(a.projectId, role, note)}
            />
            <button onClick={() => onRemove(a.projectId)}
              disabled={saving === a.projectId}
              className="ml-auto text-muted-foreground hover:text-destructive disabled:opacity-50">
              <Trash2 size={12} />
            </button>
          </div>
        );
      })}

      {availableProjects.length > 0 && (
        <div className="flex items-center gap-2 mt-2 pt-2 border-t">
          <select value={addProjectId} onChange={e => setAddProjectId(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-xs">
            <option value="">{t("targetSystems.assign.pickProject", { defaultValue: "Projekt wählen…" })}</option>
            {availableProjects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <input value={addRole} onChange={e => setAddRole(e.target.value)}
            placeholder={t("targetSystems.assign.rolePlaceholder", { defaultValue: "role (z.B. web)" })}
            className="rounded-md border bg-background px-2 py-1 text-xs w-32" />
          <input value={addNote} onChange={e => setAddNote(e.target.value)}
            placeholder={t("targetSystems.assign.notePlaceholder", { defaultValue: "note (optional)" })}
            className="rounded-md border bg-background px-2 py-1 text-xs flex-1" />
          <button onClick={handleAdd} disabled={!addProjectId || saving !== null}
            className="flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            <Plus size={12} />{t("targetSystems.assign.add", { defaultValue: "Zuweisen" })}
          </button>
        </div>
      )}
    </div>
  );
}

function EditableAssignmentFields(props: {
  role: string;
  note: string;
  disabled: boolean;
  onSave: (role: string, note: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [role, setRole] = useState(props.role);
  const [note, setNote] = useState(props.note);
  const dirty = role !== props.role || note !== props.note;

  return (
    <>
      <input value={role} onChange={e => setRole(e.target.value)}
        placeholder={t("targetSystems.assign.role", { defaultValue: "role" })}
        disabled={props.disabled}
        className="rounded-md border bg-background px-2 py-1 text-xs w-28" />
      <input value={note} onChange={e => setNote(e.target.value)}
        placeholder={t("targetSystems.assign.note", { defaultValue: "note" })}
        disabled={props.disabled}
        className="rounded-md border bg-background px-2 py-1 text-xs flex-1 min-w-[8rem]" />
      {dirty && (
        <button onClick={() => props.onSave(role.trim(), note.trim())}
          disabled={props.disabled}
          className="flex items-center gap-1 text-primary hover:underline disabled:opacity-50">
          <Check size={12} />{t("common.save", { defaultValue: "Speichern" })}
        </button>
      )}
    </>
  );
}

// ─────────────────────────────────────────── Tab: Projects

function ProjectsView(props: {
  projects: ProjectSummary[];
  servers: RemoteServer[];
  wksEntries: AdminWksEntry[];
  targetsByProject: Record<string, ProjectTargetsResponse>;
  saving: string | null;
  conflicts: Record<string, TargetsConflict>;
  onReload: () => Promise<void>;
  onUpsertServer: (pid: string, sid: string, role: string, note: string) => Promise<void>;
  onRemoveServer: (pid: string, sid: string) => Promise<void>;
  onUpsertWks:    (pid: string, username: string, role: string, note: string) => Promise<void>;
  onRemoveWks:    (pid: string, username: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { projects, servers, wksEntries, targetsByProject, saving,
          conflicts, onReload,
          onUpsertServer, onRemoveServer, onUpsertWks, onRemoveWks } = props;

  if (projects.length === 0) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/20 p-8 text-center text-sm text-muted-foreground">
        {t("targetSystems.projects.empty", { defaultValue: "Keine Projekte vorhanden." })}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {projects.map(p => {
        const tg = targetsByProject[p.id];
        const conflict = conflicts[p.id];
        return (
          <div key={p.id} className="rounded-xl border bg-card p-4 space-y-3">
            <div>
              <p className="font-medium text-sm">
                {p.name} <span className="text-muted-foreground font-mono text-xs">({p.id})</span>
              </p>
              {p.description && (
                <p className="text-xs text-muted-foreground mt-0.5">{p.description}</p>
              )}
            </div>

            {/* #676: Inline-Konflikt-Banner wenn der letzte PUT ein 428/409 war */}
            {conflict && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 flex items-start gap-3">
                <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                    {t("targetSystems.conflictTitle")}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {conflict.message || t("targetSystems.conflictDesc")}
                  </p>
                </div>
                <button
                  onClick={onReload}
                  className="text-xs px-3 py-1.5 rounded-md bg-amber-600 text-white hover:bg-amber-700 transition-colors shrink-0"
                >
                  {t("targetSystems.reloadBtn")}
                </button>
              </div>
            )}

            {/* Server-Sektion */}
            <ProjectTargetSubsection
              title={t("targetSystems.projects.serversHeader", { defaultValue: "Server" })}
              assigned={(tg?.servers || []).map(s => ({
                key: s.server_id,
                label: s.name || s.server_id,
                sub: `${s.ssh_user}@${s.ip}:${s.ssh_port}`,
                role: s.role, note: s.note,
                stale: s.stale,
              }))}
              options={servers.map(s => ({ id: s.id, label: s.name }))}
              usedIds={new Set((tg?.servers || []).map(s => s.server_id))}
              saving={saving === p.id}
              onUpsert={(id, role, note) => onUpsertServer(p.id, id, role, note)}
              onRemove={(id) => onRemoveServer(p.id, id)}
              emptyLabel={t("targetSystems.projects.noServers", { defaultValue: "Keine Server zugewiesen." })}
            />

            {/* WKS-Sektion */}
            <ProjectTargetSubsection
              title={t("targetSystems.projects.wksHeader", { defaultValue: "WKS" })}
              assigned={(tg?.wks || []).map(w => ({
                key: w.username,
                label: w.username,
                sub: `${w.ssh_user}@${w.ip}:${w.ssh_port}`,
                role: w.role, note: w.note,
              }))}
              options={wksEntries.filter(w => w.configured).map(w => ({ id: w.username, label: w.username }))}
              usedIds={new Set((tg?.wks || []).map(w => w.username))}
              saving={saving === p.id}
              onUpsert={(id, role, note) => onUpsertWks(p.id, id, role, note)}
              onRemove={(id) => onRemoveWks(p.id, id)}
              emptyLabel={t("targetSystems.projects.noWks", { defaultValue: "Keine WKS zugewiesen." })}
            />
          </div>
        );
      })}
    </div>
  );
}

function ProjectTargetSubsection(props: {
  title: string;
  assigned: { key: string; label: string; sub: string; role: string; note: string; stale?: boolean }[];
  options: { id: string; label: string }[];
  usedIds: Set<string>;
  saving: boolean;
  onUpsert: (id: string, role: string, note: string) => Promise<void>;
  onRemove: (id: string) => Promise<void>;
  emptyLabel: string;
}) {
  const { t } = useTranslation();
  const { title, assigned, options, usedIds, saving, onUpsert, onRemove, emptyLabel } = props;
  const [addId, setAddId] = useState("");
  const [addRole, setAddRole] = useState("");
  const [addNote, setAddNote] = useState("");

  const available = options.filter(o => !usedIds.has(o.id));

  async function handleAdd() {
    if (!addId) return;
    await onUpsert(addId, addRole.trim(), addNote.trim());
    setAddId(""); setAddRole(""); setAddNote("");
  }

  return (
    <div className="border-t pt-3 space-y-2">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{title}</p>

      {assigned.length === 0 && (
        <p className="text-xs text-muted-foreground italic">{emptyLabel}</p>
      )}
      {assigned.map(a => (
        <div key={a.key} className={`flex items-center gap-2 text-xs rounded-md px-3 py-2 border ${
          a.stale ? "border-amber-400/50 bg-amber-50/20" : "bg-background"
        }`}>
          <div className="min-w-[8rem]">
            <div className="font-medium">{a.label}</div>
            <div className="font-mono text-[10px] text-muted-foreground">{a.sub}</div>
            {a.stale && (
              <div className="text-[10px] text-amber-600 font-medium mt-0.5">
                {t("targetSystems.projects.staleHint", { defaultValue: "Eintrag hängt — Ziel existiert nicht mehr" })}
              </div>
            )}
          </div>
          <EditableAssignmentFields role={a.role} note={a.note} disabled={saving}
            onSave={(role, note) => onUpsert(a.key, role, note)} />
          <button onClick={() => onRemove(a.key)} disabled={saving}
            className="ml-auto text-muted-foreground hover:text-destructive disabled:opacity-50">
            <Trash2 size={12} />
          </button>
        </div>
      ))}

      {available.length > 0 && (
        <div className="flex items-center gap-2 mt-2">
          <select value={addId} onChange={e => setAddId(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-xs">
            <option value="">{t("targetSystems.assign.pick", { defaultValue: "auswählen…" })}</option>
            {available.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
          <input value={addRole} onChange={e => setAddRole(e.target.value)}
            placeholder={t("targetSystems.assign.rolePlaceholder", { defaultValue: "role (z.B. web)" })}
            className="rounded-md border bg-background px-2 py-1 text-xs w-32" />
          <input value={addNote} onChange={e => setAddNote(e.target.value)}
            placeholder={t("targetSystems.assign.notePlaceholder", { defaultValue: "note (optional)" })}
            className="rounded-md border bg-background px-2 py-1 text-xs flex-1" />
          <button onClick={handleAdd} disabled={!addId || saving}
            className="flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
            <Plus size={12} />{t("targetSystems.assign.add", { defaultValue: "Zuweisen" })}
          </button>
        </div>
      )}
    </div>
  );
}
