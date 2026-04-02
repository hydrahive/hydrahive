import { useEffect, useState, useRef } from "react";
import { Mic, Volume2, Activity, CheckCircle, XCircle, Play, Square, Send } from "lucide-react";
import { api } from "@/lib/api";

interface VoiceStatus {
  installed: boolean;
  stt: { host: string; port: number; available: boolean };
  tts: { host: string; port: number; available: boolean };
  default_agent: string;
}

export function VoicePage() {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [testText, setTestText] = useState("");
  const [testResponse, setTestResponse] = useState("");
  const [testing, setTesting] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  async function loadStatus() {
    try {
      const s = await api.voiceStatus();
      setStatus(s);
    } catch { /* silent */ }
    setLoading(false);
  }

  useEffect(() => {
    loadStatus();
    const t = setInterval(loadStatus, 10000);
    return () => clearInterval(t);
  }, []);

  async function handleTestText() {
    if (!testText.trim()) return;
    setTesting(true);
    setTestResponse("");
    try {
      const res = await api.voiceText(testText);
      setTestResponse(res.text);
    } catch (e) {
      setTestResponse(`Fehler: ${e instanceof Error ? e.message : "Unbekannt"}`);
    }
    setTesting(false);
  }

  async function handlePlayTts() {
    const text = testResponse || testText;
    if (!text.trim()) return;
    setPlaying(true);
    try {
      const blob = await api.voiceTts(text);
      const url = URL.createObjectURL(blob);
      if (audioRef.current) {
        audioRef.current.src = url;
        audioRef.current.onended = () => { setPlaying(false); URL.revokeObjectURL(url); };
        audioRef.current.play();
      }
    } catch {
      setPlaying(false);
    }
  }

  function handleStopAudio() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setPlaying(false);
  }

  if (loading) return <div className="p-6 text-muted-foreground">Lade Voice-Status...</div>;

  return (
    <div className="space-y-6 p-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold">Voice Interface</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Sprachsteuerung für HydraHive — STT, TTS und Agent-Kommunikation
        </p>
      </div>

      {/* Service-Status */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatusCard
          icon={<Activity className="h-5 w-5" />}
          title="Extension"
          available={status?.installed ?? false}
          detail={status?.installed ? "Installiert" : "Nicht installiert"}
        />
        <StatusCard
          icon={<Mic className="h-5 w-5" />}
          title="STT (Speech-to-Text)"
          available={status?.stt.available ?? false}
          detail={status?.stt.available
            ? `faster-whisper auf Port ${status?.stt.port}`
            : "Nicht erreichbar"}
        />
        <StatusCard
          icon={<Volume2 className="h-5 w-5" />}
          title="TTS (Text-to-Speech)"
          available={status?.tts.available ?? false}
          detail={status?.tts.available
            ? `Piper auf Port ${status?.tts.port}`
            : "Nicht erreichbar"}
        />
      </div>

      {/* Test-Bereich */}
      <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
        <h2 className="text-sm font-semibold">Voice testen</h2>

        <div className="flex gap-2">
          <input
            type="text"
            value={testText}
            onChange={e => setTestText(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleTestText()}
            placeholder="Text eingeben und an Agent senden..."
            className="flex-1 rounded-xl border border-border bg-background px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <button
            onClick={handleTestText}
            disabled={testing || !testText.trim()}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            <Send className="h-4 w-4" />
            {testing ? "..." : "Senden"}
          </button>
        </div>

        {testResponse && (
          <div className="rounded-xl bg-muted/50 p-4 space-y-3">
            <p className="text-sm">{testResponse}</p>
            <div className="flex gap-2">
              {status?.tts.available && (
                playing ? (
                  <button
                    onClick={handleStopAudio}
                    className="flex items-center gap-2 rounded-lg bg-destructive px-3 py-1.5 text-xs text-destructive-foreground hover:bg-destructive/90 transition-colors"
                  >
                    <Square className="h-3 w-3" /> Stop
                  </button>
                ) : (
                  <button
                    onClick={handlePlayTts}
                    className="flex items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90 transition-colors"
                  >
                    <Play className="h-3 w-3" /> Vorlesen
                  </button>
                )
              )}
            </div>
          </div>
        )}
      </div>

      {/* API-Dokumentation */}
      <div className="rounded-2xl border border-border bg-card p-5 space-y-3">
        <h2 className="text-sm font-semibold">API-Endpunkte</h2>
        <div className="text-xs font-mono space-y-2 text-muted-foreground">
          <p><span className="text-green-500">POST</span> /api/voice — Text an Agent senden</p>
          <p><span className="text-green-500">POST</span> /api/voice/stt — Audio → Text</p>
          <p><span className="text-green-500">POST</span> /api/voice/tts — Text → Audio (WAV)</p>
          <p><span className="text-green-500">POST</span> /api/voice/pipeline — Audio → Agent → Audio</p>
          <p><span className="text-blue-500">GET</span>&nbsp; /api/voice/status — Service-Status</p>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          Default-Agent: <code className="bg-muted px-1.5 py-0.5 rounded">{status?.default_agent}</code>
          {" "}— änderbar in <code className="bg-muted px-1.5 py-0.5 rounded">/etc/hydrahive/voice.json</code>
        </p>
      </div>

      <audio ref={audioRef} className="hidden" />
    </div>
  );
}

function StatusCard({ icon, title, available, detail }: {
  icon: React.ReactNode; title: string; available: boolean; detail: string;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 flex items-start gap-3">
      <div className={`mt-0.5 ${available ? "text-green-500" : "text-muted-foreground"}`}>
        {icon}
      </div>
      <div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{title}</span>
          {available
            ? <CheckCircle className="h-3.5 w-3.5 text-green-500" />
            : <XCircle className="h-3.5 w-3.5 text-muted-foreground" />
          }
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">{detail}</p>
      </div>
    </div>
  );
}
