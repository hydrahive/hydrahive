import { useEffect, useState } from "react";
import { api, type Schedule, type SchedulePayload } from "@/lib/api";
import { useTranslation } from "react-i18next";
import {
  Clock, Plus, Trash2, Pencil, CheckCircle2, XCircle,
  Loader2, Calendar, Play,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

// ---------------------------------------------------------------- helpers

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------- modal

interface ModalProps {
  initial?: Partial<SchedulePayload>;
  projects: { id: string; name: string }[];
  agents:   { id: string; name: string }[];
  onSave:   (d: SchedulePayload) => void;
  onClose:  () => void;
}

function ScheduleModal({ initial, projects, agents, onSave, onClose }: ModalProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<SchedulePayload>({
    name:       initial?.name       ?? "",
    project_id: initial?.project_id ?? (projects[0]?.id ?? ""),
    agent_id:   initial?.agent_id   ?? (agents[0]?.id   ?? ""),
    cron:       initial?.cron       ?? "0 8 * * *",
    message:    initial?.message    ?? "",
    enabled:    initial?.enabled    ?? true,
    timezone:   initial?.timezone   ?? "UTC",
  });

  const set = (k: keyof SchedulePayload, v: unknown) =>
    setForm(f => ({ ...f, [k]: v }));

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl w-full max-w-lg p-6 space-y-4">
        <h2 className="text-lg font-semibold">
          {initial ? t("schedules.editTitle") : t("schedules.newTitle")}
        </h2>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldName")}</span>
          <input
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            value={form.name}
            onChange={e => set("name", e.target.value)}
          />
        </label>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldProject")}</span>
          <select
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            value={form.project_id}
            onChange={e => set("project_id", e.target.value)}
          >
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldAgent")}</span>
          <select
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            value={form.agent_id}
            onChange={e => set("agent_id", e.target.value)}
          >
            {agents.map(a => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldCron")}</span>
          <input
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-blue-500"
            value={form.cron}
            onChange={e => set("cron", e.target.value)}
            placeholder="0 8 * * *"
          />
          <p className="text-xs text-zinc-500 mt-1">{t("pageDesc.schedulesCron")}</p>
        </label>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldTimezone")}</span>
          <input
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            value={form.timezone}
            onChange={e => set("timezone", e.target.value)}
            placeholder="UTC"
          />
        </label>

        <label className="block">
          <span className="text-xs text-zinc-400">{t("schedules.fieldMessage")}</span>
          <textarea
            className="mt-1 w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500 resize-none"
            rows={3}
            value={form.message}
            onChange={e => set("message", e.target.value)}
            placeholder={t("schedules.messagePlaceholder")}
          />
        </label>

        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={e => set("enabled", e.target.checked)}
            className="rounded"
          />
          {t("schedules.fieldEnabled")}
        </label>

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={() => onSave(form)}
            disabled={!form.name || !form.cron || !form.message}
            className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40"
          >
            {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- main

export default function SchedulesPage() {
  const { t } = useTranslation();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [projects,  setProjects]  = useState<{ id: string; name: string }[]>([]);
  const [agents,    setAgents]    = useState<{ id: string; name: string }[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [modal,     setModal]     = useState<null | "new" | Schedule>(null);
  const [error,     setError]     = useState("");
  const [confirmState, setConfirmState] = useState<{action: () => void; title: string; message: string} | null>(null);

  async function load() {
    setLoading(true);
    try {
      const [sr, pr, ar] = await Promise.all([
        api.schedules(),
        api.projects() as Promise<Record<string, unknown>>,
        api.agents()   as Promise<Record<string, unknown>>,
      ]);
      setSchedules(sr.schedules);
      // projects returns { projects: { id: cfg } } or { id: cfg } map
      const pmap = (pr as any).projects ?? pr;
      setProjects(
        Object.entries(pmap).map(([id, cfg]: [string, any]) => ({
          id,
          name: cfg?.name ?? id,
        }))
      );
      const amap = (ar as any).agents ?? ar;
      setAgents(
        Object.entries(amap).map(([id, cfg]: [string, any]) => ({
          id,
          name: cfg?.config?.identity?.name ?? cfg?.name ?? id,
        }))
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleSave(data: SchedulePayload) {
    try {
      if (modal && typeof modal === "object") {
        await api.updateSchedule(modal.id, data);
      } else {
        await api.createSchedule(data);
      }
      setModal(null);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleToggle(s: Schedule) {
    try {
      await api.updateSchedule(s.id, { enabled: !s.enabled });
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRun(s: Schedule) {
    try {
      await api.runScheduleNow(s.id);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  function handleDelete(s: Schedule) {
    setConfirmState({
      title: t("confirm.titleDelete"),
      message: t("schedules.confirmDelete", { name: s.name }),
      action: async () => {
        try {
          await api.deleteSchedule(s.id);
          load();
        } catch (e) {
          setError(String(e));
        }
      },
    });
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Calendar className="w-6 h-6 text-blue-400" />
          <div>
            <h1 className="text-xl font-semibold">{t("schedules.title")}</h1>
            <p className="text-xs text-muted-foreground">{t("pageDesc.schedules")}</p>
          </div>
        </div>
        <button
          onClick={() => setModal("new")}
          className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500"
        >
          <Plus className="w-4 h-4" />
          {t("schedules.new")}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
        </div>
      ) : schedules.length === 0 ? (
        <div className="text-center py-16 text-zinc-500">
          <Clock className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>{t("schedules.empty")}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {schedules.map(s => (
            <div
              key={s.id}
              className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 flex items-start gap-4"
            >
              {/* Enable/disable toggle */}
              <button
                onClick={() => handleToggle(s)}
                title={s.enabled ? t("schedules.disable") : t("schedules.enable")}
                className="mt-0.5 shrink-0"
              >
                {s.enabled
                  ? <CheckCircle2 className="w-5 h-5 text-green-500" />
                  : <XCircle     className="w-5 h-5 text-zinc-600" />}
              </button>

              {/* Main info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium truncate">{s.name}</span>
                  <span className="font-mono text-xs bg-zinc-800 px-2 py-0.5 rounded text-blue-300">
                    {s.cron}
                  </span>
                  <span className="text-xs text-zinc-500">{s.timezone}</span>
                </div>
                <p className="text-sm text-zinc-400 mt-1 truncate">{s.message}</p>
                <div className="flex gap-4 mt-2 text-xs text-zinc-500 flex-wrap">
                  <span>{t("schedules.project")}: <span className="text-zinc-300">{s.project_id}</span></span>
                  <span>{t("schedules.agent")}: <span className="text-zinc-300">{s.agent_id}</span></span>
                  <span>{t("schedules.lastRun")}: <span className="text-zinc-300">{fmtDate(s.last_run)}</span></span>
                  <span>{t("schedules.nextRun")}: <span className="text-zinc-300">{fmtDate(s.next_run)}</span></span>
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-1 shrink-0">
                <button
                  onClick={() => handleRun(s)}
                  className="p-1.5 rounded hover:bg-green-900/40 text-zinc-400 hover:text-green-400"
                  title={t("schedules.runNow")}
                >
                  <Play className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setModal(s)}
                  className="p-1.5 rounded hover:bg-zinc-700 text-zinc-400 hover:text-white"
                  title={t("common.edit")}
                >
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  onClick={() => handleDelete(s)}
                  className="p-1.5 rounded hover:bg-red-900/40 text-zinc-400 hover:text-red-400"
                  title={t("common.delete")}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modal && (
        <ScheduleModal
          initial={typeof modal === "object" ? modal : undefined}
          projects={projects}
          agents={agents}
          onSave={handleSave}
          onClose={() => setModal(null)}
        />
      )}
      <ConfirmDialog
        open={!!confirmState}
        title={confirmState?.title || ""}
        message={confirmState?.message || ""}
        onConfirm={() => { confirmState?.action(); setConfirmState(null); }}
        onCancel={() => setConfirmState(null)}
        variant="danger"
      />
    </div>
  );
}
