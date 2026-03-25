export interface HeartbeatTaskStatus {
  agent_id:  string;
  task_id:   string;
  schedule:  string | null;
  interval:  number | null;
  message:   string;
  project:   string | null;
  last_run:  string | null;
}

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
function getToken() { return localStorage.getItem("hydrahive_token") || ""; }

function notifyAuthExpired(path: string) {
  if (typeof window === "undefined") return;
  if (!getToken()) return;
  window.dispatchEvent(new CustomEvent("hydrahive-auth-expired", {
    detail: { path },
  }));
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...(options.headers||{}) } });
  if (!res.ok) {
    const e = await res.json().catch(()=>({detail:res.statusText}));
    if (res.status === 401) notifyAuthExpired(path);
    throw new Error(e.detail||`HTTP ${res.status}`);
  }
  return res.json();
}
export const api = {
  get:    <T>(path: string)                => request<T>(path),
  post:   <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put:    <T>(path: string, body: unknown) => request<T>(path, { method: "PUT",   body: JSON.stringify(body) }),
  patch:  <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string)               => request<T>(path, { method: "DELETE" }),
  health:        ()           => api.get<{status:string}>("/health"),
  status:        ()           => api.get<Record<string,unknown>>("/status"),
  gpuInfo:       ()           => api.get<GpuInfo>("/system/gpu"),
  heartbeatTasks: ()          => api.get<{tasks: HeartbeatTaskStatus[]}>("/system/heartbeat-tasks"),
  agents:        ()           => api.get<Record<string,unknown>>("/agents"),
  projects:      ()           => api.get<Record<string,unknown>>("/projects"),
  createProject:  (d: unknown) => api.post("/projects", d),
  updateProject:  (id: string, d: {name?:string;description?:string;boss?:string;workers?:string[];show_swarm?:boolean}) => api.put(`/projects/${id}`, d),
  deleteProject:  (id: string) => api.delete(`/projects/${id}`),
  createAgent:  (d: unknown) => api.post("/agents", d),
  updateAgent:  (id: string, d: unknown) => api.put(`/agents/${id}`, d),
  updateUser:   (username: string, d: {role?:string;allowed_projects?:string[];allowed_agents?:string[];datasources?:string[];wks_ip?:string}) => api.put(`/users/${username}`, d),
  deleteAgent:  (id: string) => api.delete(`/agents/${id}`),
  patchAgentHeartbeat: (id: string, d: {enabled: boolean; interval: string; timeout: string; on_failure: string}) =>
    api.patch<{ok: boolean}>(`/agents/${id}/heartbeat`, d),
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
  myAgent:       () => api.get<{agent_id:string;config:Record<string,unknown>}>("/me/agent"),
  updateMyAgent: (d: unknown) => api.put("/me/agent", d),
  myAgentHistory: (limit = 50) => api.get<{session_id:string|null;messages:{role:string;content:string}[];count:number}>(`/me/agent/session/history?limit=${limit}`),
  patchMyAgentHeartbeat: (d: {heartbeat?: Record<string,unknown>; heartbeat_tasks?: unknown[]}) =>
    api.patch<{updated:boolean}>("/me/agent/heartbeat", d),
  clearMyAgentSession: () => api.delete("/me/agent/session"),
  myPlatforms:    () => api.get<{username:string;platforms: PlatformOverviewEntry[]}>("/me/platforms"),
  mcpServers:    () => api.get<{servers: McpServer[]}>("/mcp/servers"),
  createMcpServer: (d: unknown) => api.post<{server: McpServer}>("/mcp/servers", d),
  updateMcpServer: (id: string, d: unknown) => api.put<{server: McpServer}>(`/mcp/servers/${id}`, d),
  deleteMcpServer: (id: string) => api.delete(`/mcp/servers/${id}`),
  listBackups:   () => api.get<{backups: BackupEntry[]}>("/admin/backups"),
  createBackup:  () => api.post<BackupEntry>("/admin/backup", {}),
  deleteBackup:  (name: string) => api.delete(`/admin/backups/${name}`),
  restoreBackup: (name: string) => api.post(`/admin/restore/${name}`, {}),
  downloadBackupUrl: (name: string) => `/api/admin/backups/${encodeURIComponent(name)}/download`,
  // Gitea
  giteaConfig:       () => api.get<GiteaConfig>("/gitea/config"),
  updateGiteaConfig: (d: GiteaConfig) => api.put("/gitea/config", d),
  giteaRepos:        () => api.get<{repos: GiteaRepo[]}>("/gitea/repos"),
  giteaProjectPRs:   (id: string) => api.get<{prs: unknown[]; count: number}>(`/gitea/repos/${id}/prs`),
  // LLM Token Status
  claudeTokenStatus:         () => api.get<{configured:boolean;token_age_days:number|null;remaining_days:number|null;warning:string|null;ttl_days:number}>("/llm/claude_token_status"),
  openaiCodexStatus:         () => api.get<{configured:boolean;account_id:string|null;models?:string[]}>("/llm/openai_codex_status"),
  getSystemDefaultModel:     () => api.get<{model:string}>("/llm/config/system_default"),
  setSystemDefaultModel:     (model: string) => api.put<{updated:boolean;model:string;agents_updated:string[]}>("/llm/config/system_default", { model }),
  availableModels:           () => api.get<{models:{id:string;label:string;provider:string}[]}>("/llm/available-models"),
  setOpenaiCodexToken:       (d: {access_token:string;account_id:string;refresh_token?:string}) => api.put("/llm/config/openai_codex", d),
  // OAuth PKCE Flow
  startOAuth:    (provider: string) => api.post<{auth_url:string;state:string}>(`/llm/oauth/${provider}/start`, {}),
  exchangeOAuth: (provider: string, body: {redirect_url?:string;code?:string;state?:string;code_and_state?:string}) =>
    api.post<{updated:boolean;[key:string]:unknown}>(`/llm/oauth/${provider}/exchange`, body),
  // VPN (Tailscale / Headscale)
  vpnStatus:            () => api.get<VpnStatus>("/admin/vpn/status"),
  vpnConnect:           (d: {auth_key?: string; login_server?: string; hostname?: string}) =>
    api.post<{connected: boolean; tailscale_ip: string | null; mode: string}>("/admin/vpn/connect", d),
  vpnDown:              () => api.post<{disconnected: boolean}>("/admin/vpn/down", {}),
  vpnPeers:             () => api.get<{peers: VpnPeer[]; count: number}>("/admin/vpn/peers"),
  vpnHeadscaleAuthkey:  () => api.post<{auth_key: string; expiration: string; reusable: boolean}>("/admin/vpn/headscale/authkey", {}),
  // KAS / All-Inkl
  getKas:  () => api.get<KasConfig>("/admin/kas"),
  putKas:  (d: KasConfigPayload) => api.put<{saved: boolean}>("/admin/kas", d),
  wizardComplete: () => api.post<{done: boolean}>("/admin/wizard/complete", {}),
  // Session History
  listSessions:  (projectId: string, limit = 20) =>
    api.get<{sessions: SessionPreview[]}>(`/projects/${projectId}/sessions?limit=${limit}`),
  getSessionById: (projectId: string, sessionId: string) =>
    api.get<SessionFull>(`/projects/${projectId}/sessions/${sessionId}`),
  // Doctor
  doctor:   () => api.get<DoctorReport>("/admin/doctor"),
  runTests: () => api.get<TestReport>("/admin/tests"),
  // System-Update
  updateStatus:  () => api.get<UpdateStatus>("/admin/update/status"),
  updateTrigger: () => api.post<{status: string; message: string}>("/admin/update/trigger", {}),
  // WKS (Workstation)
  getWks:             () => api.get<WksConfig>("/me/wks"),
  updateWks:          (d: WksConfigPayload) => api.put("/me/wks", d),
  getWksOllamaModels: () => api.get<{models: {id:string;label:string;provider:string}[];wks_url:string|null;error?:string}>("/me/wks/ollama-models"),
  getWksPubkey:       () => api.get<{public_key:string}>("/me/wks/pubkey"),
  generateWksKey:     () => api.post<{generated:boolean;public_key:string}>("/me/wks/generate-key", {}),
  testWksSsh:         () => api.post<{ok:boolean;hostname?:string;user?:string;error?:string}>("/me/wks/test-ssh", {}),
  getWhatsApp:           () => api.get<WhatsAppStatus>("/me/whatsapp"),
  connectWhatsApp:       () => api.post<WhatsAppStatus>("/me/whatsapp/connect", {}),
  disconnectWhatsApp:    () => api.delete<{disconnected:boolean}>("/me/whatsapp"),
  updateWhatsAppConfig:  (d: WhatsAppConfig) => api.put<{updated:boolean}>("/me/whatsapp/config", d),
  getTelegram:           () => api.get<TelegramStatus>("/me/telegram"),
  connectTelegram:       (d: {bot_token: string} & Partial<TelegramConfig>) => api.post<TelegramStatus>("/me/telegram/connect", d),
  disconnectTelegram:    () => api.delete<{disconnected:boolean}>("/me/telegram"),
  updateTelegramConfig:  (d: Partial<TelegramConfig>) => api.put<{updated:boolean}>("/me/telegram/config", d),
  getMail:            () => api.get<MailConfig>("/me/mail"),
  updateMail:         (d: MailConfigPayload) => api.put<{configured:boolean;mail_address:string;created:boolean}>("/me/mail", d),
  deleteMail:         () => api.delete("/me/mail"),
  getDiscord:         () => api.get<DiscordConfig>("/me/discord"),
  getDiscordChannels: () => api.get<{channels:{id:string;name:string}[]}>("/me/discord/channels"),
  updateDiscord:      (d: DiscordConfigPayload) => api.put<{updated:boolean;bot_name:string;bot_id:string}>("/me/discord", d),
  deleteDiscord:      () => api.delete<{deleted:boolean}>("/me/discord"),
  testDiscord:        () => api.post<{ok:boolean;bot_name?:string;bot_id?:string;error?:string}>("/me/discord/test", {}),
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
  author?:  string;  // "agent" = selbst angelegt, "system" oder leer = systemseitig
}

