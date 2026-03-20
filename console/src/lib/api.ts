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
  agents:        ()           => api.get<Record<string,unknown>>("/agents"),
  projects:      ()           => api.get<Record<string,unknown>>("/projects"),
  createProject: (d: unknown) => api.post("/projects", d),
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
};
