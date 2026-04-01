import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import { useTranslation } from "react-i18next";

interface AgentEntry {
  config: { type: string; identity: string; model: string };
}

export function ProjectCreatePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [id,          setId]          = useState("");
  const [name,        setName]        = useState("");
  const [description, setDescription] = useState("");
  const [boss,        setBoss]        = useState("");
  const [workers,     setWorkers]     = useState<string[]>([]);
  const [workerInput, setWorkerInput] = useState("");
  const [samba,       setSamba]       = useState(true);
  const [showSwarm,   setShowSwarm]   = useState(false);
  const [githubRepo,  setGithubRepo]  = useState("");
  const [gitClone,    setGitClone]    = useState(false);
  const [gitBranch,   setGitBranch]   = useState("main");
  const [gitToken,    setGitToken]    = useState("");

  const [agents,      setAgents]      = useState<Record<string, AgentEntry>>({});
  const [submitting,  setSubmitting]  = useState(false);
  const [error,       setError]       = useState("");

  useEffect(() => {
    api.agents().then(d => setAgents(d as Record<string, AgentEntry>)).catch(() => {});
  }, []);

  const agentIds = Object.keys(agents);

  function addWorker() {
    const w = workerInput.trim();
    if (w && !workers.includes(w)) setWorkers(ws => [...ws, w]);
    setWorkerInput("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!id || !name || !boss) { setError(t("projectCreate.requiredFields")); return; }
    setSubmitting(true);
    try {
      await api.createProject({ id, name, description, boss, workers, samba, nfs: false, show_swarm: showSwarm, github_repo: githubRepo.trim() });
      // Git Clone nach Erstellung
      if (gitClone && githubRepo.trim()) {
        try {
          let cloneUrl = githubRepo.trim();
          if (!cloneUrl.startsWith("http")) cloneUrl = `https://github.com/${cloneUrl}`;
          if (cloneUrl.endsWith("/")) cloneUrl = cloneUrl.slice(0, -1);
          if (!cloneUrl.endsWith(".git")) cloneUrl += ".git";
          if (gitToken.trim()) {
            cloneUrl = cloneUrl.replace("https://", `https://${gitToken.trim()}@`);
          }
          await api.post(`/projects/${id}/git-clone`, { url: cloneUrl, branch: gitBranch || "main" });
        } catch (cloneErr: any) {
          setError(`Projekt erstellt, aber Git-Clone fehlgeschlagen: ${cloneErr.message}`);
          setSubmitting(false);
          return;
        }
      }
      navigate("/projects");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="p-6 max-w-2xl space-y-6 overflow-y-auto flex-1">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate("/projects")}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-xl font-semibold">{t("projectCreate.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("projectCreate.subtitle")}</p>
        </div>
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <form onSubmit={submit} className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("projectCreate.projectId")}</label>
            <input
              value={id}
              onChange={e => setId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
              placeholder={t("projectCreate.projectIdPlaceholder")}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring font-mono"
            />
            <p className="text-xs text-muted-foreground">{t("projectCreate.projectIdHint")}</p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">{t("projectCreate.name")}</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={t("projectCreate.namePlaceholder")}
              className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">{t("projectCreate.description")}</label>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            placeholder={t("projectCreate.descriptionPlaceholder")}
            className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none"
          />
        </div>

        <div className="space-y-3 rounded-lg border p-4 bg-muted/20">
          <label className="text-sm font-medium">Git-Repository <span className="text-muted-foreground font-normal">(optional)</span></label>
          <input
            value={githubRepo}
            onChange={e => setGithubRepo(e.target.value)}
            placeholder="org/repo oder https://github.com/org/repo"
            className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring font-mono"
          />
          {githubRepo.trim() && (
            <>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={gitClone} onChange={e => setGitClone(e.target.checked)} className="rounded" />
                Repository automatisch in das Projekt klonen
              </label>
              {gitClone && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">Branch</label>
                    <input
                      value={gitBranch}
                      onChange={e => setGitBranch(e.target.value)}
                      placeholder="main"
                      className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">Token <span className="opacity-50">(für private Repos)</span></label>
                    <input
                      type="password"
                      value={gitToken}
                      onChange={e => setGitToken(e.target.value)}
                      placeholder="ghp_... oder leer für public"
                      className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                    />
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">{t("projectCreate.bossAgent")}</label>
          <select
            value={boss}
            onChange={e => setBoss(e.target.value)}
            className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">{t("projectCreate.selectAgent")}</option>
            {agentIds.map(aid => (
              <option key={aid} value={aid}>
                {agents[aid].config.identity} ({aid})
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <label className="text-sm font-medium">{t("projectCreate.workerAgents")}</label>
          <div className="flex gap-2">
            <select
              value={workerInput}
              onChange={e => setWorkerInput(e.target.value)}
              className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">{t("projectCreate.addWorker")}</option>
              {agentIds.filter(a => !workers.includes(a)).map(aid => (
                <option key={aid} value={aid}>
                  {agents[aid].config.identity} ({aid})
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={addWorker}
              disabled={!workerInput}
              className="flex items-center gap-1 px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors disabled:opacity-40"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
          {workers.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {workers.map(w => (
                <span key={w} className="flex items-center gap-1 px-2 py-0.5 text-xs bg-secondary rounded-full">
                  {w}
                  <button type="button" onClick={() => setWorkers(ws => ws.filter(x => x !== w))}>
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">{t("projectCreate.options")}</label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={samba} onChange={e => setSamba(e.target.checked)} className="rounded" />
            {t("projectCreate.sambaShare")}
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={showSwarm} onChange={e => setShowSwarm(e.target.checked)} className="rounded" />
            {t("projectCreate.showSwarm")}
          </label>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {submitting ? t("projectCreate.creating") : t("projectCreate.createBtn")}
          </button>
          <button
            type="button"
            onClick={() => navigate("/projects")}
            className="px-4 py-2 text-sm border rounded-md hover:bg-accent transition-colors"
          >
            {t("projectCreate.cancel")}
          </button>
        </div>
      </form>
    </div>
  );
}
