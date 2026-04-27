import { useEffect, useState, useCallback } from "react";
import {
  Image, Film, Music2, Download, RefreshCw, Loader2,
  AlertTriangle, ImageOff, Filter,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, JobMeta, JobArtifact } from "@/lib/api";

// ─── Typen ───────────────────────────────────────────────────────────────────

type MediaType = "all" | "image_generation" | "video_generation" | "music_generation";

interface MediaItem {
  job:      JobMeta;
  artifact: JobArtifact;
}

// ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

function mimeCategory(mime: string): "image" | "video" | "audio" | "other" {
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "other";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString(); }
  catch { return iso; }
}

// ─── Kachel ──────────────────────────────────────────────────────────────────

function MediaCard({ item }: { item: MediaItem }) {
  const { artifact, job } = item;
  const cat = mimeCategory(artifact.mime);
  const { t } = useTranslation();

  return (
    <div className="group relative flex flex-col rounded-xl border border-border bg-card overflow-hidden shadow-sm hover:shadow-md transition-shadow">
      {/* Preview */}
      <div className="relative bg-muted flex items-center justify-center" style={{ minHeight: 180 }}>
        {cat === "image" && artifact.download_url ? (
          <img
            src={artifact.download_url}
            alt={artifact.filename}
            className="w-full h-44 object-cover"
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        ) : cat === "video" && artifact.download_url ? (
          <video
            src={artifact.download_url}
            controls
            className="w-full h-44 object-cover bg-black"
            preload="metadata"
          />
        ) : cat === "audio" && artifact.download_url ? (
          <div className="flex flex-col items-center gap-2 p-4 w-full">
            <Music2 className="w-10 h-10 text-muted-foreground" />
            <audio src={artifact.download_url} controls className="w-full mt-1" preload="none" />
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 p-6 text-muted-foreground">
            <ImageOff className="w-8 h-8" />
            <span className="text-xs">{t("media.noPreview", "Keine Vorschau")}</span>
          </div>
        )}
        <span className="absolute top-2 left-2 text-xs px-2 py-0.5 rounded-full bg-black/60 text-white font-medium capitalize">
          {cat}
        </span>
      </div>

      {/* Meta */}
      <div className="flex flex-col gap-1 p-3 text-sm flex-1">
        <p className="font-medium truncate text-foreground" title={artifact.filename}>
          {artifact.filename}
        </p>
        <p className="text-xs text-muted-foreground">
          {job.type} · {formatBytes(artifact.size)}
        </p>
        <p className="text-xs text-muted-foreground">{formatDate(artifact.created_at)}</p>
        {job.input_summary?.prompt && (
          <p className="text-xs text-muted-foreground line-clamp-2 mt-1 italic">
            „{String(job.input_summary.prompt)}"
          </p>
        )}
      </div>

      {/* Download */}
      <div className="px-3 pb-3">
        {artifact.download_url ? (
          <a
            href={artifact.download_url}
            download={artifact.filename}
            className="flex items-center gap-1.5 text-xs text-primary hover:underline"
          >
            <Download className="w-3.5 h-3.5" />
            {t("media.download", "Herunterladen")}
          </a>
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-not-allowed select-none">
            <Download className="w-3.5 h-3.5 opacity-40" />
            {t("media.noUrl", "Kein Download verfügbar")}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Haupt-Seite ─────────────────────────────────────────────────────────────

const MEDIA_TYPES: { value: MediaType; label: string; icon: React.ElementType }[] = [
  { value: "all",              label: "Alle",   icon: Filter },
  { value: "image_generation", label: "Bilder", icon: Image  },
  { value: "video_generation", label: "Videos", icon: Film   },
  { value: "music_generation", label: "Audio",  icon: Music2 },
];

export default function MediaGalleryPage() {
  const { t } = useTranslation();
  const [items,      setItems]      = useState<MediaItem[]>([]);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<MediaType>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const filter = typeFilter !== "all" ? { type: typeFilter } : undefined;
      const res = await api.jobsList(filter);
      const collected: MediaItem[] = [];
      for (const job of res.jobs) {
        const relevant = job.artifacts.filter(a => mimeCategory(a.mime) !== "other");
        for (const artifact of relevant) {
          collected.push({ job, artifact });
        }
      }
      collected.sort((a, b) =>
        new Date(b.artifact.created_at).getTime() - new Date(a.artifact.created_at).getTime()
      );
      setItems(collected);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [typeFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{t("nav.mediaGallery", "Media-Galerie")}</h1>
        <p className="text-xs text-muted-foreground">
          {t("pageDesc.mediaGallery", "Generierte Bilder, Videos und Audio-Dateien")}
        </p>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-border overflow-hidden text-sm">
          {MEDIA_TYPES.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setTypeFilter(value)}
              className={[
                "flex items-center gap-1.5 px-3 py-1.5 transition-colors",
                typeFilter === value
                  ? "bg-primary text-primary-foreground"
                  : "bg-background text-muted-foreground hover:bg-muted",
              ].join(" ")}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          {t("tools.refresh", "Aktualisieren")}
        </button>
      </div>

      {/* Fehler */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Laden */}
      {loading && items.length === 0 && (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />
          {t("common.loading", "Laden…")}
        </div>
      )}

      {/* Leer */}
      {!loading && !error && items.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <ImageOff className="w-10 h-10" />
          <p className="text-sm">{t("media.empty", "Noch keine Media-Artifacts vorhanden.")}</p>
          <p className="text-xs">{t("media.emptyHint", "Starte einen Image-, Video- oder Audio-Job um Ergebnisse zu sehen.")}</p>
        </div>
      )}

      {/* Grid */}
      {items.length > 0 && (
        <>
          <p className="text-xs text-muted-foreground">
            {items.length} {t("media.itemsFound", "Einträge")}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-4">
            {items.map((item, i) => (
              <MediaCard key={`${item.job.job_id}-${i}`} item={item} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
