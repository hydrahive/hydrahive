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

// #645 Composer
export type ComposerWarningSeverity = "info" | "warning" | "error";
export interface ComposerWarning {
  rule:      string;
  severity:  ComposerWarningSeverity;
  message:   string;
  block_ids: string[];
}
// #647: Composer-Backup Listing/Preview/Restore
export type ComposerBackupKind = "versioned" | "latest";
export interface ComposerBackup {
  name:       string;
  kind:       ComposerBackupKind;
  size_bytes: number;
  mtime:      string;   // ISO-UTC, frontend via new Date().toLocaleString()
}
export interface ComposerBackupPreview {
  name:       string;
  content:    string;
  size_bytes: number;
  mtime:      string;
}
export interface ComposerRestoreResult {
  restored:             true;
  agent_id:             string;
  from_backup:          string;
  pre_restore_snapshot: string | null;
  etag:                 string;
}

const BASE = "/api";
function getToken() { return localStorage.getItem("hydrahive_token") || ""; }
function apiPath(path: string) {
  return path.startsWith(`${BASE}/`) ? path.slice(BASE.length) : path;
}

function notifyAuthExpired(path: string) {
  if (typeof window === "undefined") return;
  if (!getToken()) return;
  window.dispatchEvent(new CustomEvent("hydrahive-auth-expired", {
    detail: { path },
  }));
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  // #764 (Phase 2 von #748): Cookie-based Auth via credentials:'include'.
  // Backend-Middleware (#763) erlaubt Cookie ODER Bearer — wir wählen Cookie
  // für Browser-Clients. getToken() nur noch für den Race-Check bei 401.
  const tokenAtRequest = getToken();
  const res = await fetch(`${BASE}${apiPath(path)}`, { ...options, credentials: "include", headers: { "Content-Type": "application/json", ...(options.headers||{}) } });
  if (!res.ok) {
    const e = await res.json().catch(()=>({detail:res.statusText}));
    // Only trigger logout if the token hasn't changed since this request was sent
    if (res.status === 401 && tokenAtRequest && tokenAtRequest === getToken()) notifyAuthExpired(path);
    // Stringify structured detail (z.B. 409 Composer-Conflict) damit kein [object Object] entsteht,
    // aber originalen Status + Detail am Error-Objekt verfügbar machen.
    const detailText = typeof e.detail === "string" ? e.detail : (e.detail?.message || `HTTP ${res.status}`);
    const err = new Error(detailText) as Error & { status?: number; detail?: unknown };
    err.status = res.status;
    err.detail = e.detail;
    throw err;
  }
  return res.json();
}
export const api = {
  get:    <T>(path: string)                => request<T>(path),
  post:   <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put:    <T>(path: string, body: unknown) => request<T>(path, { method: "PUT",   body: JSON.stringify(body) }),
  putWithHeaders: <T>(path: string, body: unknown, headers: Record<string, string>) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body), headers }),
  postWithHeaders: <T>(path: string, body: unknown, headers: Record<string, string>) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body), headers }),
  patch:  <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string)               => request<T>(path, { method: "DELETE" }),
  health:        ()           => api.get<{status:string}>("/health"),
  status:        ()           => api.get<Record<string,unknown>>("/status"),
  gpuInfo:       ()           => api.get<GpuInfo>("/system/gpu"),
  sessionMetrics: ()          => api.get<Record<string,any>>("/admin/session-metrics"),
  oauthUsage:    ()           => api.get<Record<string,unknown>>("/admin/system/oauth-usage"),
  oauthUsageFetch: ()         => api.get<Record<string,unknown>>("/admin/system/oauth-usage/fetch"),
  minimaxUsage:  ()           => api.get<{
    available: boolean;
    reason?: string;
    fetched_at?: string;
    models?: Array<{
      name: string;
      label: string;
      interval_total: number;
      interval_used: number;
      interval_pct: number;
      interval_reset_in_s: number;
      weekly_total: number;
      weekly_used: number;
      weekly_pct: number;
    }>;
  }>("/admin/system/minimax-usage"),
  heartbeatTasks: ()          => api.get<{tasks: HeartbeatTaskStatus[]}>("/system/heartbeat-tasks"),
  agents:        ()           => api.get<Record<string,unknown>>("/agents"),
  projects:      ()           => api.get<Record<string,unknown>>("/projects"),
  createProject:  (d: unknown) => api.post("/projects", d),
  githubTokenStatus: () => api.get<Record<string,unknown>>("/github/token/status"),
  saveGithubToken: (token: string) => api.post<Record<string,unknown>>("/github/token", { token }),
  deleteGithubToken: () => api.delete("/github/token"),
  listGithubRepos: () => api.get<{full_name:string;html_url:string;description:string;private:boolean;language:string|null;pushed_at:string|null}[]>("/github/repos"),
  updateProject:  (id: string, d: {name?:string;description?:string}) => api.put(`/projects/${id}`, d),
  deleteProject:  (id: string) => api.delete(`/projects/${id}`),
  updateUser:   (username: string, d: {role?:string;allowed_projects?:string[];datasources?:string[];wks_ip?:string}) => api.put(`/users/${username}`, d),
  tools:        ()           => api.get<Record<string,unknown>>("/tools"),
  sendMessage:   (id: string, content: string) =>
    api.post<{response:string;workers:string[];session_id:string}>(`/projects/${id}/message`, { content }),
  sessionHistory: (id: string) =>
    api.get<{session_id:string|null;messages:{role:string;content:string}[];count:number}>(`/projects/${id}/session/history`),
  agentLogs: (id: string, lines = 100) =>
    api.get<{agent_id:string;lines:string[];count:number}>(`/agents/${id}/logs?lines=${lines}`),
  agentDebug: (agentId: string) =>
    api.get<{agent_id:string;output:string}>(`/admin/agents/${agentId}/debug`),
  agentHealthCheck: (agentId: string) =>
    api.post<{agent_id:string;healthy:boolean;latency_ms:number;error?:string}>(`/admin/agents/${agentId}/health-check`, {}),
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
  // #645 Profile-Composer (Personal-Agent)
  composerBlocks: () => api.get<{categories: {id:string; label:string; blocks:{id:string; label:string; description:string}[]}[]}>("/me/agent/composer/blocks"),
  composerPresets: () => api.get<{presets: {id:string; label:string; description:string; selected:string[]}[]}>("/me/agent/composer/presets"),
  composerProfile: () => api.get<{schema_version:number; preset:string|null; selected:string[]; updated_at:string|null; agent_md_exists:boolean; agent_md_mtime_matches:boolean; etag:string; warnings:ComposerWarning[]}>("/me/agent/composer/profile"),
  composerPreview: (selected: string[], preset?: string|null) => api.post<{markdown: string; warnings: ComposerWarning[]; save_blocked: boolean}>("/me/agent/composer/preview", {selected, preset: preset ?? null}),
  // #650: Composer-Save ist strict — If-Match ist Pflicht. Backend liefert
  // 428 Precondition Required wenn der Header fehlt, 409 Conflict bei
  // Mismatch. Signatur hart: etag muss vor dem Call geholt/validiert sein.
  composerSave: (selected: string[], preset: string|null, etag: string) => api.putWithHeaders<{updated:boolean; agent_id:string; backup_created:boolean; versioned_backup?: string|null; bytes_written:number; preset:string|null; etag:string; warnings:ComposerWarning[]}>("/me/agent/composer", {selected, preset}, {"If-Match": etag}),
  // #645 Phase 1d — Admin-Agent-Composer
  adminComposerBlocks: (agentId: string) => api.get<{categories: {id:string; label:string; blocks:{id:string; label:string; description:string}[]}[]}>(`/admin/agents/${encodeURIComponent(agentId)}/composer/blocks`),
  adminComposerPresets: (agentId: string) => api.get<{presets: {id:string; label:string; description:string; selected:string[]}[]}>(`/admin/agents/${encodeURIComponent(agentId)}/composer/presets`),
  adminComposerProfile: (agentId: string) => api.get<{schema_version:number; preset:string|null; selected:string[]; updated_at:string|null; agent_md_exists:boolean; agent_md_mtime_matches:boolean; etag:string; warnings:ComposerWarning[]}>(`/admin/agents/${encodeURIComponent(agentId)}/composer/profile`),
  adminComposerPreview: (agentId: string, selected: string[], preset?: string|null) => api.post<{markdown: string; warnings: ComposerWarning[]; save_blocked: boolean}>(`/admin/agents/${encodeURIComponent(agentId)}/composer/preview`, {selected, preset: preset ?? null}),
  // #650: siehe composerSave — If-Match Pflicht.
  adminComposerSave: (agentId: string, selected: string[], preset: string|null, etag: string) => api.putWithHeaders<{updated:boolean; agent_id:string; backup_created:boolean; versioned_backup?: string|null; bytes_written:number; preset:string|null; etag:string; warnings:ComposerWarning[]}>(`/admin/agents/${encodeURIComponent(agentId)}/composer`, {selected, preset}, {"If-Match": etag}),
  // #645 Phase 1e — Projekt-Boss-Composer
  projectComposerBlocks: (projectId: string) => api.get<{categories: {id:string; label:string; blocks:{id:string; label:string; description:string}[]}[]}>(`/projects/${encodeURIComponent(projectId)}/composer/blocks`),
  projectComposerPresets: (projectId: string) => api.get<{presets: {id:string; label:string; description:string; selected:string[]}[]}>(`/projects/${encodeURIComponent(projectId)}/composer/presets`),
  projectComposerProfile: (projectId: string) => api.get<{schema_version:number; preset:string|null; selected:string[]; updated_at:string|null; agent_md_exists:boolean; agent_md_mtime_matches:boolean; etag:string; warnings:ComposerWarning[]}>(`/projects/${encodeURIComponent(projectId)}/composer/profile`),
  projectComposerPreview: (projectId: string, selected: string[], preset?: string|null) => api.post<{markdown: string; warnings: ComposerWarning[]; save_blocked: boolean}>(`/projects/${encodeURIComponent(projectId)}/composer/preview`, {selected, preset: preset ?? null}),
  // #650: siehe composerSave — If-Match Pflicht.
  projectComposerSave: (projectId: string, selected: string[], preset: string|null, etag: string) => api.putWithHeaders<{updated:boolean; agent_id:string; backup_created:boolean; versioned_backup?: string|null; bytes_written:number; preset:string|null; etag:string; warnings:ComposerWarning[]}>(`/projects/${encodeURIComponent(projectId)}/composer`, {selected, preset}, {"If-Match": etag}),
  // #647 Composer-Backups (list / preview / restore) — pro Scope. Restore
  // ist strict If-Match: Backend antwortet 428 wenn Header fehlt, 409 bei
  // Mismatch. UI sendet immer den aktuellen ETag.
  composerBackups: () => api.get<{backups: ComposerBackup[]; count:number; truncated:boolean}>("/me/agent/composer/backups"),
  composerBackupPreview: (name: string) => api.get<ComposerBackupPreview>(`/me/agent/composer/backups/${encodeURIComponent(name)}`),
  composerBackupRestore: (name: string, etag: string) => api.postWithHeaders<ComposerRestoreResult>(`/me/agent/composer/backups/${encodeURIComponent(name)}/restore`, {}, {"If-Match": etag}),
  adminComposerBackups: (agentId: string) => api.get<{backups: ComposerBackup[]; count:number; truncated:boolean}>(`/admin/agents/${encodeURIComponent(agentId)}/composer/backups`),
  adminComposerBackupPreview: (agentId: string, name: string) => api.get<ComposerBackupPreview>(`/admin/agents/${encodeURIComponent(agentId)}/composer/backups/${encodeURIComponent(name)}`),
  adminComposerBackupRestore: (agentId: string, name: string, etag: string) => api.postWithHeaders<ComposerRestoreResult>(`/admin/agents/${encodeURIComponent(agentId)}/composer/backups/${encodeURIComponent(name)}/restore`, {}, {"If-Match": etag}),
  projectComposerBackups: (projectId: string) => api.get<{backups: ComposerBackup[]; count:number; truncated:boolean}>(`/projects/${encodeURIComponent(projectId)}/composer/backups`),
  projectComposerBackupPreview: (projectId: string, name: string) => api.get<ComposerBackupPreview>(`/projects/${encodeURIComponent(projectId)}/composer/backups/${encodeURIComponent(name)}`),
  projectComposerBackupRestore: (projectId: string, name: string, etag: string) => api.postWithHeaders<ComposerRestoreResult>(`/projects/${encodeURIComponent(projectId)}/composer/backups/${encodeURIComponent(name)}/restore`, {}, {"If-Match": etag}),
  myPlatforms:    () => api.get<{username:string;platforms: PlatformOverviewEntry[]}>("/me/platforms"),
  mcpServers:    () => api.get<{servers: McpServer[]}>("/mcp/servers"),
  createMcpServer: (d: unknown) => api.post<{server: McpServer}>("/mcp/servers", d),
  updateMcpServer: (id: string, d: unknown) => api.put<{server: McpServer}>(`/mcp/servers/${id}`, d),
  deleteMcpServer: (id: string) => api.delete(`/mcp/servers/${id}`),
  usageStats:    () => api.get<UsageStats>("/admin/usage"),
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
  vpnConnect:           (d: {auth_key?: string; login_server?: string; hostname?: string; mode?: string}) =>
    api.post<{connected: boolean; tailscale_ip: string | null; mode: string}>("/admin/vpn/connect", d),
  vpnDown:              () => api.post<{disconnected: boolean}>("/admin/vpn/down", {}),
  vpnPeers:             () => api.get<{peers: VpnPeer[]; count: number}>("/admin/vpn/peers"),
  vpnHeadscaleAuthkey:  () => api.post<{auth_key: string; expiration: string; reusable: boolean}>("/admin/vpn/headscale/authkey", {}),
  // KAS / All-Inkl
  getKas:  () => api.get<KasConfig>("/admin/kas"),
  putKas:  (d: KasConfigPayload) => api.put<{saved: boolean}>("/admin/kas", d),
  wizardComplete: () => api.post<{done: boolean}>("/admin/wizard/complete", {}),
  // Session History
  listSessions:  (agentId: string, limit = 20) =>
    api.get<{sessions: SessionPreview[]}>(`/agents/${agentId}/sessions?limit=${limit}`),
  listProjectSessions: (projectId: string, limit = 20) =>
    api.get<{sessions: SessionPreview[]}>(`/projects/${projectId}/sessions?limit=${limit}`),
  resumeProjectSession: (projectId: string, sessionId: string) =>
    api.post<{ resumed: boolean; id: string; messages: SessionFull["messages"] }>(`/projects/${projectId}/sessions/${sessionId}/resume`, {}),
  getSessionById: (agentId: string, sessionId: string) =>
    api.get<SessionFull>(`/agents/${agentId}/sessions/${sessionId}`),
  searchAgentSessions: (agentId: string, q: string) =>
    api.get<{query: string; results: {session_id: string; started_at: string; match_count: number; matches: {role: string; content: string; timestamp: string}[]}[]; total_matches: number}>(`/agents/${agentId}/sessions/search?q=${encodeURIComponent(q)}`),
  resumeSession: (agentId: string, sessionId: string) =>
    api.post<{ resumed: boolean; id: string; messages: SessionFull["messages"] }>(`/agents/${agentId}/sessions/${sessionId}/resume`, {}),
  // #641: CONFIRM-Round-Trip — pendings Tool-Call genehmigen/ablehnen
  confirmToolCall: (projectId: string, sessionId: string, toolCallId: string, decision: "approve" | "deny") =>
    api.post<{ resolved: boolean; decision: string; tool_call_id: string }>(
      `/projects/${projectId}/sessions/${sessionId}/tool-confirm`,
      { tool_call_id: toolCallId, decision },
    ),
  // #641-Followup: derselbe Roundtrip für Agent-Sessions
  confirmToolCallAgent: (agentId: string, sessionId: string, toolCallId: string, decision: "approve" | "deny") =>
    api.post<{ resolved: boolean; decision: string; tool_call_id: string }>(
      `/agents/${agentId}/sessions/${sessionId}/tool-confirm`,
      { tool_call_id: toolCallId, decision },
    ),
  // Doctor
  doctor:    () => api.get<DoctorReport>("/admin/doctor"),
  doctorFix: (fixId: string) => api.post<{ok:boolean;output?:string;error?:string}>(`/admin/doctor/fix/${fixId}`, {}),
  runTests:  () => api.get<TestReport>("/admin/tests"),
  // Live-Agent-Übersicht
  agentsLive: () => api.get<AgentsLiveReport>("/admin/agents/live"),
  stopAgent:  (id: string) => api.post<{stopped: string}>(`/admin/agents/${id}/stop`, {}),
  // SearXNG Web-Suche
  searxngStatus: () => api.get<SearxngStatus>("/admin/searxng/status"),
  searxngTest:   (body: { query: string; engines?: string }) => api.post<SearxngTestResult>("/admin/searxng/test", body),
  knowledgeStatus: () => api.get<{ available: boolean; total_notes: number }>("/admin/knowledge/status"),
  knowledgeSearch: (body: { query: string; mode?: string; limit?: number }) =>
    api.post<{ results: KnowledgeResult[]; total: number; mode: string; query: string; error?: string }>("/admin/knowledge/search", body),
  // Schedules
  schedules:        () => api.get<{ schedules: Schedule[] }>("/schedules"),
  createSchedule:   (d: SchedulePayload) => api.post<Schedule>("/schedules", d),
  updateSchedule:   (id: string, d: Partial<SchedulePayload>) => api.patch<Schedule>(`/schedules/${id}`, d),
  deleteSchedule:   (id: string) => api.delete<void>(`/schedules/${id}`),
  runScheduleNow:   (id: string) => api.post<{ triggered: boolean }>(`/schedules/${id}/run`, {}),
  // Notifications
  notifications:    () => api.get<{ notifications: AppNotification[] }>("/notifications"),
  unreadCount:      () => api.get<{ count: number }>("/notifications/unread-count"),
  markRead:         (id: string) => api.patch<{ ok: boolean }>(`/notifications/${id}/read`, {}),
  markAllRead:      () => api.post<{ marked: number }>("/notifications/read-all", {}),
  deleteNotif:      (id: string) => api.delete<{ ok: boolean }>(`/notifications/${id}`),
  // System-Update
  updateStatus:  () => api.get<UpdateStatus>("/admin/update/status"),
  updateTrigger: () => api.post<{status: string; message: string}>("/admin/update/trigger", {}),
  coreRestart:   () => api.post<{status: string; message: string}>("/admin/core/restart", {}),
  // Disk-Cleanup (#81)
  cleanupStatus: () => api.get<CleanupStatus>("/admin/cleanup/status"),
  cleanupRun:    () => api.post<CleanupResult>("/admin/cleanup/run", {}),
  cleanupConfig: (cfg: Partial<CleanupConfig>) => api.put<{updated:boolean;config:CleanupConfig}>("/admin/cleanup/config", cfg),
  // WKS (Workstation)
  getWks:             () => api.get<WksConfig>("/me/wks"),
  updateWks:          (d: WksConfigPayload) => api.put("/me/wks", d),
  getWksOllamaModels: () => api.get<{models: {id:string;label:string;provider:string}[];wks_url:string|null;error?:string}>("/me/wks/ollama-models"),
  getWksPubkey:       () => api.get<{public_key:string}>("/me/wks/pubkey"),
  generateWksKey:     () => api.post<{generated:boolean;public_key:string}>("/me/wks/generate-key", {}),
  testWksSsh:         () => api.post<{ok:boolean;hostname?:string;user?:string;error?:string;ssh_port?:number}>("/me/wks/test-ssh", {}),
  getWhatsApp:           () => api.get<WhatsAppStatus>("/me/whatsapp"),
  connectWhatsApp:       () => api.post<WhatsAppStatus>("/me/whatsapp/connect", {}),
  disconnectWhatsApp:    () => api.delete<{disconnected:boolean}>("/me/whatsapp"),
  installWhatsAppChromium: () => api.post<{ok:boolean;output?:string;error?:string}>("/me/whatsapp/install-chromium", {}),
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
  getDiscordRoles:    () => api.get<{roles:{id:string;name:string;color:string}[]}>("/me/discord/roles"),
  updateDiscord:      (d: DiscordConfigPayload) => api.put<{updated:boolean;bot_name:string;bot_id:string}>("/me/discord", d),
  deleteDiscord:      () => api.delete<{deleted:boolean}>("/me/discord"),
  testDiscord:        () => api.post<{ok:boolean;bot_name?:string;bot_id?:string;error?:string}>("/me/discord/test", {}),
  sambaCreds:         (id: string) => api.get<{project_id:string;username:string;password:string}>(`/projects/${id}/samba-credentials`),
  sambaResetPassword: (id: string) => api.post<{project_id:string;username:string;password:string}>(`/projects/${id}/samba-reset-password`, {}),
  // #820: Pro-Projekt Token-Budget Override.
  getProjectTokenBudget: (id: string) =>
    api.get<{project_id:string; hard_per_hour:number|null; warn_per_hour:number|null}>(`/projects/${id}/token-budget`),
  setProjectTokenBudget: (id: string, body: {hard_per_hour:number|null; warn_per_hour:number|null}) =>
    api.put<{updated:boolean; project_id:string; hard_per_hour:number|null; warn_per_hour:number|null}>(`/projects/${id}/token-budget`, body),
  // #821: Pro-Projekt Compaction-Threshold Override.
  getProjectCompactionThreshold: (id: string) =>
    api.get<{project_id:string; threshold:number|null; model:string; context_window:number; default_threshold:number}>(`/projects/${id}/compaction-threshold`),
  setProjectCompactionThreshold: (id: string, body: {threshold:number|null}) =>
    api.put<{updated:boolean; project_id:string; threshold:number|null}>(`/projects/${id}/compaction-threshold`, body),
  // Voice Interface (#131 + Provider-Registry #794)
  voiceStatus:    () => api.get<{
    installed: boolean;
    stt: { host: string; port: number; available: boolean };
    tts: { host: string; port: number; available: boolean };
    stt_providers: { id: string; name: string; available: boolean; languages: string[] }[];
    tts_providers: { id: string; name: string; available: boolean; voices: { id: string; name: string; language: string; gender: string | null }[] }[];
    current_stt: { provider: string };
    current_tts: { provider: string; voice: string | null };
    global_stt_provider: string | null;
    global_tts_provider: string | null;
    user_preferences: { stt_provider: string | null; stt_voice: string | null; tts_provider: string | null; tts_voice: string | null };
    default_agent: string;
  }>("/voice/status"),
  voicePreferences:    () => api.get<{stt_provider:string|null;stt_voice:string|null;tts_provider:string|null;tts_voice:string|null}>("/voice/preferences"),
  setVoicePreference:  (provider_type: "stt" | "tts", provider_id: string, voice_id: string | null) => api.put<{stt_provider:string|null;stt_voice:string|null;tts_provider:string|null;tts_voice:string|null}>("/voice/preferences", { provider_type, provider_id, voice_id }),
  setVoiceGlobalProvider: (provider_type: "stt" | "tts", provider_id: string) => api.put<{global_stt_provider:string|null;global_tts_provider:string|null}>("/voice/providers/default", { provider_type, provider_id }),
  voiceText:      (text: string, agent_id?: string) => api.post<{text:string;agent_id:string}>("/voice", { text, agent_id }),
  voiceTts:       async (text: string) => { const token = getToken(); const res = await fetch("/api/voice/tts", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }); if (!res.ok) { if (res.status === 401 && token && token === getToken()) notifyAuthExpired("/voice/tts"); throw new Error(`TTS error: ${res.status}`); } return res.blob(); },
  voiceStt:       async (audioBlob: Blob) => { const token = getToken(); const fd = new FormData(); fd.append("audio", audioBlob, "recording.wav"); const res = await fetch("/api/voice/stt", { method: "POST", credentials: "include", body: fd }); if (!res.ok) { if (res.status === 401 && token && token === getToken()) notifyAuthExpired("/voice/stt"); throw new Error(`STT error: ${res.status}`); } return res.json() as Promise<{text: string}>; },
  // A2A Federation (#50)
  a2aPeers:       () => api.get<A2APeersResponse>("/admin/a2a/peers"),
  a2aSetSecret:   (secret: string) => api.put<{ok:boolean}>("/admin/a2a/secret", { secret }),
  a2aUpsertPeer:  (d: A2APeer) => api.put<{ok:boolean;action:string}>("/admin/a2a/peers", d),
  a2aDeletePeer:  (name: string) => api.delete<{ok:boolean}>(`/admin/a2a/peers/${name}`),
  a2aTestPeer:    (name: string) => api.post<A2ATestResult>(`/admin/a2a/test/${name}`, {}),
  a2aSendTask:    (peer: string, agent_id: string, message: string) =>
    api.post<{ok:boolean;response:string;status:number}>(`/admin/a2a/send/${peer}`, { agent_id, message }),
  // Migration (MigrationPage refactor — #956)
  migrationExport: async (includeAmem: boolean): Promise<Blob> => {
    const res = await fetch(`/api/admin/migration/export?include_amem=${includeAmem}`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) { const d = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(d.detail || `HTTP ${res.status}`); }
    return res.blob();
  },
  migrationImport: async (file: File): Promise<{ imported: boolean }> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/admin/migration/import", { method: "POST", credentials: "include", body: form });
    if (!res.ok) { const d = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(d.detail || `HTTP ${res.status}`); }
    return res.json();
  },
  // HydraHub
  hubIndex:     () => api.get<HubIndex>("/hub/index"),
  hubInstalled: () => api.get<HubInstalledEntry[]>("/hub/installed"),
  hubLocalPlugins: () => api.get<{plugins: any[]; count: number}>("/hub/local-plugins"),
  hubInstall:   (d: HubInstallRequest) => api.post<HubInstallResult>("/hub/install", d),
  hubUninstall: (agentId: string) => api.delete<{uninstalled:boolean;agent_id:string}>(`/hub/installed/${agentId}`),
  hubUninstallPlugin: (pluginId: string) => api.delete<{uninstalled:boolean;plugin_id:string}>(`/hub/installed/plugin/${pluginId}`),
  // Extensions (ExtensionsPage refactor — #956)
  extensionList:   () => api.get<Extension[]>("/admin/extensions"),
  extensionAction: (id: string, action: "install" | "uninstall", params?: Record<string, string>, signal?: AbortSignal) =>
    fetch(`/api/admin/extensions/${id}/${action}`, {
      method: "POST",
      credentials: "include",
      signal,
      headers: params ? { "Content-Type": "application/json" } : undefined,
      body: params ? JSON.stringify({ params }) : undefined,
    }),
  // Jobs (JobsPage — #929)
  jobsList:   (filters?: { status?: string; type?: string; project_id?: string }) => {
    const params = new URLSearchParams();
    if (filters?.status)     params.set("status",     filters.status);
    if (filters?.type)       params.set("type",       filters.type);
    if (filters?.project_id) params.set("project_id", filters.project_id);
    const qs = params.toString();
    return api.get<{jobs: JobMeta[]}>(`/admin/jobs${qs ? `?${qs}` : ""}`);
  },
  jobsGet:    (jobId: string) => api.get<JobMeta>(`/admin/jobs/${jobId}`),
  jobsCancel: (jobId: string) => api.post<JobMeta>(`/admin/jobs/${jobId}/cancel`, {}),
  // #933: Media Generation
  jobsGenerate: (body: { type: string; prompt: string; options?: Record<string,unknown>; project_id?: string; agent_id?: string }) =>
    api.post<JobMeta>(`/admin/jobs/generate`, body),
  // Tailscale (#111)
  tailscaleStatus:    () => api.get<{api_configured:boolean;local:{logged_in:boolean;ip:string|null;hostname:string|null;dns_name?:string;online:boolean}}>("/admin/tailscale/status"),
  tailscaleDevices:   () => api.get<{devices:{id:string;hostname:string;name:string;ip:string;os:string;online:boolean;last_seen:string;tags:string[]}[];count:number}>("/admin/tailscale/devices"),
  tailscaleScan:      () => api.post<{total_devices:number;online_devices:number;hydrahive_found:number;instances:{hostname:string;name:string;ip:string;port:number;scheme?:string;os:string}[]}>("/admin/tailscale/scan", {}),
  tailscaleAutoPeer:  (hostname: string, ip: string, port: number, scheme?: string, name?: string) => api.post<{ok:boolean;peer_name:string;url:string}>("/admin/tailscale/auto-peer", { hostname, ip, port, scheme: scheme || "https", name }),
  tailscaleRemoveDevice: (deviceId: string) => api.delete<{ok:boolean}>(`/admin/tailscale/devices/${deviceId}`),
  tailscaleInvite:      () => api.post<{ok:boolean;auth_key:string;expires:string}>("/admin/tailscale/invite", {}),
  tailscaleConfig:    (api_key: string, tailnet?: string) => api.put<{ok:boolean}>("/admin/tailscale/config", { api_key, tailnet: tailnet || "-" }),
  // Plugins (#110)
  pluginsList:          () => api.get<{plugins:PluginInfo[];legacy_plugins:unknown[];hooks:Record<string,number>;total:number}>("/plugins"),
  pluginGet:            (id: string) => api.get<PluginInfo & {agents:string[]}>(`/plugins/${id}`),
  pluginEnable:         (id: string) => api.post<{ok:boolean;plugin:PluginInfo}>(`/plugins/${id}/enable`, {}),
  pluginDisable:        (id: string) => api.post<{ok:boolean;plugin:PluginInfo}>(`/plugins/${id}/disable`, {}),
  pluginReload:         (id: string) => api.post<{ok:boolean;plugin:PluginInfo}>(`/plugins/${id}/reload`, {}),
  pluginsReloadAll:     () => api.post<{reloaded:number;plugins:PluginInfo[]}>("/plugins/reload", {}),
  pluginAgentGet:       (agentId: string) => api.get<{agent_id:string;plugins:string[]}>(`/plugins/agents/${agentId}`),
  pluginAgentSet:       (agentId: string, pluginIds: string[]) => api.put<{ok:boolean}>(`/plugins/agents/${agentId}`, { plugin_ids: pluginIds }),
  // ClawhHub
  clawhubStatus:      () => api.get<{installed:boolean;path:string|null;token_configured:boolean;token_preview:string|null}>("/hub/clawhub/status"),
  clawhubSetToken:    (token: string) => api.put<{ok:boolean;token_preview:string}>("/hub/clawhub/config", { token }),
  clawhubInstallCli:  () => api.post<{ok:boolean;output:string}>("/hub/clawhub/install-cli", {}),
  clawhubSkills:      (q: string) => api.get<{items:ClawhubSkillItem[]}>(`/hub/clawhub/skills?q=${encodeURIComponent(q)}`),
  clawhubPackages:    (family: string) => api.get<{items:ClawhubPackageItem[]}>(`/hub/clawhub/packages?family=${encodeURIComponent(family)}`),
  clawhubInstallSkill: (slug: string, agent_id: string, force?: boolean) =>
    api.post<{installed:boolean;skill_name:string;agent_id:string;file:string}>("/hub/clawhub/skill/install", { slug, agent_id, force: force ?? false }),

  // ── #955 VMs ─────────────────────────────────────────────────────────────
  vmsList:           () => api.get<{vms?: any[]; items?: any[]}>(`/admin/vms`),
  vmsGet:            (vmId: string) =>
    api.get<any>(`/admin/vms/${vmId}`),
  vmsCreate:         (body: { name: string; cpu: number; ram_mb: number; disk_gb: number; iso_file?: string|null; network_mode?: string; bridge_iface?: string; import_job_id?: string }) =>
    api.post<any>('/admin/vms', body),
  vmsPatch:          (vmId: string, body: { cpu: number; ram_mb: number; disk_gb: number; network_mode: string; bridge_iface: string }) =>
    api.patch<any>(`/admin/vms/${vmId}`, body),
  vmsStart:          (vmId: string) => api.post<any>(`/admin/vms/${vmId}/start`, {}),
  vmsStop:           (vmId: string) => api.post<any>(`/admin/vms/${vmId}/stop`, {}),
  vmsPoweroff:       (vmId: string) => api.post<any>(`/admin/vms/${vmId}/poweroff`, {}),
  vmsDelete:         (vmId: string) => api.delete<any>(`/admin/vms/${vmId}`),
  vmsLog:            (vmId: string, lines?: number) =>
    api.get<{lines:string[];size_bytes:number}>(`/admin/vms/${vmId}/log?lines=${lines ?? 200}`),
  vmsIsoList:        () => api.get<{isos?: any[]}>(`/admin/vms/isos`),
  vmsIsoDelete:      (filename: string) =>
    api.delete<any>(`/admin/vms/isos/${encodeURIComponent(filename)}`),
  vmsImportFromPath:  (body: { path: string }) =>
    api.post<{job_id:string}>(`/admin/vms/import/from-path`, body),
  vmsImportStatus:   (jobId: string) =>
    api.get<{status:string;progress_pct?:number;error?:string}>(`/admin/vms/import/${jobId}/status`),

  // ── #312/#314 Blueprints ─────────────────────────────────────────────────
  blueprintList:      () => api.get<any[]>(`/admin/blueprints`),
  blueprintGet:       (id: string) => api.get<Record<string,any>>(`/admin/blueprints/${id}`),
  blueprintExport:    (id: string) => api.get<Record<string,any>>(`/admin/blueprints/export/${id}`),
  blueprintImport:    (data: Record<string,any>) =>
    api.post<{imported:string}>(`/admin/blueprints/import`, data),
  blueprintDelete:    (id: string) =>
    api.delete<{deleted:string}>(`/admin/blueprints/${id}`),
  blueprintInstall:   (bpId: string, agentId: string) =>
    api.post<Record<string,any>>(`/admin/blueprints/${bpId}/install/${agentId}`, {}),
  promoteScratchpad:  (agentId: string, blueprintId: string, descriptionOverride?: string) =>
    api.post<Record<string,any>>(`/admin/blueprints/promote-scratchpad/${agentId}`, {
      blueprint_id: blueprintId, description_override: descriptionOverride ?? "",
    }),
  promotePreview:     (agentId: string) =>
    api.get<Record<string,any>>(`/admin/blueprints/promote-scratchpad/${agentId}/preview`),

  // ── #313 Scratchpad ─────────────────────────────────────────────────────
  scratchpadGet:  (agentId: string) =>
    api.get<{content:string}>(`/admin/agents/${agentId}/scratchpad`),
  scratchpadSave: (agentId: string, content: string) =>
    api.put<{saved:boolean}>(`/admin/agents/${agentId}/scratchpad`, { data: { content } }),
  scratchpadClear: (agentId: string) =>
    api.delete<{cleared:boolean}>(`/admin/agents/${agentId}/scratchpad`),

  uploadFile: async (projectId: string, file: File): Promise<{ path: string; filename: string; size: number }> => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`/api/projects/${projectId}/upload`, {
      method: 'POST',
      credentials: 'include',
      body: form,
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  },
};

