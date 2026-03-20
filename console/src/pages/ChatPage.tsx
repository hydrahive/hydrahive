import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Bot, User } from "lucide-react";
import { api } from "@/lib/api";

interface Message {
  role:    "user" | "assistant";
  content: string;
}

export function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const navigate  = useNavigate();

  const [messages,  setMessages]  = useState<Message[]>([]);
  const [input,     setInput]     = useState("");
  const [sending,   setSending]   = useState(false);
  const [error,     setError]     = useState("");
  const [projectName, setProjectName] = useState(id ?? "");

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!id) return;
    api.get<Record<string, unknown>>(`/projects/${id}`)
      .then(d => {
        const ident = d.identity as { name?: string } | undefined;
        if (ident?.name) setProjectName(ident.name);
      })
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!input.trim() || sending || !id) return;
    const content = input.trim();
    setInput("");
    setError("");
    setMessages(ms => [...ms, { role: "user", content }]);
    setSending(true);
    try {
      const res = await api.sendMessage(id, content);
      setMessages(ms => [...ms, { role: "assistant", content: res.response }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fehler beim Senden");
      setMessages(ms => ms.slice(0, -1));
      setInput(content);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b flex-shrink-0">
        <button
          onClick={() => navigate("/projects")}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold truncate">{projectName}</h1>
          <p className="text-xs text-muted-foreground font-mono">{id}</p>
        </div>
      </div>

      {/* Nachrichten */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-3 text-muted-foreground">
            <Bot className="h-10 w-10" />
            <p className="text-sm">Schreib eine Nachricht, um den Boss-Agenten zu erreichen.</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "assistant" && (
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                <Bot className="h-4 w-4 text-primary" />
              </div>
            )}
            <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words ${
              msg.role === "user"
                ? "bg-primary text-primary-foreground"
                : "bg-card border"
            }`}>
              {msg.content}
            </div>
            {msg.role === "user" && (
              <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center flex-shrink-0 mt-0.5">
                <User className="h-4 w-4" />
              </div>
            )}
          </div>
        ))}
        {sending && (
          <div className="flex gap-3 justify-start">
            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Bot className="h-4 w-4 text-primary" />
            </div>
            <div className="bg-card border rounded-lg px-3 py-2">
              <div className="flex gap-1 items-center h-5">
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Fehler */}
      {error && (
        <div className="px-4 py-2 text-xs text-destructive bg-destructive/10 border-t flex-shrink-0">
          {error}
        </div>
      )}

      {/* Eingabe */}
      <div className="px-4 py-3 border-t flex-shrink-0">
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Nachricht… (Enter zum Senden, Shift+Enter für Zeilenumbruch)"
            rows={1}
            disabled={sending}
            className="flex-1 px-3 py-2 text-sm border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-ring resize-none disabled:opacity-50"
            style={{ maxHeight: "120px", overflowY: "auto" }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || sending}
            className="flex-shrink-0 p-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
