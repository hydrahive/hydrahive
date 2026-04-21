import { useEffect, useState, useRef } from "react";
import { Mic, Volume2, Activity, CheckCircle, XCircle, Play, Square, Send } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

interface VoiceProvider {
  id: string;
  name: string;
  available: boolean;
}
interface TTSProvider extends VoiceProvider {
  voices: { id: string; name: string; language: string; gender: string | null }[];
}
interface STTProvider extends VoiceProvider {
  languages: string[];
}
interface VoiceStatusResponse {
  installed: boolean;
  stt: { host: string; port: number; available: boolean };
  tts: { host: string; port: number; available: boolean };
  stt_providers: STTProvider[];
  tts_providers: TTSProvider[];
  current_stt: { provider: string };
  current_tts: { provider: string; voice: string | null };
  global_stt_provider: string | null;
  global_tts_provider: string | null;
  user_preferences: { stt_provider: string | null; stt_voice: string | null; tts_provider: string | null; tts_voice: string | null };
  default_agent: string;
}

export function VoicePage() {
  const [status, setStatus] = useState<VoiceStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [testText, setTestText] = useState("");
  const [testResponse, setTestResponse] = useState("");
  const [testing, setTesting] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [saving, setSaving] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const { isAdmin } = useAuth();

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

  async function handleSetTtsProvider(providerId: string) {
    if (!status) return;
    setSaving(true);
    try {
      const prefs = await api.setVoicePreference("tts", providerId, null);
      setStatus({ ...status, user_preferences: prefs, current_tts: { provider: providerId, voice: null } });
    } catch (e) { console.error(e); }
    setSaving(false);
  }

  async function handleSetTtsVoice(voiceId: string) {
    if (!status) return;
    const providerId = status.current_tts.provider;
    setSaving(true);
    try {
      const prefs = await api.setVoicePreference("tts", providerId, voiceId || null);
      setStatus({ ...status, user_preferences: prefs, current_tts: { provider: providerId, voice: voiceId || null } });
    } catch (e) { console.error(e); }
    setSaving(false);
  }

  async function handleSetSttProvider(providerId: string) {
    if (!status) return;
    setSaving(true);
    try {
      const prefs = await api.setVoicePreference("stt", providerId, null);
      setStatus({ ...status, user_preferences: prefs, current_stt: { provider: providerId } });
    } catch (e) { console.error(e); }
    setSaving(false);
  }

  async function handleSetGlobalProvider(providerType: "stt" | "tts", providerId: string) {
    setSaving(true);
    try {
      const res = await api.setVoiceGlobalProvider(providerType, providerId);
      if (status) {
        setStatus({ ...status, global_stt_provider: res.global_stt_provider, global_tts_provider: res.global_tts_provider });
      }
    } catch (e) { console.error(e); }
    setSaving(false);
  }

  if (loading) return <div className="p-6 text-muted-foreground">Lade Voice-Status...</div>;
  if (!status) return <div className="p-6 text-destructive">Voice-Status konnte nicht geladen werden</div>;

  const currentTtsProvider = status.tts_providers.find(p => p.id === status.current_tts.provider);
  const currentSttProvider = status.stt_providers.find(p => p.id === status.current_stt.provider);
  const availableTts = status.tts_providers.filter(p => p.available);
  const availableStt = status.stt_providers.filter(p => p.available);

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
          available={status.installed}
          detail={status.installed ? "Installiert" : "Nicht installiert"}
        />
        <StatusCard
          icon={<Mic className="h-5 w-5" />}
          title="STT (Speech-to-Text)"
          available={currentSttProvider?.available ?? false}
          detail={currentSttProvider
            ? `${currentSttProvider.name}${currentSttProvider.available ? "" : " — nicht erreichbar"}`
            : "Kein Provider aktiv"}
        />
        <StatusCard
          icon={<Volume2 className="h-5 w-5" />}
          title="TTS (Text-to-Speech)"
          available={currentTtsProvider?.available ?? false}
          detail={currentTtsProvider
            ? `${currentTtsProvider.name}${currentTtsProvider.available ? "" : " — nicht erreichbar"}`
            : "Kein Provider aktiv"}
        />
      </div>

      {/* Provider-Auswahl */}
      <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Provider-Auswahl</h2>
          {saving && <span className="text-xs text-muted-foreground">speichert …</span>}
        </div>

        {/* TTS-Provider */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className="text-xs font-medium flex flex-col gap-1.5">
            <span>TTS-Provider</span>
            <select
              value={status.current_tts.provider}
              onChange={e => handleSetTtsProvider(e.target.value)}
              disabled={saving}
              className="rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              {status.tts_providers.map(p => (
                <option key={p.id} value={p.id} disabled={!p.available}>
                  {p.name} {p.available ? "" : "(nicht verfügbar)"}
                </option>
              ))}
            </select>
          </label>

          <label className="text-xs font-medium flex flex-col gap-1.5">
            <span>TTS-Stimme</span>
            <select
              value={status.current_tts.voice ?? ""}
              onChange={e => handleSetTtsVoice(e.target.value)}
              disabled={saving || !currentTtsProvider || currentTtsProvider.voices.length === 0}
              className="rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">Standard</option>
              {currentTtsProvider?.voices.map(v => (
                <option key={v.id} value={v.id}>
                  {v.name} {v.gender ? `(${v.gender})` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="text-xs font-medium flex flex-col gap-1.5">
            <span>STT-Provider</span>
            <select
              value={status.current_stt.provider}
              onChange={e => handleSetSttProvider(e.target.value)}
              disabled={saving}
              className="rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              {status.stt_providers.map(p => (
                <option key={p.id} value={p.id} disabled={!p.available}>
                  {p.name} {p.available ? "" : "(nicht verfügbar)"}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Admin: Globale Defaults */}
        {isAdmin && (
          <div className="pt-3 border-t border-border space-y-2">
            <h3 className="text-xs font-semibold text-muted-foreground">Globale Defaults (Admin)</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="text-xs flex flex-col gap-1.5">
                <span>Globaler TTS-Default</span>
                <select
                  value={status.global_tts_provider ?? ""}
                  onChange={e => e.target.value && handleSetGlobalProvider("tts", e.target.value)}
                  disabled={saving}
                  className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
                >
                  <option value="" disabled>— nicht gesetzt (erster registrierter) —</option>
                  {availableTts.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs flex flex-col gap-1.5">
                <span>Globaler STT-Default</span>
                <select
                  value={status.global_stt_provider ?? ""}
                  onChange={e => e.target.value && handleSetGlobalProvider("stt", e.target.value)}
                  disabled={saving}
                  className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
                >
                  <option value="" disabled>— nicht gesetzt (erster registrierter) —</option>
                  {availableStt.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        )}

        <p className="text-xs text-muted-foreground pt-2">
          Aktiv: <code className="bg-muted px-1.5 py-0.5 rounded">{status.current_tts.provider}</code>
          {status.current_tts.voice && <> / Stimme <code className="bg-muted px-1.5 py-0.5 rounded">{status.current_tts.voice}</code></>}
        </p>
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
              {(currentTtsProvider?.available ?? false) && (
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
          <p><span className="text-green-500">POST</span> /api/voice/tts — Text → Audio</p>
          <p><span className="text-green-500">POST</span> /api/voice/pipeline — Audio → Agent → Audio</p>
          <p><span className="text-blue-500">GET</span>&nbsp; /api/voice/status — Provider-Status</p>
          <p><span className="text-blue-500">GET</span>&nbsp; /api/voice/preferences — User-Prefs lesen</p>
          <p><span className="text-yellow-500">PUT</span>&nbsp; /api/voice/preferences — User-Prefs setzen</p>
          <p><span className="text-yellow-500">PUT</span>&nbsp; /api/voice/providers/default — globaler Default (Admin)</p>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          Default-Agent: <code className="bg-muted px-1.5 py-0.5 rounded">{status.default_agent}</code>
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