export interface A2APeer {
  name:        string;
  url:         string;
  secret:      string;
  description: string;
}
export interface A2APeersResponse {
  has_secret: boolean;
  peers:      (Omit<A2APeer, "secret"> & { secret: string })[];
}
export interface A2ATestResult {
  ok:           boolean;
  status:       number;
  peer_name:    string;
  peer_version: string;
  agents:       { id: string; name: string; description: string }[];
  error:        string;
}

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
  ssh_port:    number;   // #677
  ollama_port: number;
  has_ssh_key: boolean;
}

export interface WksConfigPayload {
  ip:          string;
  ssh_user:    string;
  ssh_port:    number;   // #677
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
  user_whitelist?:      string[];
  user_blacklist?:      string[];
  role_whitelist?:      string[];
  role_blacklist?:      string[];
  channel_modes?:       Record<string, string>;
  channel_names?:       Record<string, string>;
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
  user_whitelist:       string[];
  user_blacklist:       string[];
  role_whitelist:       string[];
  role_blacklist:       string[];
  channel_modes:        Record<string, string>;
  channel_names:        Record<string, string>;
}

export interface WhatsAppConfig {
  private_chats_enabled: boolean;
  group_chats_enabled:   boolean;
  require_keyword:       string;
  allowed_numbers:       string[];
  blocked_numbers:       string[];
  owner_numbers:         string[];
  voice_mode:            string;
  voice_name:            string;
}

