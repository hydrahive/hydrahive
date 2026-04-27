import {
  AuiProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  type MessagePartState,
  type ThreadMessage,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import { Bot, Check, History, ImagePlus, Loader2, Network, Paperclip, RefreshCw, RotateCcw, Send, ShieldAlert, Square, User, Volume2, VolumeX, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import VoiceChatButton from "@/components/VoiceChatButton";
import { api } from "@/lib/api";
import {
  type ChatV2Target,
  type HydraHiveRuntime,
  type HydraHiveRuntimeOptions,
  type PendingImage,
  type PendingToolConfirm,
  useHydraHiveRuntime,
} from "./hydrahive-runtime";
import { CollabComposer } from "./CollabComposer";
import type { ProjectYjs } from "@/hooks/useProjectYjs";
import type { SessionPreview } from "@/lib/api";

type DataPart = Extract<MessagePartState, { type: "data" }>;

function partText(part: MessagePartState): string {
  if (part.type === "text") return part.text;
  if (part.type === "reasoning") return part.text;
  return "";
}

type MessageLike = ThreadMessage & { metadata?: { custom?: Record<string, unknown> }; status?: { type?: string } };

function TokenBadge({ message }: { message: MessageLike }) {
  const custom = (message.metadata?.custom ?? {}) as {
    tokenUsage?: { input?: number; output?: number; cache_read?: number; cache_write?: number; rounds?: number };
    model?: string;
  };
  const usage = custom.tokenUsage;
  if (message.role !== "assistant" || !usage || message.status?.type === "running") return null;
  const input = usage.input ?? 0;
  const output = usage.output ?? 0;
  const cacheRead = usage.cache_read ?? 0;
  const cacheWrite = usage.cache_write ?? 0;
  // Claude-API Semantik: input zählt nur neue, ungecachte Tokens; cache_read/
  // cache_write sind getrennt. Effektiver Input = Summe, cache-Rate darauf.
  const effectiveInput = input + cacheRead + cacheWrite;
  const cachePct = effectiveInput > 0 ? Math.min(100, Math.round((cacheRead / effectiveInput) * 100)) : 0;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground font-mono tabular-nums">
      <span>↑ {effectiveInput}</span>
      <span>↓ {output}</span>
      <span className={cn(cachePct > 0 ? "text-emerald-400" : "text-amber-400")}>◆ {cachePct}% cached</span>
      {usage.rounds ? <span>· {usage.rounds} Runden</span> : null}
      {custom.model ? <span>· {custom.model}</span> : null}
    </div>
  );
}

function ToolDataPart({ part }: { part: DataPart }) {
  const data = part.data as Record<string, unknown>;
  if (part.name === "context_info" || part.name === "info") {
    return (
      <div className="badge-candy-info rounded-lg border px-3 py-2 text-xs">
        {part.name === "info" ? String(data.info ?? "") : `Context: ${data.history_tokens ?? 0} History · ${data.tool_tokens ?? 0} Tools`}
      </div>
    );
  }
  if (part.name === "tool_image") {
    const src = String(data.tool_image ?? "");
    const toolName = String(data.tool_name ?? "tool-image");
    // #791: Download-Link fuer generierte Bilder. Data-URIs werden korrekt
    // angezeigt; HTTP-URLs bleiben als Fallback (ziehen sich Cookies via
    // user-click statt bei passivem <img>-Load).
    const downloadName = toolName.replace(/[^a-z0-9._-]+/gi, "_") + (src.includes("image/png") ? ".png" : src.includes("image/jpeg") ? ".jpg" : ".img");
    return (
      <div className="rounded-xl border border-border/60 bg-card/70 p-2 space-y-1">
        {src ? (
          <>
            <img src={src} alt={toolName} className="max-h-72 rounded-lg object-contain" />
            <a
              href={src}
              download={downloadName}
              className="block text-[11px] text-muted-foreground hover:text-foreground hover:underline"
              title="Bild speichern"
            >
              ⬇ {downloadName}
            </a>
          </>
        ) : null}
      </div>
    );
  }
  if (part.name === "tool_audio") {
    // #803: Audio-Artifact-Renderer. Signed URLs aus #802 Phase 3a erlauben
    // cookie-less <audio src=...>-Laden, data-URIs sind ebenfalls möglich.
    const src = String(data.tool_audio ?? "");
    const toolName = String(data.tool_name ?? "tool-audio");
    const ext = src.includes("audio/mpeg") || src.includes(".mp3") ? ".mp3"
      : src.includes("audio/wav") ? ".wav"
      : src.includes("audio/ogg") ? ".ogg"
      : ".audio";
    const downloadName = toolName.replace(/[^a-z0-9._-]+/gi, "_") + ext;
    return (
      <div className="rounded-xl border border-border/60 bg-card/70 p-2 space-y-1">
        {src ? (
          <>
            <audio src={src} controls preload="metadata" className="w-full max-w-md" />
            <a
              href={src}
              download={downloadName}
              className="block text-[11px] text-muted-foreground hover:text-foreground hover:underline"
              title="Audio speichern"
            >
              ⬇ {downloadName}
            </a>
          </>
        ) : null}
      </div>
    );
  }
  if (part.name === "tool_video") {
    // #803: Video-Artifact-Renderer. Signed URLs aus #802 Phase 3a.
    const src = String(data.tool_video ?? "");
    const toolName = String(data.tool_name ?? "tool-video");
    const ext = src.includes("video/mp4") || src.includes(".mp4") ? ".mp4"
      : src.includes("video/webm") ? ".webm"
      : ".video";
    const downloadName = toolName.replace(/[^a-z0-9._-]+/gi, "_") + ext;
    return (
      <div className="rounded-xl border border-border/60 bg-card/70 p-2 space-y-1">
        {src ? (
          <>
            <video src={src} controls preload="metadata" className="max-h-80 w-full max-w-lg rounded-lg" />
            <a
              href={src}
              download={downloadName}
              className="block text-[11px] text-muted-foreground hover:text-foreground hover:underline"
              title="Video speichern"
            >
              ⬇ {downloadName}
            </a>
          </>
        ) : null}
      </div>
    );
  }
  if (part.name === "tool_warning") {
    return (
      <div className="badge-candy-warn rounded-lg border px-3 py-2 text-sm">
        {String(data.tool_name ?? "tool")}: {String(data.tool_warning ?? "Warnung")}
      </div>
    );
  }
  if (part.name === "tool_confirm_required") {
    return (
      <div className="badge-candy-confirm rounded-lg border px-3 py-2 text-sm">
        Bestätigung nötig: {String(data.tool_name ?? "tool")} wartet unten im Composer-Bereich.
      </div>
    );
  }

  // ── #888: dispatch_task_dag — hübsche Darstellung ───────────────────────
  if (part.name === "tool_call" && data.tool_call === "dispatch_task_dag") {
    let tasks: {id: string; agent: string; question: string}[] = [];
    try {
      const raw = typeof data.tool_input === "string" ? JSON.parse(data.tool_input) : data.tool_input;
      tasks = raw.tasks ?? [];
    } catch { tasks = []; }
    return (
      <div className="rounded-xl border border-indigo-500/20 bg-indigo-950/20 px-4 py-3 text-sm">
        <div className="flex items-center gap-2 mb-2">
          <Network className="h-4 w-4 text-indigo-400 shrink-0" />
          <span className="font-semibold text-indigo-300">Delegiere an Spezialisten</span>
          <span className="text-xs text-indigo-400/60">{tasks.length} Tasks</span>
        </div>
        <div className="space-y-1.5">
          {tasks.map((t: {id: string; agent: string; question: string}) => (
            <div key={t.id} className="flex items-start gap-2 rounded-lg bg-zinc-800/60 px-3 py-1.5">
              <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-mono text-indigo-300 shrink-0">{t.agent}</span>
              <span className="text-xs text-white/70 truncate">{t.question.slice(0, 80)}{t.question.length > 80 ? "…" : ""}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (part.name === "tool_result") {
    // Versuche dispatch_task_dag Ergebnis zu erkennen
    let dagResult: {results?: Record<string, string>; summary?: string; failed_tasks?: string[]} | null = null;
    try {
      const rawStr = typeof data.tool_result === "string" ? JSON.parse(data.tool_result) : data.tool_result;
      if (rawStr && typeof rawStr === "object" && "results" in rawStr && "summary" in rawStr) {
        dagResult = rawStr as {results?: Record<string, string>; summary?: string; failed_tasks?: string[]};
      }
    } catch { /* kein JSON */ }
    if (dagResult) {
      const summaryText = dagResult.summary ?? "";
      const summaryLines = summaryText.split("\n").filter(Boolean);
      return (
        <div className="rounded-xl border border-indigo-500/20 bg-indigo-950/20 px-4 py-3 text-sm">
          <div className="flex items-center gap-2 mb-2">
            <Network className="h-4 w-4 text-indigo-400 shrink-0" />
            <span className="font-semibold text-indigo-300">Spezialistent-Ergebnisse</span>
          </div>
          <div className="space-y-1">
            {summaryLines.map((line: string, i: number) => {
              const isOk = line.startsWith("[OK]");
              const isFail = line.startsWith("[FEHLER]");
              return (
                <details key={i} className="rounded-lg border border-white/5 bg-zinc-800/40">
                  <summary className={cn("cursor-pointer px-3 py-1.5 text-xs", isOk ? "text-emerald-400" : isFail ? "text-red-400" : "text-white/60")}>
                    {line.slice(0, 120)}
                  </summary>
                  {dagResult.results && (
                    <div className="px-3 pb-2 text-xs text-white/50">
                      {Object.entries(dagResult.results).map(([tid, txt]) => (
                        <div key={tid} className="mt-1">
                          <span className="font-mono text-indigo-300">{tid}:</span>{" "}
                          {String(txt).slice(0, 300)}{String(txt).length > 300 ? "…" : ""}
                        </div>
                      ))}
                    </div>
                  )}
                </details>
              );
            })}
          </div>
        </div>
      );
    }
  }

  const title = part.name === "tool_call"
    ? `Tool: ${String(data.tool_call ?? "unknown")}`
    : part.name === "tool_result"
      ? "Tool-Ergebnis"
      : part.name === "tool_confirm_required"
        ? `Bestätigung nötig: ${String(data.tool_name ?? "tool")}`
        : part.name;
  const badgeClass = part.name === "tool_call" ? "badge-candy-tool" : "badge-candy-result";
  return (
    <details className={cn("rounded-lg border px-3 py-2 text-sm", badgeClass)}>
      <summary className="cursor-pointer">{title}</summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-foreground/70">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

function MessagePart({ part }: { part: MessagePartState }) {
  if (part.type === "text" || part.type === "reasoning") {
    const text = partText(part);
    return (
      <div className="prose prose-sm max-w-none dark:prose-invert prose-pre:bg-muted prose-pre:text-foreground">
        <ReactMarkdown>{text}</ReactMarkdown>
      </div>
    );
  }
  if (part.type === "data") return <ToolDataPart part={part as DataPart} />;
  if (part.type === "image") {
    return <img src={part.image} alt={part.filename ?? "image"} className="max-h-72 rounded-lg object-contain" />;
  }
  return null;
}

// #734 O: ein aktiver TTS-Stream zur Zeit. "activeId" ist unabhängig vom
// Error-State gesetzt, solange das Audio-Element im DOM existiert — damit
// es immer einen Stop-Button gibt (Till-Bug: Retry angezeigt während
// Audio weiterlief).
type TtsState = {
  activeId: string | null;
  playingId: string | null;
  loadingId: string | null;
  errorId: string | null;
  speak: (messageId: string, text: string) => void;
  stop: () => void;
};

function useTtsPlayback(): TtsState {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [errorId, setErrorId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const hardStop = useCallback(() => {
    if (audioRef.current) {
      try { audioRef.current.pause(); } catch { /* ignore */ }
      try { audioRef.current.removeAttribute("src"); audioRef.current.load(); } catch { /* ignore */ }
    }
    if (urlRef.current) {
      try { URL.revokeObjectURL(urlRef.current); } catch { /* ignore */ }
      urlRef.current = null;
    }
    audioRef.current = null;
  }, []);

  const stop = useCallback(() => {
    hardStop();
    setActiveId(null);
    setPlayingId(null);
    setLoadingId(null);
  }, [hardStop]);

  const speak = useCallback(async (messageId: string, text: string) => {
    hardStop();
    setErrorId(null);
    if (!text.trim()) return;
    setActiveId(messageId);
    setLoadingId(messageId);
    setPlayingId(null);
    let blob: Blob;
    try {
      blob = await api.voiceTts(text);
    } catch {
      // TTS-API selbst tot — kein Audio erzeugt, Stop macht keinen Sinn.
      setActiveId(null);
      setLoadingId(null);
      setErrorId(messageId);
      return;
    }
    const url = URL.createObjectURL(blob);
    urlRef.current = url;
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.addEventListener("playing", () => {
      setPlayingId(messageId);
      setLoadingId(null);
    });
    audio.addEventListener("pause", () => {
      if (audio.ended) return;
      setPlayingId((id) => (id === messageId ? null : id));
    });
    audio.addEventListener("ended", () => {
      setActiveId((id) => (id === messageId ? null : id));
      setPlayingId((id) => (id === messageId ? null : id));
      setLoadingId(null);
      if (urlRef.current === url) {
        try { URL.revokeObjectURL(url); } catch { /* ignore */ }
        urlRef.current = null;
      }
      if (audioRef.current === audio) audioRef.current = null;
    });
    audio.addEventListener("error", () => {
      // Audio hart stoppen falls Browser trotz error-Event weiterspielt.
      try { audio.pause(); } catch { /* ignore */ }
      setActiveId((id) => (id === messageId ? null : id));
      setPlayingId(null);
      setLoadingId(null);
      setErrorId(messageId);
      if (urlRef.current === url) {
        try { URL.revokeObjectURL(url); } catch { /* ignore */ }
        urlRef.current = null;
      }
      if (audioRef.current === audio) audioRef.current = null;
    });
    try {
      await audio.play();
    } catch {
      // play() kann in einigen Browsern rejecten (z.B. NotSupportedError) während
      // der Stream trotzdem läuft. activeId bleibt deshalb gesetzt — den State
      // räumt das 'ended'/'error'-Event auf. UI zeigt dadurch Stop statt Retry.
    }
  }, [hardStop]);

  useEffect(() => () => hardStop(), [hardStop]);

  return { activeId, playingId, loadingId, errorId, speak, stop };
}

function messageSpeakableText(message: MessageLike): string {
  return message.content
    .filter((p: unknown) => (p as { type?: string }).type === "text")
    .map((p) => (p as { type: "text"; text: string }).text)
    .join(" ")
    .trim();
}

function SpeakButton({ message, tts }: { message: MessageLike; tts: TtsState }) {
  if (message.role !== "assistant") return null;
  if (message.status?.type === "running") return null;
  const text = messageSpeakableText(message);
  if (!text) return null;
  const isActive = tts.activeId === message.id;     // Audio-Element existiert noch
  const isLoading = tts.loadingId === message.id;
  const isPlaying = tts.playingId === message.id;
  const hasError = !isActive && tts.errorId === message.id;
  // Priorität: aktive Wiedergabe → Stop (überschreibt error), sonst error → Retry, sonst Speak.
  const mode = isActive ? "stop" : hasError ? "retry" : "idle";
  const label = mode === "stop" ? "TTS stoppen" : mode === "retry" ? "TTS fehlgeschlagen — erneut versuchen" : "Vorlesen";
  const onClick = () => {
    if (mode === "stop") tts.stop();
    else tts.speak(message.id, text);
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "mt-2 inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] transition",
        mode === "stop"
          ? "border-[hsl(var(--candy-violet)/0.6)] bg-[hsl(var(--candy-violet)/0.12)] text-[hsl(var(--candy-violet))] hover:bg-[hsl(var(--candy-violet)/0.2)]"
          : mode === "retry"
            ? "border-[hsl(var(--candy-pink)/0.5)] text-[hsl(var(--candy-pink))] hover:bg-[hsl(var(--candy-pink)/0.08)]"
            : "border-[hsl(var(--candy-cyan)/0.4)] text-[hsl(var(--candy-cyan))] hover:bg-[hsl(var(--candy-cyan)/0.08)]"
      )}
      title={label}
      aria-label={label}
    >
      {isLoading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : mode === "stop" ? (
        <Square className="h-3.5 w-3.5" />
      ) : mode === "retry" ? (
        <VolumeX className="h-3.5 w-3.5" />
      ) : (
        <Volume2 className="h-3.5 w-3.5" />
      )}
      <span className="font-medium">
        {mode === "stop" ? (isPlaying ? "Stop" : "Lädt…") : mode === "retry" ? "Retry" : "Speak"}
      </span>
    </button>
  );
}

function ChatMessage({ message, tts }: { message: MessageLike; tts: TtsState }) {
  const isUser = message.role === "user";
  return (
    <MessagePrimitive.Root className={cn("flex gap-3 px-4 py-4", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[hsl(var(--candy-cyan)/0.35)] to-[hsl(var(--candy-violet)/0.25)] text-primary shadow-[0_0_12px_hsl(var(--candy-cyan)/0.3)]">
          <Bot className="h-4 w-4" />
        </div>
      )}
      <div className={cn(
        "max-w-[min(860px,92vw)] rounded-2xl border px-4 py-3 shadow-sm",
        isUser
          ? "bubble-candy-user"
          : "bubble-candy-assistant bg-card/95 text-card-foreground"
      )}>
        <MessagePrimitive.Parts>
          {({ part }) => <MessagePart part={part as MessagePartState} />}
        </MessagePrimitive.Parts>
        {message.status?.type === "running" && (
          <div className="mt-2 inline-flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            streamt
          </div>
        )}
        <TokenBadge message={message} />
        <SpeakButton message={message} tts={tts} />
      </div>
      {isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[hsl(var(--candy-pink)/0.35)] to-[hsl(var(--candy-amber)/0.25)] text-[hsl(var(--candy-pink))] shadow-[0_0_12px_hsl(var(--candy-pink)/0.3)]">
          <User className="h-4 w-4" />
        </div>
      )}
    </MessagePrimitive.Root>
  );
}

function formatSessionDate(value: string) {
  try {
    return new Date(value).toLocaleString("de-DE");
  } catch {
    return value;
  }
}

function HistoryPanel({
  sessions,
  loading,
  activeSessionId,
  onOpen,
  onResume,
  onClose,
}: {
  sessions: SessionPreview[];
  loading: boolean;
  activeSessionId: string | null;
  onOpen: (sessionId: string) => void;
  onResume: (sessionId: string) => void;
  onClose: () => void;
}) {
  return (
    <aside className="w-full border-b border-border bg-muted/20 lg:w-80 lg:border-b-0 lg:border-r">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Sessions</div>
          <div className="text-sm text-foreground">Verlauf</div>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg p-2 text-muted-foreground hover:bg-muted">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="max-h-72 overflow-y-auto lg:max-h-none">
        {loading ? (
          <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            lade Sessions...
          </div>
        ) : sessions.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">Keine vergangenen Sessions.</p>
        ) : (
          <div className="divide-y divide-border">
            {sessions.map((session) => (
              <div key={session.id} className={cn("flex items-stretch", session.id === activeSessionId && "bg-primary/5")}>
                <button
                  type="button"
                  onClick={() => onOpen(session.id)}
                  className="min-w-0 flex-1 px-4 py-3 text-left hover:bg-muted/70"
                >
                  <div className="truncate text-sm font-medium text-foreground">{session.preview || "(leer)"}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {session.message_count} Messages · {formatSessionDate(session.started_at)}
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => onResume(session.id)}
                  title="Session fortsetzen"
                  className="border-l border-border px-3 text-primary hover:bg-primary/10"
                >
                  <RotateCcw className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function previewToolInput(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  const text = JSON.stringify(input, null, 2);
  return text.length > 700 ? `${text.slice(0, 700)}…` : text;
}

function ConfirmBanner({
  item,
  disabled,
  onConfirm,
}: {
  item: PendingToolConfirm;
  disabled: boolean;
  onConfirm: (toolCallId: string, decision: "approve" | "deny") => void;
}) {
  const input = previewToolInput(item.tool_input);
  return (
    <div className="rounded-2xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-200">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            Tool wartet auf Bestätigung
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-mono text-foreground">{item.tool_name}</span>
            {" "}wurde als riskant eingestuft ({item.risk || "confirm"}).
          </p>
          {input ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                Tool-Argumente anzeigen
              </summary>
              <pre className="mt-2 max-h-44 overflow-auto rounded-xl bg-background/70 p-3 text-[11px] text-muted-foreground">
                {input}
              </pre>
            </details>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onConfirm(item.tool_call_id, "approve")}
            className="inline-flex items-center gap-1 rounded-xl bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            Erlauben
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onConfirm(item.tool_call_id, "deny")}
            className="inline-flex items-center gap-1 rounded-xl border border-destructive/50 px-3 py-2 text-xs font-semibold text-destructive transition hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" />
            Ablehnen
          </button>
        </div>
      </div>
    </div>
  );
}

export function CoachFeedbackCard({
  reason,
  suggestion,
  onSendAnyway,
  onUseSuggestion,
  onDismiss,
}: {
  reason?: string;
  suggestion?: string;
  onSendAnyway: () => void;
  onUseSuggestion: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-2xl border border-orange-500/30 bg-orange-500/10 px-4 py-3 text-sm">
      <p className="font-semibold text-orange-300">{reason || "Prompt-Coach empfiehlt eine Anpassung."}</p>
      {suggestion ? <p className="mt-1 text-muted-foreground">{suggestion}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {suggestion ? (
          <button
            type="button"
            onClick={onUseSuggestion}
            className="rounded-xl bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground transition hover:bg-primary/90"
          >
            Vorschlag übernehmen
          </button>
        ) : null}
        <button
          type="button"
          onClick={onSendAnyway}
          className="rounded-xl border border-border px-3 py-2 text-xs font-semibold text-foreground transition hover:bg-muted"
        >
          Trotzdem senden
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-xl px-3 py-2 text-xs font-semibold text-muted-foreground transition hover:bg-muted"
        >
          Abbrechen
        </button>
      </div>
    </div>
  );
}

// #730 I (Avatar-Teil): deterministische Candy-Hue aus User-String.
// Kein echter Hash — wir brauchen nur eine stabile Zuweisung, keine Krypto.
const PRESENCE_HUES = ["--candy-violet", "--candy-pink", "--candy-cyan", "--candy-lime", "--candy-amber"] as const;

function presenceHue(user: string): string {
  let h = 0;
  for (let i = 0; i < user.length; i++) h = (h * 31 + user.charCodeAt(i)) >>> 0;
  return PRESENCE_HUES[h % PRESENCE_HUES.length];
}

function PresenceAvatar({ user }: { user: string }) {
  const hue = presenceHue(user);
  const initial = user.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      title={user}
      className="inline-flex h-6 w-6 items-center justify-center rounded-full border text-[10px] font-semibold"
      style={{
        backgroundColor: `hsl(var(${hue}) / 0.2)`,
        color: `hsl(var(${hue}))`,
        borderColor: `hsl(var(${hue}) / 0.5)`,
      }}
    >
      {initial}
    </span>
  );
}

function Composer({
  isRunning,
  pendingConfirms,
  confirmingIds,
  pendingImages,
  followUpChips,
  coachEnabled,
  coachChecking,
  coachFeedback,
  typingUsers,
  presenceUsers,
  onAddImages,
  onRemoveImage,
  onUseSuggestion,
  onToggleCoach,
  onSendAnyway,
  onUseCoachSuggestion,
  onDismissCoach,
  onTranscript,
  onConfirm,
  onComposerActivity,
  onUploadFile,
}: {
  isRunning: boolean;
  pendingConfirms: PendingToolConfirm[];
  confirmingIds: Set<string>;
  pendingImages: PendingImage[];
  followUpChips: string[];
  coachEnabled: boolean;
  coachChecking: boolean;
  coachFeedback: { reason?: string; suggestion?: string } | null;
  typingUsers?: string[];
  presenceUsers?: string[];
  onAddImages: (files: FileList | File[]) => void;
  onRemoveImage: (index: number) => void;
  onUseSuggestion: (text: string) => void;
  onToggleCoach: (enabled: boolean) => void;
  onSendAnyway: () => void;
  onUseCoachSuggestion: () => void;
  onDismissCoach: () => void;
  onTranscript: (text: string) => void;
  onConfirm: (toolCallId: string, decision: "approve" | "deny") => void;
  onComposerActivity?: (hasText: boolean) => void;
  onUploadFile?: (file: File) => Promise<void>;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const hasText = useAuiState((s: any) => Boolean(s?.thread?.composer?.text?.trim?.()));
  const lastActivityRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (!onComposerActivity) return;
    if (lastActivityRef.current === hasText) return;
    lastActivityRef.current = hasText;
    onComposerActivity(hasText);
  }, [hasText, onComposerActivity]);
  return (
    <ThreadPrimitive.ViewportFooter className="sticky bottom-0 border-t border-border/70 bg-background/95 px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur">
      {presenceUsers && presenceUsers.length > 1 ? (
        <div className="mx-auto mb-2 flex max-w-4xl flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="uppercase tracking-[0.18em]">online</span>
          {presenceUsers.map((u) => (
            <PresenceAvatar key={u} user={u} />
          ))}
        </div>
      ) : null}
      {typingUsers && typingUsers.length > 0 ? (
        <div className="mx-auto mb-2 max-w-4xl text-xs text-muted-foreground italic">
          {typingUsers.length === 1
            ? `${typingUsers[0]} tippt …`
            : `${typingUsers.join(", ")} tippen …`}
        </div>
      ) : null}
      {pendingConfirms.length > 0 ? (
        <div className="mx-auto mb-3 max-w-4xl space-y-2">
          {pendingConfirms.map((item) => (
            <ConfirmBanner
              key={item.tool_call_id}
              item={item}
              disabled={confirmingIds.has(item.tool_call_id)}
              onConfirm={onConfirm}
            />
          ))}
        </div>
      ) : null}
      {coachFeedback ? (
        <div className="mx-auto mb-3 max-w-4xl">
          <CoachFeedbackCard
            reason={coachFeedback.reason}
            suggestion={coachFeedback.suggestion}
            onSendAnyway={onSendAnyway}
            onUseSuggestion={onUseCoachSuggestion}
            onDismiss={onDismissCoach}
          />
        </div>
      ) : null}
      {followUpChips.length > 0 && !isRunning ? (
        <div className="mx-auto mb-3 flex max-w-4xl flex-wrap gap-2">
          {followUpChips.map((suggestion, index) => (
            <button
              key={`${suggestion}-${index}`}
              type="button"
              onClick={() => onUseSuggestion(suggestion)}
              className="chip-candy rounded-full px-3 py-1.5 text-xs font-medium"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}
      {pendingImages.length > 0 ? (
        <div className="mx-auto mb-3 flex max-w-4xl flex-wrap gap-2">
          {pendingImages.map((image, index) => (
            <div key={`${image.preview}-${index}`} className="group relative">
              <img
                src={image.preview}
                alt=""
                className="h-16 w-16 rounded-xl border border-border object-cover shadow-sm"
              />
              <button
                type="button"
                onClick={() => onRemoveImage(index)}
                className="absolute -right-2 -top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow transition hover:bg-destructive hover:text-destructive-foreground"
                aria-label="Bild entfernen"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mx-auto mb-2 flex max-w-4xl items-center gap-2">
        <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={coachEnabled}
            onChange={(event) => onToggleCoach(event.target.checked)}
            className="h-3 w-3 rounded"
          />
          Prompt-Coach
          {coachChecking ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
        </label>
      </div>
      <ComposerPrimitive.Root className="mx-auto flex max-w-4xl items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-lg">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => {
            const files = event.target.files;
            if (files) onAddImages(files);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={isRunning || pendingImages.length >= 5}
          onClick={() => fileInputRef.current?.click()}
          className="btn-glass inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-muted-foreground transition disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Bild hochladen"
        >
          <ImagePlus className={cn("h-4 w-4", pendingImages.length > 0 && "text-primary")} />
        </button>
        <ComposerPrimitive.Input
          rows={1}
          submitMode="enter"
          placeholder="Nachricht schreiben..."
          className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted-foreground sm:text-sm"
        />
        <VoiceChatButton
          onTranscript={onTranscript}
          disabled={isRunning}
          className="!h-10 !w-10 !rounded-xl !border-border !p-0"
        />
        <ComposerPrimitive.Cancel
          className={cn(
            "btn-glass inline-flex h-10 w-10 items-center justify-center rounded-xl",
            !isRunning && "hidden"
          )}
        >
          <Square className="h-4 w-4" />
        </ComposerPrimitive.Cancel>
        <label className="btn-glass inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-xl">
          <input
            type="file"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file || !onUploadFile) return;
              try {
                await onUploadFile(file);
              } catch (err) {
                console.error('Upload failed:', err);
              }
              e.target.value = '';
            }}
          />
          <Paperclip className="h-4 w-4" />
        </label>
        <ComposerPrimitive.Send className="btn-candy inline-flex h-10 w-10 items-center justify-center rounded-xl">
          <Send className="h-4 w-4" />
        </ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.ViewportFooter>
  );
}

export type ChatShellProps = {
  target?: ChatV2Target;
  runtime?: HydraHiveRuntime;
  runtimeOptions?: HydraHiveRuntimeOptions;
  hideHeader?: boolean;
  headerLabel?: string;
  typingUsers?: string[];
  presenceUsers?: string[];
  onComposerActivity?: (hasText: boolean) => void;
  /** #554: wenn gesetzt, wird der Composer durch einen gemeinsamen Yjs-
   * Composer ersetzt. Der Hook useProjectYjs lebt im Page-Layer damit die
   * Page auch selbst in den Y.Text schreiben kann (z.B. Slash-Chips). */
  collab?: ProjectYjs | null;
};

export function ChatShell(props: ChatShellProps) {
  if (props.runtime) {
    return <ChatShellInner {...props} runtime={props.runtime} />;
  }
  if (!props.target) {
    throw new Error("ChatShell requires either `target` or `runtime`.");
  }
  return <ChatShellWithTarget {...props} target={props.target} />;
}

function ChatShellWithTarget({ target, runtimeOptions, ...rest }: ChatShellProps & { target: ChatV2Target }) {
  const runtime = useHydraHiveRuntime(target, runtimeOptions);
  return <ChatShellInner {...rest} target={target} runtime={runtime} />;
}

function ChatShellInner({ runtime, hideHeader, headerLabel, target, typingUsers, presenceUsers, onComposerActivity, collab }: ChatShellProps & { runtime: HydraHiveRuntime }) {
  const label = headerLabel ?? target?.label ?? "";
  const tts = useTtsPlayback();
  const yjs = collab ?? null;
  const appendTranscript = (text: string) => {
    const composer = runtime.aui.composer();
    const current = composer.getState().text.trim();
    composer.setText(current ? `${current} ${text}` : text);
  };
  const useSuggestion = (text: string) => {
    runtime.aui.composer().setText(text);
    runtime.clearFollowUpChips();
  };
  const useCoachSuggestion = () => {
    const suggestion = runtime.coachFeedback?.suggestion;
    if (!suggestion) return;
    runtime.aui.composer().setText(suggestion);
    runtime.clearCoachFeedback();
  };
  const sendAnyway = () => {
    const composer = runtime.aui.composer();
    const content = composer.getState().text || runtime.coachFeedback?.content || "";
    runtime.clearCoachFeedback();
    composer.setText("");
    void runtime.sendText(content, true);
  };
  useEffect(() => {
    if (!runtime.coachFeedback?.content) return;
    runtime.aui.composer().setText(runtime.coachFeedback.content);
  }, [runtime.aui, runtime.coachFeedback?.content]);

  return (
    <AuiProvider value={runtime.aui}>
      <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col bg-background">
        {hideHeader ? null : (
          <div className="border-b border-border/70 bg-background/95 px-4 py-3 backdrop-blur">
            <div className="mx-auto flex max-w-5xl items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Chat v2 Demo</div>
                <h1 className="text-lg font-semibold text-foreground">{label}</h1>
              </div>
              <div className="flex items-center gap-2">
                {runtime.error ? (
                  <div className="flex items-center gap-2">
                    <div className="rounded-full bg-destructive/10 px-3 py-1 text-xs text-destructive">{runtime.error}</div>
                    <button
                      type="button"
                      onClick={() => void runtime.reloadHistory()}
                      className="rounded-full bg-destructive/20 px-2 py-1 text-xs text-destructive hover:bg-destructive/30 transition-colors"
                      title="Session neu laden"
                    >
                      <RefreshCw className="h-3 w-3" />
                    </button>
                  </div>
                ) : null}
                <button
                  type="button"
                  onClick={runtime.toggleHistory}
                  className={cn(
                    "inline-flex h-9 w-9 items-center justify-center rounded-xl border border-border text-muted-foreground transition hover:bg-muted",
                    runtime.showHistory && "bg-muted text-foreground"
                  )}
                  title="Chat-Verlauf"
                >
                  <History className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          {runtime.showHistory ? (
            <HistoryPanel
              sessions={runtime.sessions}
              loading={runtime.historyLoading}
              activeSessionId={runtime.sessionId}
              onOpen={(sessionId) => void runtime.openSession(sessionId)}
              onResume={(sessionId) => void runtime.resumeSession(sessionId)}
              onClose={runtime.closeHistory}
            />
          ) : null}
          {runtime.viewSession ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex items-center justify-between border-b border-border bg-muted/20 px-4 py-2">
                <button type="button" onClick={runtime.closeSessionView} className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted">
                  Zurück
                </button>
                <span className="truncate px-3 text-xs text-muted-foreground">
                  Session vom {formatSessionDate(runtime.viewSession.startedAt)}
                </span>
                <button
                  type="button"
                  onClick={() => void runtime.resumeSession(runtime.viewSession!.id)}
                  className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
                >
                  Fortsetzen
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-5xl py-3">
                  {runtime.viewSession.messages.map((message) => (
                    <div key={message.id} className={cn("flex gap-3 px-4 py-4", message.role === "user" ? "justify-end" : "justify-start")}>
                        {message.role !== "user" ? (
                          <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                            <Bot className="h-4 w-4" />
                          </div>
                        ) : null}
                        <div className={cn(
                          "max-w-[min(860px,92vw)] rounded-2xl border px-4 py-3 shadow-sm",
                          message.role === "user"
                            ? "border-primary/20 bg-primary text-primary-foreground"
                            : "border-border/70 bg-card/95 text-card-foreground"
                        )}>
                          {message.content.map((part, index) => part.type === "text" ? (
                            <div key={index} className="prose prose-sm max-w-none dark:prose-invert">
                              <ReactMarkdown>{part.text}</ReactMarkdown>
                            </div>
                          ) : null)}
                        </div>
                        {message.role === "user" ? (
                          <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                            <User className="h-4 w-4" />
                          </div>
                        ) : null}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <ThreadPrimitive.Viewport
              autoScroll
              turnAnchor="bottom"
              className="min-h-0 flex-1 overflow-y-auto"
            >
              <ThreadPrimitive.Empty>
                <div className="mx-auto flex max-w-xl flex-col items-center justify-center px-6 py-24 text-center text-muted-foreground">
                  <div className="mb-4 rounded-2xl border border-border bg-card p-4 text-foreground shadow-sm">
                    <Bot className="h-8 w-8" />
                  </div>
                  <h2 className="text-xl font-semibold text-foreground">Neuer Chat-Unterbau</h2>
                  <p className="mt-2 text-sm">
                    Diese Demo nutzt assistant-ui-Primitives und den HydraHive-SSE-Adapter. Alt-Chat bleibt parallel aktiv.
                  </p>
                </div>
              </ThreadPrimitive.Empty>
              <div className="mx-auto w-full max-w-5xl py-3">
                <ThreadPrimitive.Messages>
                  {({ message }) => <ChatMessage message={message as MessageLike} tts={tts} />}
                </ThreadPrimitive.Messages>
              </div>
              {yjs ? (
                // #554: Collab-Modus — Y.Text ersetzt den Standard-Composer.
                // Coach / Voice / Images / Follow-Up-Chips fallen hier weg
                // (kommen in H13 zurück). Pending-Confirms + Typing-Banner
                // bleiben sichtbar über den normalen Pfad.
                <ThreadPrimitive.ViewportFooter className="sticky bottom-0 border-t border-border/70 bg-background/95 px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur">
                  {presenceUsers && presenceUsers.length > 1 ? (
                    <div className="mx-auto mb-2 flex max-w-4xl flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span className="uppercase tracking-[0.18em]">online</span>
                      {presenceUsers.map((u) => (
                        <PresenceAvatar key={u} user={u} />
                      ))}
                    </div>
                  ) : null}
                  {typingUsers && typingUsers.length > 0 ? (
                    <div className="mx-auto mb-2 max-w-4xl text-xs text-muted-foreground italic">
                      {typingUsers.length === 1
                        ? `${typingUsers[0]} tippt …`
                        : `${typingUsers.join(", ")} tippen …`}
                    </div>
                  ) : null}
                  {/* Bug-Fix: im Collab-Composer wurden pendingConfirms nicht gerendert
                      → kritische Tool-Bestätigungen (shell_exec, git_push …) kamen
                      nicht hoch im Projektchat. Banner identisch zum normalen Composer. */}
                  {runtime.pendingConfirms.length > 0 ? (
                    <div className="mx-auto mb-3 max-w-4xl space-y-2">
                      {runtime.pendingConfirms.map((item) => (
                        <ConfirmBanner
                          key={item.tool_call_id}
                          item={item}
                          disabled={runtime.confirmingIds.has(item.tool_call_id)}
                          onConfirm={runtime.confirmTool}
                        />
                      ))}
                    </div>
                  ) : null}
                  <CollabComposer yjs={yjs} runtime={runtime} projectId={target?.kind === "project" ? target.id : undefined} />
                </ThreadPrimitive.ViewportFooter>
              ) : (
                <Composer
                  isRunning={runtime.isRunning}
                  pendingConfirms={runtime.pendingConfirms}
                  confirmingIds={runtime.confirmingIds}
                  pendingImages={runtime.pendingImages}
                  followUpChips={runtime.followUpChips}
                  coachEnabled={runtime.coachEnabled}
                  coachChecking={runtime.coachChecking}
                  coachFeedback={runtime.coachFeedback}
                  typingUsers={typingUsers}
                  presenceUsers={presenceUsers}
                  onAddImages={runtime.addImages}
                  onRemoveImage={runtime.removeImage}
                  onUseSuggestion={useSuggestion}
                  onToggleCoach={runtime.toggleCoach}
                  onSendAnyway={sendAnyway}
                  onUseCoachSuggestion={useCoachSuggestion}
                  onDismissCoach={runtime.clearCoachFeedback}
                  onTranscript={appendTranscript}
                  onConfirm={runtime.confirmTool}
                  onComposerActivity={onComposerActivity}
                  onUploadFile={target?.kind === "project" ? async (file) => {
                    const result = await api.uploadFile(target.id, file);
                    const sizeKB = (result.size / 1024).toFixed(1);
                    runtime.aui.composer().setText(`[Datei hochgeladen: ${result.path} (${sizeKB} KB)]`);
                  } : undefined}
                />
              )}
            </ThreadPrimitive.Viewport>
          )}
        </div>
      </ThreadPrimitive.Root>
    </AuiProvider>
  );
}