export interface McpServer {
  id:        string;
  name:      string;
  transport: string;
  url:       string;
  headers:   Record<string, string>;
  meta?:     Record<string, unknown>;
}

export interface GiteaConfig {
  url:            string;
  token:          string;
  org:            string;
  webhook_secret: string;
}

export interface GiteaRepo {
  name:           string;
  description:    string;
  html_url:       string;
  default_branch: string;
  updated:        string;
}

export interface WksConfig {
  configured:  boolean;
  ip:          string;
  ssh_user:    string;
  ollama_port: number;
  has_ssh_key: boolean;
}

export interface WksConfigPayload {
  ip:          string;
  ssh_user:    string;
  ollama_port: number;
  ssh_key?:    string;
}

export interface DiscordConfig {
  configured:           boolean;
  guild_id?:            string;
  channel_ids?:         string[];
  ignore_bots?:         boolean;
  require_mention?:     boolean;
  loop_detection?:      boolean;
  loop_bot_threshold?:  number;
  loop_pingpong_seconds?: number;
  loop_cooldown_seconds?: number;
  connected?:           boolean;
}

export interface DiscordConfigPayload {
  bot_token:            string;
  guild_id:             string;
  channel_ids:          string[];
  ignore_bots:          boolean;
  require_mention:      boolean;
  loop_detection:       boolean;
  loop_bot_threshold:   number;
  loop_pingpong_seconds: number;
  loop_cooldown_seconds: number;
}

