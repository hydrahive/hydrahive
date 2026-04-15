import { useEffect, useMemo, useState } from "react";
import { Sparkles, Save, RefreshCw, CheckCircle, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface BlockDef { id: string; label: string; description: string }
interface CategoryDef { id: string; label: string; blocks: BlockDef[] }

export function ProfileComposer() {
  const { t } = useTranslation();
  const [categories, setCategories] = useState<CategoryDef[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<string>("");
  const [loadingBlocks, setLoadingBlocks] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.composerBlocks();
        setCategories(r.categories);
      } catch (e) {
        setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
      } finally {
        setLoadingBlocks(false);
      }
    })();
  }, []);

  const selectedCount = selected.size;
  const hasSelection = selectedCount > 0;

  function toggle(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setPreview("");
  }

  async function refreshPreview() {
    setPreviewing(true); setMsg(null);
    try {
      const r = await api.composerPreview(Array.from(selected));
      setPreview(r.markdown);
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
      const r = await api.composerSave(Array.from(selected));
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
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
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

  if (loadingBlocks) {
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
            "Wähle Bausteine, um eine AGENT.md-Datei für deinen persönlichen Agent zu erzeugen. AGENT.md beschreibt, wie der Agent arbeiten soll, und ersetzt keine bestehende Soul-Einstellung.",
        })}
      </p>

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
          disabled={!hasSelection || saving}
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
    </section>
  );
}
