export interface GpuEntry {
  name:          string;
  temp_c:        number | null;
  util_gpu_pct:  number | null;
  util_mem_pct:  number | null;
  mem_total_mb:  number | null;
  mem_used_mb:   number | null;
  mem_free_mb:   number | null;
  power_draw_w:  number | null;
  power_limit_w: number | null;
}
export interface GpuInfo {
  available: boolean;
  reason?:   string;
  gpus?:     GpuEntry[];
}

const BASE = "/api";
function getToken() { return localStorage.getItem("octopos_token") || ""; }
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...(options.headers||{}) } });
  if (!res.ok) { const e = await res.json().catch(()=>({detail:res.statusText})); throw new Error(e.detail||`HTTP ${res.status}`); }
  return res.json();
}
export const api = {
  get:    <T>(path: string)                => request<T>(path),
  post:   <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put:    <T>(path: string, body: unknown) => request<T>(path, { method: "PUT",    body: JSON.stringify(body) }),
  delete: <T>(path: string)               => request<T>(path, { method: "DELETE" }),
  health:        ()           => api.get<{status:string}>("/health"),
  status:        ()           => api.get<Record<string,unknown>>("/status"),
  gpuInfo:       ()           => api.get<GpuInfo>("/system/gpu"),
  agents:        ()           => api.get<Record<string,unknown>>("/agents"),
  projects:      ()           => api.get<Record<string,unknown>>("/projects"),
  createProject:  (d: unknown) => api.post("/projects", d),
  deleteProject:  (id: string) => api.delete(`/projects/${id}`),
  createAgent:  (d: unknown) => api.post("/agents", d),
  updateAgent:  (id: string, d: unknown) => api.put(`/agents/${id}`, d),
  deleteAgent:  (id: string) => api.delete(`/agents/${id}`),
  getAgentSoul: (id: string) => api.get<{soul:string;exists:boolean}>(`/agents/${id}/soul`),
  tools:        ()           => api.get<Record<string,unknown>>("/tools"),
  sendMessage:   (id: string, content: string) =>
    api.post<{response:string;workers:string[];session_id:string}>(`/projects/${id}/message`, { content }),
  sessionHistory: (id: string) =>
    api.get<{session_id:string|null;messages:{role:string;content:string}[];count:number}>(`/projects/${id}/session/history`),
  agentLogs: (id: string, lines = 100) =>
    api.get<{agent_id:string;lines:string[];count:number}>(`/agents/${id}/logs?lines=${lines}`),
  projectWebhooks:  (id: string) =>
    api.get<{webhooks: Webhook[]}>(`/projects/${id}/webhooks`),
  createWebhook:    (id: string, d: unknown) => api.post<Webhook>(`/projects/${id}/webhooks`, d),
  deleteWebhook:    (id: string, wid: string) => api.delete(`/projects/${id}/webhooks/${wid}`),
  testWebhook:      (id: string, d: unknown) => api.post(`/projects/${id}/webhooks/test`, d),
  auditLogs: (params?: { limit?: number; project?: string; user?: string; action?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit)   q.set("limit",   String(params.limit));
    if (params?.project) q.set("project", params.project);
    if (params?.user)    q.set("user",    params.user);
    if (params?.action)  q.set("action",  params.action);
    const qs = q.toString();
    return api.get<{logs: AuditEntry[]; count: number}>(`/audit/logs${qs ? "?" + qs : ""}`);
  },
  agentSkills:       (id: string) =>
    api.get<{skills: AgentSkill[]}>(`/agents/${id}/skills`),
  createSkill:       (id: string, d: unknown) => api.post(`/agents/${id}/skills`, d),
  updateSkill:       (id: string, filename: string, d: unknown) => api.put(`/agents/${id}/skills/${filename}`, d),
  deleteSkill:       (id: string, filename: string) => api.delete(`/agents/${id}/skills/${filename}`),
  agentlinkHandoffs: (id: string) =>
    api.get<{handoffs: Handoff[]}>(`/projects/${id}/agentlink`),
  deleteHandoff:     (id: string, handoffId: string) =>
    api.delete(`/projects/${id}/agentlink/${handoffId}`),
  listBackups:   () => api.get<{backups: BackupEntry[]}>("/admin/backups"),
  createBackup:  () => api.post<BackupEntry>("/admin/backup", {}),
  deleteBackup:  (name: string) => api.delete(`/admin/backups/${name}`),
  restoreBackup: (name: string) => api.post(`/admin/restore/${name}`, {}),
  downloadBackupUrl: (name: string) => `/api/admin/backups/${encodeURIComponent(name)}/download`,
};

export interface AuditEntry {
  id:         string;
  timestamp:  string;
  user:       string;
  action:     string;
  target:     string;
  project_id: string | null;
  ip:         string;
  details:    Record<string, unknown>;
}

export interface Webhook {
  id:         string;
  name:       string;
  url:        string;
  events:     string[];
  created_at: string;
}

export interface Handoff {
  id:         string;
  created_at: string;
  expires_at: string;
  from_agent: string;
  to_agent:   string | null;
  context:    string;
  data:       Record<string, unknown>;
}

export interface BackupEntry {
  name:       string;
  size:       number;
  created_at: string;
}

export interface AgentSkill {
  filename: string;
  skill:    string;
  version:  string;
  scope:    string;
  triggers: string[];
  priority: number;
  content:  string;
}