export interface WhatsAppStatus extends Partial<WhatsAppConfig> {
  configured: boolean;
  status:     "disconnected" | "connecting" | "waiting_qr" | "connected" | "reconnecting" | "bridge_unavailable" | "saved" | "error";
  qr:         string | null;
  phone:      string | null;
  bridge_error?: string | null;
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

export interface AgentLiveEntry {
  id:                 string;
  identity:           string;
  type:               string | null;
  model:              string | null;
  status:             string;
  current_activity:   string | null;
  restart_count:      number;
  last_heartbeat_age:  number | null;
  heartbeat_timeout:   number | null;
  heartbeat_interval:  number | null;
  tokens_1h:          number;
  token_warn_threshold: number;
  token_history?:     { minute: number; tokens: number }[];
}

export interface AgentsLiveReport {
  agents: AgentLiveEntry[];
  count:  number;
}

export interface DoctorCheck {
  name:   string;
  status: "ok" | "warn" | "error";
  detail: string;
  hint?:  string;
  fix?:   string;
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
  tool_call_id?: string;
  metadata?:  Record<string, unknown>;
}

export interface SessionFull {
  id:         string;
  project_id: string;
  started_at: string;
  ended_at:   string | null;
  messages:   SessionMessage[];
}

export interface UsageModelBreakdown {
  tokens: { input: number; output: number; cache_read: number; cache_write: number };
  cost:   { input: number; output: number; cache_read: number; cache_write: number; total: number };
}
export interface UsageProject {
  project_id:          string;
  total_input:         number;
  total_output:        number;
  total_cache_read:    number;
  total_cache_write:   number;
  sessions_with_usage: number;
  total_cost:          number;
  cache_hit_rate:      number;
  model_breakdown:     Record<string, UsageModelBreakdown>;
}
export interface UsageStats {
  projects:    UsageProject[];
  grand_total: { input: number; output: number; cache_read: number; cache_write: number; cost: number; cache_hit_rate: number };
  pricing_ref: Record<string, { input: number; output: number; cache_write: number; cache_read: number }>;
}

export interface CleanupConfig {
  transcript_days:  number;
  backup_keep:      number;
  warn_pct_yellow:  number;
  warn_pct_red:     number;
}

export interface CleanupResult {
  ran_at:                    string;
  elapsed_ms:                number;
  deleted_transcripts:       number;
  deleted_backups:           number;
  deleted_orphan_projects:   number;
  deleted_stale_indices:     number;
  disk: { total_gb: number; used_gb: number; free_gb: number; percent: number };
}

export interface CleanupStatus {
  last_result: CleanupResult | null;
  disk:        { total_gb: number; used_gb: number; free_gb: number; percent: number };
  config:      CleanupConfig;
}

export interface UpdateStatus {
  status:      string;   // "ok" | "running" | "error" | "unknown"
  started_at?: string;
  finished_at?: string;
  commit?:     string;
  commit_full?: string;
  available?:  boolean;
  remote_commit?: string;
  remote_commit_full?: string;
  source?: string;
  message?:    string;
  log_tail?:   string[];
  error?:      string;
}

export interface SearxngStatus {
  installed:      boolean;
  service_active: boolean;
  service_uptime: string;
  http_ok:        boolean;
  json_ok:        boolean;
  url:            string;
  version:        string | null;
  engines:        string[];
  config_exists:  boolean;
}

export interface SearxngResult {
  title:   string;
  url:     string;
  snippet: string;
  engine:  string;
}

export interface SearxngTestResult {
  query?:       string;
  total?:       number;
  results:      SearxngResult[];
  suggestions?: string[];
  error?:       string;
}

export interface KnowledgeResult {
  id:       string;
  content:  string;
  score?:   number;
  keywords: string[];
  category: string;
  tags:     string[];
  context:  string;
}

export interface Schedule {
  id:         string;
  name:       string;
  project_id: string;
  agent_id:   string;
  cron:       string;
  message:    string;
  enabled:    boolean;
  timezone:   string;
  last_run:   string | null;
  next_run:   string | null;
  created_by: string;
}

export interface SchedulePayload {
  name:       string;
  project_id: string;
  agent_id:   string;
  cron:       string;
  message:    string;
  enabled?:   boolean;
  timezone?:  string;
}

export interface AppNotification {
  id:         string;
  user:       string;
  type:       string;
  title:      string;
  body:       string;
  link:       string | null;
  read:       boolean;
  created_at: string;
}

export interface HubPackage {
  id:          string;
  type:        string;
  name:        string;
  description: string;
  author:      string;
  author_url?: string;
  category:    string;
  tags:        string[];
  icon:        string;
  version:     string;
  source?:     string;
  source_url?: string;
  license?:    string;
  _path:       string;
}

export interface HubIndex {
  version:  string;
  updated:  string;
  count:    number;
  packages: HubPackage[];
}

export interface HubInstalledEntry extends HubPackage {
  installed_agent_id: string;
}

export interface HubInstallRequest {
  id:                  string;
  agent_id_override?:  string;
  model_override?:     string;
}

export interface HubInstallResult {
  installed: boolean;
  agent_id:  string;
  name:      string;
  category:  string;
}

// Plugins (#110)
export interface PluginInfo {
  id:          string;
  name:        string;
  version:     string;
  description: string;
  author:      string;
  type:        string;
  enabled:     boolean;
  error:       string | null;
  path:        string;
  tools:       string[];
  hook_count:  number;
  permissions: string[];
  agents?:     string[];
}

// Extensions (ExtensionsPage refactor — #956)
export interface Extension {
  id:           string;
  name:         string;
  description:  string;
  icon:         string;
  category:     string;
  installed:    boolean;
  active:       boolean;
  http_ok:      boolean;
  open_url:     string | null;
  has_uninstall: boolean;
  external?:    boolean;
  config_hint?: string;
  plugin_id?:   string;
  install_params?: { key: string; label: string; type: string; placeholder?: string; required?: boolean; description?: string }[];
  validation?:  { valid: boolean; errors: string[]; warnings: string[] };
}

export interface ClawhubSkillItem {
  slug:  string;
  name:  string;
  score: number | null;
}

export interface ClawhubPackageItem {
  name:        string;
  displayName: string;
  summary:     string;
  family:      string;
  executesCode: boolean;
  latestVersion: string;
  ownerHandle: string;
  updatedAt:   number;
}

// Jobs (JobsPage — #929)
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobArtifact {
  filename:    string;
  size:        number;
  mime:        string;
  created_at:  string;
  download_url?: string;
}

export interface JobMeta {
  job_id:            string;
  type:              string;
  provider:          string;
  status:            JobStatus;
  created_at:        string;
  updated_at:        string;
  started_at:        string | null;
  finished_at:       string | null;
  created_by:        string | null;
  project_id:        string | null;
  agent_id:          string | null;
  input_summary:     Record<string, unknown>;
  progress_percent:  number | null;
  progress_message:  string | null;
  artifacts:         JobArtifact[];
  error:             string | null;
}
