import {
  AuiProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useMessage,
  useMessagePart,
  type MessagePartState,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import { Bot, Check, History, ImagePlus, Loader2, RefreshCw, RotateCcw, Send, ShieldAlert, Square, User, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import VoiceChatButton from "@/components/VoiceChatButton";
import {
  type ChatV2Target,
  type HydraHiveRuntime,
  type HydraHiveRuntimeOptions,
  type PendingImage,
  type PendingToolConfirm,
  useHydraHiveRuntime,
} from "./hydrahive-runtime";
import type { SessionPreview } from "@/lib/api";

type DataPart = Extract<MessagePartState, { type: "data" }>;

function partText(part: MessagePartState): string {
  if (part.type === "text") return part.text;
  if (part.type === "reasoning") return part.text;
  return "";
}

function TokenBadge() {
  const message = useMessage();
  const custom = message.metadata.custom as {
    tokenUsage?: { input?: number; output?: number; cache_read?: number; cache_write?: number; rounds?: number };
    model?: string;
  };
  const usage = custom.tokenUsage;
  if (message.role !== "assistant" || !usage || message.status?.type === "running") return null;
  const input = usage.input ?? 0;
  const output = usage.output ?? 0;
  const cacheRead = usage.cache_read ?? 0;
  const cachePct = input > 0 ? Math.round((cacheRead / input) * 100) : 0;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground font-mono tabular-nums">
      <span>↑ {input}</span>
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
      <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        {part.name === "info" ? String(data.info ?? "") : `Context: ${data.history_tokens ?? 0} History · ${data.tool_tokens ?? 0} Tools`}
      </div>
    );
  }
  if (part.name === "tool_image") {
    const src = String(data.tool_image ?? "");
    return (
      <div className="rounded-xl border border-border/60 bg-card/70 p-2">
        {src ? <img src={src} alt={String(data.tool_name ?? "tool image")} className="max-h-72 rounded-lg object-contain" /> : null}
      </div>
    );
  }
  if (part.name === "tool_warning") {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
        {String(data.tool_name ?? "tool")}: {String(data.tool_warning ?? "Warnung")}
      </div>
    );
  }
  if (part.name === "tool_confirm_required") {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
        Bestätigung nötig: {String(data.tool_name ?? "tool")} wartet unten im Composer-Bereich.
      </div>
    );
  }
  const title = part.name === "tool_call"
    ? `Tool: ${String(data.tool_call ?? "unknown")}`
    : part.name === "tool_result"
      ? "Tool-Ergebnis"
      : part.name === "tool_confirm_required"
        ? `Bestätigung nötig: ${String(data.tool_name ?? "tool")}`
        : part.name;
  return (
    <details className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-sm">
      <summary className="cursor-pointer text-muted-foreground">{title}</summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

function MessagePart() {
  const part = useMessagePart();
  if (part.type === "text" || part.type === "reasoning") {
    const text = partText(part);
    return (
      <div className="prose prose-sm max-w-none dark:prose-invert prose-pre:bg-muted prose-pre:text-foreground">
        <ReactMarkdown>{text}</ReactMarkdown>
      </div>
    );
  }
  if (part.type === "data") return <ToolDataPart part={part} />;
  if (part.type === "image") {
    return <img src={part.image} alt={part.filename ?? "image"} className="max-h-72 rounded-lg object-contain" />;
  }
  return null;
}

function ChatMessage() {
  const message = useMessage();
  const isUser = message.role === "user";
  return (
    <MessagePrimitive.Root className={cn("flex gap-3 px-4 py-4", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Bot className="h-4 w-4" />
        </div>
      )}
      <div className={cn(
        "max-w-[min(860px,92vw)] rounded-2xl border px-4 py-3 shadow-sm",
        isUser
          ? "border-primary/20 bg-primary text-primary-foreground"
          : "border-border/70 bg-card/95 text-card-foreground"
      )}>
        <MessagePrimitive.Parts>
          {() => <MessagePart />}
        </MessagePrimitive.Parts>
        {message.status?.type === "running" && (
          <div className="mt-2 inline-flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            streamt
          </div>
        )}
        <TokenBadge />
      </div>
      {isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
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

function CoachFeedbackCard({
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

function Composer({
  isRunning,
  pendingConfirms,
  confirmingIds,
  pendingImages,
  followUpChips,
  coachEnabled,
  coachChecking,
  coachFeedback,
  onAddImages,
  onRemoveImage,
  onUseSuggestion,
  onToggleCoach,
  onSendAnyway,
  onUseCoachSuggestion,
  onDismissCoach,
  onTranscript,
  onConfirm,
}: {
  isRunning: boolean;
  pendingConfirms: PendingToolConfirm[];
  confirmingIds: Set<string>;
  pendingImages: PendingImage[];
  followUpChips: string[];
  coachEnabled: boolean;
  coachChecking: boolean;
  coachFeedback: { reason?: string; suggestion?: string } | null;
  onAddImages: (files: FileList | File[]) => void;
  onRemoveImage: (index: number) => void;
  onUseSuggestion: (text: string) => void;
  onToggleCoach: (enabled: boolean) => void;
  onSendAnyway: () => void;
  onUseCoachSuggestion: () => void;
  onDismissCoach: () => void;
  onTranscript: (text: string) => void;
  onConfirm: (toolCallId: string, decision: "approve" | "deny") => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  return (
    <ThreadPrimitive.ViewportFooter className="sticky bottom-0 border-t border-border/70 bg-background/95 p-3 backdrop-blur">
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
              className="rounded-full border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary transition hover:bg-primary/15"
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
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Bild hochladen"
        >
          <ImagePlus className={cn("h-4 w-4", pendingImages.length > 0 && "text-primary")} />
        </button>
        <ComposerPrimitive.Input
          rows={1}
          autoFocus
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
            "inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border text-muted-foreground transition hover:bg-muted",
            !isRunning && "hidden"
          )}
        >
          <Square className="h-4 w-4" />
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40">
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

function ChatShellInner({ runtime, hideHeader, headerLabel, target }: ChatShellProps & { runtime: HydraHiveRuntime }) {
  const label = headerLabel ?? target?.label ?? "";
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
                {runtime.error ? <div className="rounded-full bg-destructive/10 px-3 py-1 text-xs text-destructive">{runtime.error}</div> : null}
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
                  {() => <ChatMessage />}
                </ThreadPrimitive.Messages>
              </div>
              <Composer
                isRunning={runtime.isRunning}
                pendingConfirms={runtime.pendingConfirms}
                confirmingIds={runtime.confirmingIds}
                pendingImages={runtime.pendingImages}
                followUpChips={runtime.followUpChips}
                coachEnabled={runtime.coachEnabled}
                coachChecking={runtime.coachChecking}
                coachFeedback={runtime.coachFeedback}
                onAddImages={runtime.addImages}
                onRemoveImage={runtime.removeImage}
                onUseSuggestion={useSuggestion}
                onToggleCoach={runtime.toggleCoach}
                onSendAnyway={sendAnyway}
                onUseCoachSuggestion={useCoachSuggestion}
                onDismissCoach={runtime.clearCoachFeedback}
                onTranscript={appendTranscript}
                onConfirm={runtime.confirmTool}
              />
            </ThreadPrimitive.Viewport>
          )}
        </div>
      </ThreadPrimitive.Root>
    </AuiProvider>
  );
}
