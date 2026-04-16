import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles, Save, RefreshCw, CheckCircle, AlertCircle, Info, AlertTriangle, Archive, Eye, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";
import { api, type ComposerBackup, type ComposerBackupPreview, type ComposerWarning } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface BlockDef { id: string; label: string; description: string }
interface CategoryDef { id: string; label: string; blocks: BlockDef[] }
interface PresetDef { id: string; label: string; description: string; selected: string[] }

const PRESET_CUSTOM = "__custom__";

export interface ProfileComposerProps {
  /** "me" (default) → /me/agent/composer/*; "admin" → /admin/agents/{agentId}/composer/*; "project" → /projects/{projectId}/composer/* */
  scope?: "me" | "admin" | "project";
  /** Erforderlich wenn scope = "admin". */
  agentId?: string;
  /** Erforderlich wenn scope = "project". */
  projectId?: string;
  /** Zeigt einen dezenten Hinweis, dass AGENT.md Vorrang vor soul.md hat. */
  showSoulHint?: boolean;
}

interface ComposerApi {
  loadBlocks: () => Promise<{categories: CategoryDef[]}>;
  loadPresets: () => Promise<{presets: PresetDef[]}>;
  loadProfile: () => Promise<{selected: string[]; preset: string | null; warnings: ComposerWarning[]; agent_md_exists: boolean; agent_md_mtime_matches: boolean; etag: string}>;
  preview: (selected: string[], preset: string | null) => Promise<{markdown: string; warnings: ComposerWarning[]; save_blocked: boolean}>;
  save: (selected: string[], preset: string | null, etag: string | null) => Promise<{backup_created: boolean; warnings: ComposerWarning[]; etag: string}>;
}

function buildApi(scope: "me" | "admin" | "project", agentId?: string, projectId?: string): ComposerApi {
  if (scope === "admin") {
    if (!agentId) throw new Error("ProfileComposer: agentId ist bei scope='admin' erforderlich.");
    return {
      loadBlocks: () => api.adminComposerBlocks(agentId),
      loadPresets: () => api.adminComposerPresets(agentId),
      loadProfile: () => api.adminComposerProfile(agentId),
      preview: (sel, p) => api.adminComposerPreview(agentId, sel, p),
      save: (sel, p, etag) => api.adminComposerSave(agentId, sel, p, etag),
    };
  }
  if (scope === "project") {
    if (!projectId) throw new Error("ProfileComposer: projectId ist bei scope='project' erforderlich.");
    return {
      loadBlocks: () => api.projectComposerBlocks(projectId),
      loadPresets: () => api.projectComposerPresets(projectId),
      loadProfile: () => api.projectComposerProfile(projectId),
      preview: (sel, p) => api.projectComposerPreview(projectId, sel, p),
      save: (sel, p, etag) => api.projectComposerSave(projectId, sel, p, etag),
    };
  }
  return {
    loadBlocks: () => api.composerBlocks(),
    loadPresets: () => api.composerPresets(),
    loadProfile: () => api.composerProfile(),
    preview: (sel, p) => api.composerPreview(sel, p),
    save: (sel, p, etag) => api.composerSave(sel, p, etag),
  };
}