export interface WhatsAppConfig {
  private_chats_enabled: boolean;
  group_chats_enabled:   boolean;
  require_keyword:       string;
  allowed_numbers:       string[];
  blocked_numbers:       string[];
  owner_numbers:         string[];
}

export interface WhatsAppStatus extends Partial<WhatsAppConfig> {
  configured: boolean;
  status:     "disconnected" | "connecting" | "waiting_qr" | "connected" | "reconnecting" | "bridge_unavailable" | "saved";
  qr:         string | null;
  phone:      string | null;
}

export interface TelegramConfig {
  allow_private:    boolean;
  allow_groups:     boolean;
  require_keyword:  string;
  allowed_user_ids: string[];
  blocked_user_ids: string[];
  admin_user_ids:   string[];
}

export interface TelegramStatus extends Partial<TelegramConfig> {
  configured:   boolean;
  enabled:      boolean;
  status:       "running" | "stopped" | "error";
  bot_username: string;
}

export interface MailConfig {
  configured:   boolean;
  mail_address: string;
  smtp_host:    string;
}

export interface MailConfigPayload {
  mail_address:   string;
  domain:         string;
  create_account: boolean;
  smtp_host?:     string;
  smtp_port?:     number;
  smtp_user?:     string;
  smtp_password?: string;
  imap_host?:     string;
}

export interface PlatformOverviewEntry {
  platform:   string;
  label:      string;
  supported:  boolean;
  configured: boolean;
  connected:  boolean;
  details:    Record<string, unknown>;
}

export interface VpnPeer {
  id:        string;
  hostname:  string;
  ip:        string;
  online:    boolean;
  os:        string;
  last_seen: string;
}

export interface VpnStatus {
  mode:              "tailscale" | "headscale" | "none";
  configured:        boolean;
  connected:         boolean;
  backend_state?:    string;
  tailscale_ip?:     string | null;
  login_server?:     string;
  hostname?:         string;
  peers?:            VpnPeer[];
  headscale_running?: boolean | null;
  error?:            string;
}

export interface TestReport {
  status:   "ok" | "error";
  passed:   number;
  failed:   number;
  total:    number;
  duration: number;
  output:   string;
}

export interface DoctorCheck {
  name:   string;
  status: "ok" | "warn" | "error";
  detail: string;
  hint?:  string;
}
export interface DoctorReport {
  status:  "ok" | "warn" | "error";
  summary: { total: number; ok: number; warn: number; error: number };
  checks:  DoctorCheck[];
}

export interface KasConfig {
  configured:     boolean;
  login?:         string;
  password?:      string;
  default_domain?: string;
  smtp_host?:     string;
  smtp_port?:     number;
}

export interface KasConfigPayload {
  login:          string;
  password:       string;
  default_domain: string;
  smtp_host:      string;
  smtp_port:      number;
}

export interface SessionPreview {
  id:            string;
  started_at:    string;
  ended_at:      string | null;
  message_count: number;
  preview:       string;
}

export interface SessionMessage {
  role:      "user" | "assistant" | "system" | "tool";
  content:   string;
  timestamp: string;
  agent_id:  string | null;
}

export interface SessionFull {
  id:         string;
  project_id: string;
  started_at: string;
  ended_at:   string | null;
  messages:   SessionMessage[];
}

export interface UpdateStatus {
  status:      string;   // "ok" | "running" | "error" | "unknown"
  started_at?: string;
  finished_at?: string;
  commit?:     string;
  commit_full?: string;
  message?:    string;
  log_tail?:   string[];
  error?:      string;
}