export function ProfileComposer({ scope = "me", agentId, projectId, showSoulHint = false }: ProfileComposerProps = {}) {
  const { t } = useTranslation();
  const composerApi = useMemo(() => buildApi(scope, agentId, projectId), [scope, agentId, projectId]);
  const [categories, setCategories] = useState<CategoryDef[]>([]);
  const [presets, setPresets] = useState<PresetDef[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preset, setPreset] = useState<string>(PRESET_CUSTOM);
  const [preview, setPreview] = useState<string>("");
  const [warnings, setWarnings] = useState<ComposerWarning[]>([]);
  const [saveBlocked, setSaveBlocked] = useState(false);
  const [mtimeMatches, setMtimeMatches] = useState<boolean>(true);
  const [agentMdExists, setAgentMdExists] = useState<boolean>(false);
  const [etag, setEtag] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ currentEtag: string; message: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [presetSwitchTarget, setPresetSwitchTarget] = useState<string | null>(null);

  async function reloadAll() {
    setLoading(true);
    setMsg(null);
    setPreview("");
    try {
      const [blocksR, presetsR, profileR] = await Promise.all([
        composerApi.loadBlocks(),
        composerApi.loadPresets(),
        composerApi.loadProfile(),
      ]);
      setCategories(blocksR.categories);
      setPresets(presetsR.presets);
      setSelected(new Set(profileR.selected));
      setPreset(profileR.preset ?? PRESET_CUSTOM);
      setWarnings(profileR.warnings);
      setSaveBlocked(profileR.warnings.some(w => w.severity === "error"));
      setAgentMdExists(profileR.agent_md_exists);
      setMtimeMatches(profileR.agent_md_mtime_matches);
      setEtag(profileR.etag);
      setConflict(null);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reloadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [composerApi]);

  const selectedCount = selected.size;
  const hasSelection = selectedCount > 0;

  function toggle(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // Manuelle Änderung → Preset auf Custom zurücksetzen
    if (preset !== PRESET_CUSTOM) setPreset(PRESET_CUSTOM);
    setPreview("");
  }

  function requestPresetSwitch(targetId: string) {
    if (targetId === preset) return;
    if (targetId === PRESET_CUSTOM) {
      setPreset(PRESET_CUSTOM);
      return;
    }
    if (selected.size > 0) {
      setPresetSwitchTarget(targetId);
    } else {
      applyPreset(targetId);
    }
  }

  function applyPreset(targetId: string) {
    const p = presets.find(p => p.id === targetId);
    if (!p) return;
    setSelected(new Set(p.selected));
    setPreset(targetId);
    setPreview("");
    setPresetSwitchTarget(null);
  }

  async function refreshPreview() {
    setPreviewing(true); setMsg(null);
    try {
      const r = await composerApi.preview(
        Array.from(selected),
        preset === PRESET_CUSTOM ? null : preset,
      );
      setPreview(r.markdown);
      setWarnings(r.warnings);
      setSaveBlocked(r.save_blocked);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setPreviewing(false);
    }
  }

  async function doSave() {
    setConfirmOpen(false);
    setSaving(true); setMsg(null);
    try {
      const r = await composerApi.save(
        Array.from(selected),
        preset === PRESET_CUSTOM ? null : preset,
        etag,
      );
      setMsg({
        kind: "ok",
        text: t("composer.saveOk", {
          defaultValue: "AGENT.md gespeichert ({{count}} Bausteine).{{backup}}",
          count: selectedCount,
          backup: r.backup_created
            ? " " + t("composer.saveBackup", { defaultValue: "Vorheriger Stand liegt in AGENT.md.backup." })
            : "",
        }),
      });
      setWarnings(r.warnings);
      setSaveBlocked(false);
      setMtimeMatches(true);
      setAgentMdExists(true);
      setEtag(r.etag);
      setConflict(null);
    } catch (e) {
      const err = e as Error & { status?: number; detail?: { message?: string; current_etag?: string } };
      if (err && err.status === 409 && err.detail && typeof err.detail === "object") {
        setConflict({
          currentEtag: err.detail.current_etag || "",
          message: err.detail.message || t("composer.conflictMessage", {
            defaultValue: "AGENT.md wurde seit dem Laden geändert. Bitte Profil neu laden.",
          }),
        });
        setMsg(null);
      } else {
        setMsg({ kind: "err", text: err instanceof Error ? err.message : String(err) });
      }
    } finally {
      setSaving(false);
    }
  }

  const summaryText = useMemo(() => {
    if (!hasSelection) return t("composer.noSelection", { defaultValue: "Keine Bausteine ausgewählt." });
    return t("composer.selectionCount", {
      defaultValue: "{{count}} Baustein(e) ausgewählt",
      count: selectedCount,
    });
  }, [selectedCount, hasSelection, t]);

  if (loading) {
    return <div className="text-xs text-muted-foreground">{t("composer.loading", { defaultValue: "Lade Bausteine..." })}</div>;
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
          <Sparkles className="h-4 w-4" />
          {t("composer.title", { defaultValue: "Profil-Composer" })}
        </h2>
        <span className="text-xs text-muted-foreground">{summaryText}</span>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        {t("composer.intro", {
          defaultValue:
            "Wähle Bausteine oder ein Preset, um eine AGENT.md-Datei für deinen persönlichen Agent zu erzeugen. Die Auswahl wird in agent_profile.yaml gespeichert. soul.md und agent.yaml bleiben unverändert.",
        })}
      </p>

      {/* Preset-Auswahl */}
      <div className="rounded-md border border-border/70 bg-background p-3 space-y-2">
        <label className="text-xs font-semibold text-foreground">
          {t("composer.presetLabel", { defaultValue: "Preset" })}
        </label>
        <select
          value={preset}
          onChange={e => requestPresetSwitch(e.target.value)}
          className="w-full px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
        >
          <option value={PRESET_CUSTOM}>
            {t("composer.presetCustom", { defaultValue: "Custom (eigene Auswahl)" })}
          </option>
          {presets.map(p => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        {preset !== PRESET_CUSTOM && (
          <p className="text-xs text-muted-foreground">
            {presets.find(p => p.id === preset)?.description}
          </p>
        )}
      </div>

      {/* soul.md Hinweis — AGENT.md hat Vorrang */}
      {showSoulHint && (
        <div className="flex items-start gap-2 text-xs rounded-md border border-border/70 bg-muted/30 text-muted-foreground p-2">
          <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>
            {t("composer.soulHint", {
              defaultValue:
                "AGENT.md hat Vorrang vor soul.md. Diese Ansicht bearbeitet nur AGENT.md und agent_profile.yaml — agent.yaml, soul.md, tools[] und execution_modes bleiben unverändert.",
            })}
          </span>
        </div>
      )}

      {/* 409 Conflict — AGENT.md extern geändert, Reload nötig */}
      {conflict && (
        <div className="flex items-start gap-2 text-xs rounded-md border border-destructive/50 bg-destructive/10 text-destructive p-2">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <div className="flex-1 min-w-0 space-y-1.5">
            <p>{conflict.message}</p>
            <button
              type="button"
              onClick={() => void reloadAll()}
              className="inline-flex items-center gap-1.5 px-2 py-1 text-xs rounded-md border border-destructive/50 bg-background hover:bg-accent transition"
            >
              <RefreshCw className="h-3 w-3" />
              {t("composer.conflictReload", { defaultValue: "Profil neu laden" })}
            </button>
          </div>
        </div>
      )}

      {/* Externer AGENT.md-Hinweis */}
      {agentMdExists && !mtimeMatches && (
        <div className="flex items-start gap-2 text-xs rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 p-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>
            {t("composer.mtimeDrift", {
              defaultValue:
                "AGENT.md wurde seit dem letzten Composer-Save extern geändert. Speichern überschreibt den aktuellen Stand; ein Backup wird als AGENT.md.backup angelegt.",
            })}
          </span>
        </div>
      )}

      {/* Bausteine */}
      <div className="space-y-4">
        {categories.map(cat => (
          <div key={cat.id} className="rounded-md border border-border/70 bg-muted/20 p-3 space-y-2">
            <div className="text-xs font-semibold text-foreground">{cat.label}</div>
            <div className="grid gap-1.5">
              {cat.blocks.map(b => (
                <label
                  key={b.id}
                  className="flex items-start gap-2 rounded px-2 py-1.5 text-xs cursor-pointer hover:bg-muted/40 transition"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 flex-shrink-0"
                    checked={selected.has(b.id)}
                    onChange={() => toggle(b.id)}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="font-medium block">{b.label}</span>
                    <span className="text-muted-foreground block">{b.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="space-y-1.5">
          {warnings.map((w, i) => {
            const tone =
              w.severity === "error"
                ? "border-destructive/40 bg-destructive/10 text-destructive"
                : w.severity === "warning"
                ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                : "border-border/70 bg-muted/30 text-muted-foreground";
            const Icon = w.severity === "error" ? AlertCircle : w.severity === "warning" ? AlertTriangle : Info;
            return (
              <div key={`${w.rule}-${i}`} className={`flex items-start gap-2 text-xs rounded-md border p-2 ${tone}`}>
                <Icon className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>{w.message}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Aktionen */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={refreshPreview}
          disabled={!hasSelection || previewing}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs border rounded-md hover:bg-accent transition disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${previewing ? "animate-spin" : ""}`} />
          {t("composer.refreshPreview", { defaultValue: "Vorschau aktualisieren" })}
        </button>
        <button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={!hasSelection || saving || saveBlocked || !!conflict}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs rounded-md bg-primary text-primary-foreground hover:opacity-90 transition disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" />
          {t("composer.save", { defaultValue: "In AGENT.md übernehmen" })}
        </button>
      </div>

      {msg && (
        <div
          className={`flex items-start gap-2 text-xs rounded-md border p-2 ${
            msg.kind === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-destructive/40 bg-destructive/10 text-destructive"
          }`}
        >
          {msg.kind === "ok" ? <CheckCircle className="h-3.5 w-3.5 mt-0.5" /> : <AlertCircle className="h-3.5 w-3.5 mt-0.5" />}
          <span>{msg.text}</span>
        </div>
      )}

      {preview && (
        <div className="rounded-md border border-border/70 bg-background p-3">
          <div className="text-xs font-semibold text-muted-foreground mb-2">
            {t("composer.previewTitle", { defaultValue: "Vorschau (Markdown)" })}
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none text-xs">
            <ReactMarkdown>{preview}</ReactMarkdown>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        variant="danger"
        title={t("composer.confirmTitle", { defaultValue: "AGENT.md überschreiben?" })}
        message={t("composer.confirmMsg", {
          defaultValue:
            "Eine bestehende AGENT.md wird überschrieben. Der vorherige Stand wird automatisch als AGENT.md.backup gesichert. soul.md und agent.yaml bleiben unverändert.",
        })}
        confirmLabel={t("composer.confirmOk", { defaultValue: "Überschreiben" })}
        cancelLabel={t("composer.confirmCancel", { defaultValue: "Abbrechen" })}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={doSave}
      />

      <ConfirmDialog
        open={presetSwitchTarget !== null}
        variant="default"
        title={t("composer.presetSwitchTitle", { defaultValue: "Preset übernehmen?" })}
        message={t("composer.presetSwitchMsg", {
          defaultValue:
            "Preset-Auswahl ersetzt deine aktuelle Bausteinauswahl. Fortfahren?",
        })}
        confirmLabel={t("composer.presetSwitchOk", { defaultValue: "Übernehmen" })}
        cancelLabel={t("composer.presetSwitchCancel", { defaultValue: "Abbrechen" })}
        onCancel={() => setPresetSwitchTarget(null)}
        onConfirm={() => presetSwitchTarget && applyPreset(presetSwitchTarget)}
      />

      {/* #647: Backup-Panel */}
      <BackupPanel
        scope={scope}
        agentId={agentId}
        projectId={projectId}
        etag={etag}
        onRestored={(newEtag) => {
          setEtag(newEtag);
          setConflict(null);
          setMsg({ kind: "ok", text: t("composer.restoreOk", { defaultValue: "Wiederhergestellt aus Backup." }) });
          // Profile neu laden, damit selected/preset mit dem wiederhergestellten Stand sync ist.
          composerApi.loadProfile().then(p => {
            setSelected(new Set(p.selected));
            setPreset(p.preset ?? PRESET_CUSTOM);
            setAgentMdExists(p.agent_md_exists);
            setMtimeMatches(p.agent_md_mtime_matches);
          }).catch(() => {/* ignore */});
        }}
      />
    </section>
  );
}


// ─────────────────────────────────────────── #647 Backup-Panel

type BackupApi = {
  list:    () => Promise<{backups: ComposerBackup[]; count:number; truncated:boolean}>;
  preview: (name: string) => Promise<ComposerBackupPreview>;
  restore: (name: string, etag: string) => Promise<{restored: true; etag: string; from_backup: string; pre_restore_snapshot: string | null}>;
};

function buildBackupApi(scope: "me" | "admin" | "project", agentId?: string, projectId?: string): BackupApi {
  if (scope === "admin") {
    if (!agentId) throw new Error("BackupPanel: agentId erforderlich bei scope='admin'.");
    return {
      list:    () => api.adminComposerBackups(agentId),
      preview: (n) => api.adminComposerBackupPreview(agentId, n),
      restore: (n, e) => api.adminComposerBackupRestore(agentId, n, e),
    };
  }
  if (scope === "project") {
    if (!projectId) throw new Error("BackupPanel: projectId erforderlich bei scope='project'.");
    return {
      list:    () => api.projectComposerBackups(projectId),
      preview: (n) => api.projectComposerBackupPreview(projectId, n),
      restore: (n, e) => api.projectComposerBackupRestore(projectId, n, e),
    };
  }
  return {
    list:    () => api.composerBackups(),
    preview: (n) => api.composerBackupPreview(n),
    restore: (n, e) => api.composerBackupRestore(n, e),
  };
}

interface BackupPanelProps {
  scope:       "me" | "admin" | "project";
  agentId?:    string;
  projectId?:  string;
  etag:        string | null;
  onRestored:  (newEtag: string) => void;
}

function BackupPanel({ scope, agentId, projectId, etag, onRestored }: BackupPanelProps) {
  const { t } = useTranslation();
  const backupApi = useMemo(() => buildBackupApi(scope, agentId, projectId), [scope, agentId, projectId]);

  const [open, setOpen] = useState(false);
  const [backups, setBackups] = useState<ComposerBackup[] | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [previewName, setPreviewName] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const [restoreName, setRestoreName] = useState<string | null>(null);
  const [restoreBusy, setRestoreBusy] = useState(false);

  const loadBackups = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await backupApi.list();
      setBackups(data.backups);
      setCount(data.count);
      setTruncated(data.truncated);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [backupApi]);

  useEffect(() => {
    if (open && backups === null) {
      void loadBackups();
    }
  }, [open, backups, loadBackups]);

  async function openPreview(name: string) {
    setPreviewName(name);
    setPreviewContent("");
    setPreviewLoading(true);
    try {
      const data = await backupApi.preview(name);
      setPreviewContent(data.content);
    } catch (e: unknown) {
      setPreviewContent(t("composer.backupPreviewErr", {
        defaultValue: "Preview fehlgeschlagen: {{err}}",
        err: e instanceof Error ? e.message : String(e),
      }) as string);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function doRestore() {
    if (!restoreName) return;
    if (!etag) {
      setErr(t("composer.backupNoEtag", {
        defaultValue: "Kein aktueller ETag — Profil zuerst neu laden.",
      }) as string);
      setRestoreName(null);
      return;
    }
    setRestoreBusy(true);
    try {
      const res = await backupApi.restore(restoreName, etag);
      onRestored(res.etag);
      setRestoreName(null);
      // Liste neu laden — der Pre-Restore-Snapshot ist jetzt als neues Backup da.
      await loadBackups();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRestoreBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-border/70 bg-background">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-semibold hover:bg-muted/50"
      >
        <span className="flex items-center gap-2">
          <Archive className="h-3.5 w-3.5" />
          {t("composer.backupsTitle", { defaultValue: "Backups verwalten" })}
          {count !== null && (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              {count}
            </span>
          )}
        </span>
        <span className="text-[10px] text-muted-foreground">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="border-t border-border/70 p-3 space-y-2">
          {loading && (
            <div className="text-xs text-muted-foreground">
              {t("composer.backupsLoading", { defaultValue: "Lade…" })}
            </div>
          )}
          {err && (
            <div className="text-xs text-red-600 dark:text-red-400">{err}</div>
          )}
          {!loading && backups !== null && backups.length === 0 && (
            <div className="text-xs text-muted-foreground">
              {t("composer.backupsEmpty", {
                defaultValue: "Noch keine Backups vorhanden. Backups entstehen automatisch beim Speichern.",
              })}
            </div>
          )}
          {!loading && backups && backups.length > 0 && (
            <>
              {truncated && (
                <div className="text-[11px] text-amber-600 dark:text-amber-400">
                  {t("composer.backupsTruncated", {
                    defaultValue: "Liste auf 500 Einträge gekürzt.",
                  })}
                </div>
              )}
              <ul className="divide-y divide-border/60">
                {backups.map(b => (
                  <li key={b.name} className="flex items-center gap-2 py-1.5 text-xs">
                    <span className="flex-1 font-mono text-[11px]">{b.name}</span>
                    <span className="text-muted-foreground">
                      {new Date(b.mtime).toLocaleString()}
                    </span>
                    <span className="text-muted-foreground">{b.size_bytes} B</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${b.kind === "latest" ? "bg-muted" : "bg-muted/50"}`}>
                      {b.kind}
                    </span>
                    <button
                      type="button"
                      onClick={() => openPreview(b.name)}
                      className="flex items-center gap-1 rounded border border-border/70 px-2 py-0.5 text-[11px] hover:bg-muted"
                      title={t("composer.backupPreview", { defaultValue: "Preview" }) as string}
                    >
                      <Eye className="h-3 w-3" />
                      {t("composer.backupPreview", { defaultValue: "Preview" })}
                    </button>
                    <button
                      type="button"
                      onClick={() => setRestoreName(b.name)}
                      className="flex items-center gap-1 rounded border border-red-300 dark:border-red-900 px-2 py-0.5 text-[11px] text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950"
                      title={t("composer.backupRestore", { defaultValue: "Wiederherstellen" }) as string}
                    >
                      <RotateCcw className="h-3 w-3" />
                      {t("composer.backupRestore", { defaultValue: "Wiederherstellen" })}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {/* Preview-Modal: einfaches Overlay, kein Diff-Viewer */}
      {previewName !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setPreviewName(null)}>
          <div className="max-h-[85vh] w-full max-w-3xl overflow-auto rounded-md bg-background p-4 shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between">
              <div className="font-mono text-xs">{previewName}</div>
              <button type="button" onClick={() => setPreviewName(null)} className="text-xs text-muted-foreground hover:text-foreground">
                ✕
              </button>
            </div>
            {previewLoading ? (
              <div className="text-xs text-muted-foreground">
                {t("composer.backupsLoading", { defaultValue: "Lade…" })}
              </div>
            ) : (
              <pre className="whitespace-pre-wrap break-words text-xs">{previewContent}</pre>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={restoreName !== null}
        variant="danger"
        title={t("composer.restoreConfirmTitle", { defaultValue: "Backup wiederherstellen?" })}
        message={t("composer.restoreConfirmMsg", {
          defaultValue:
            "AGENT.md wird mit dem Inhalt des ausgewählten Backups überschrieben. Der aktuelle Stand wird automatisch als neues Backup gesichert. soul.md und agent.yaml bleiben unverändert.",
        })}
        confirmLabel={t("composer.restoreConfirmOk", { defaultValue: "Wiederherstellen" })}
        cancelLabel={t("composer.restoreConfirmCancel", { defaultValue: "Abbrechen" })}
        onCancel={() => setRestoreName(null)}
        onConfirm={doRestore}
      />

      {restoreBusy && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/20 text-xs text-white">
          {t("composer.restoreBusy", { defaultValue: "Wiederherstellen…" })}
        </div>
      )}
    </div>
  );
}
