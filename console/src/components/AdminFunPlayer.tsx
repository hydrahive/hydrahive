/**
 * AdminFunPlayer.tsx — Floating Music Player für Admins mit Beat-Sync Visuals (#AdminFun)
 *
 * Features:
 *  - MP3/OGG/WAV Upload (max 50 MB)
 *  - HTML5 Audio Player (Play/Pause/Next/Prev/Volume)
 *  - Web Audio API Analyzer: Bass/Mid/Treble via FFT
 *  - Beat-Detection: Energy-Threshold-Algorithmus
 *  - CSS-Variables `--adminfun-bass/mid/treble/beat` pulsieren global
 *  - Admin-Toggle persistent in /etc/hydrahive/adminfun/.state.json
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Pause, SkipForward, SkipBack, Upload, Trash2, Volume2, VolumeX, Music, X, Sparkles } from "lucide-react";
import { api } from "@/lib/api";

interface Track {
  name: string;
  size_bytes: number;
  modified_at: number;
}

interface AdminFunSettings {
  enabled: boolean;
  volume: number;
  current_track: string;
  sensitivity: number;
}

const FFT_SIZE = 512;
// Frequenzbänder in Bins des FFT (FFT_SIZE/2 = 256 bins, ~43Hz pro bin bei 44.1kHz Sample Rate)
const BASS_RANGE = [0, 6] as const;       // 0-260 Hz
const MID_RANGE = [6, 50] as const;       // 260-2170 Hz
const TREBLE_RANGE = [50, 256] as const;  // 2170+ Hz

export function AdminFunPlayer() {
  const [settings, setSettings] = useState<AdminFunSettings | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [playing, setPlaying] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [playlistOpen, setPlaylistOpen] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const beatHistoryRef = useRef<number[]>([]);
  const blobUrlRef = useRef<string>("");
  const [trackBlobUrl, setTrackBlobUrl] = useState<string>("");

  const loadSettings = useCallback(async () => {
    try {
      const s = await api.get<AdminFunSettings>("/admin/adminfun/settings");
      setSettings(s);
    } catch (e: any) {
      // Nicht-Admin oder noch nicht aktiv → still ausblenden
      setSettings(null);
    }
  }, []);

  const loadTracks = useCallback(async () => {
    try {
      const r = await api.get<{ tracks: Track[] }>("/admin/adminfun/tracks");
      setTracks(r.tracks || []);
    } catch {
      setTracks([]);
    }
  }, []);

  useEffect(() => {
    loadSettings();
    loadTracks();
  }, [loadSettings, loadTracks]);

  // Track-Stream als Blob laden (HTML5 Audio kann kein Auth-Header)
  useEffect(() => {
    if (!settings?.current_track || !settings.enabled) {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = "";
        setTrackBlobUrl("");
      }
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const token = localStorage.getItem("hydrahive_token") || "";
        const res = await fetch(`/api/admin/adminfun/stream/${encodeURIComponent(settings.current_track)}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
        const url = URL.createObjectURL(blob);
        blobUrlRef.current = url;
        setTrackBlobUrl(url);
      } catch (e: any) {
        if (!cancelled) setErr(`Stream-Fehler: ${e?.message || "unbekannt"}`);
      }
    })();
    return () => { cancelled = true; };
  }, [settings?.current_track, settings?.enabled]);

  // Audio-Analyzer-Loop
  const startAnalyzer = useCallback(() => {
    if (!audioRef.current) return;
    if (!audioCtxRef.current) {
      const Ctx = (window.AudioContext || (window as any).webkitAudioContext);
      audioCtxRef.current = new Ctx();
      analyserRef.current = audioCtxRef.current.createAnalyser();
      analyserRef.current.fftSize = FFT_SIZE;
      analyserRef.current.smoothingTimeConstant = 0.6;
      sourceRef.current = audioCtxRef.current.createMediaElementSource(audioRef.current);
      sourceRef.current.connect(analyserRef.current);
      analyserRef.current.connect(audioCtxRef.current.destination);
    }
    audioCtxRef.current?.resume();

    const analyser = analyserRef.current!;
    const data = new Uint8Array(analyser.frequencyBinCount);
    const sens = settings?.sensitivity ?? 1.0;

    const tick = () => {
      analyser.getByteFrequencyData(data);
      const avg = (from: number, to: number) => {
        let sum = 0;
        for (let i = from; i < to; i++) sum += data[i];
        return sum / (to - from);
      };
      const bass = avg(BASS_RANGE[0], BASS_RANGE[1]) / 255;
      const mid = avg(MID_RANGE[0], MID_RANGE[1]) / 255;
      const treble = avg(TREBLE_RANGE[0], TREBLE_RANGE[1]) / 255;

      // Beat-Detection (Energy auf Bass-Band)
      const hist = beatHistoryRef.current;
      hist.push(bass);
      if (hist.length > 43) hist.shift(); // ~1s Historie bei 43fps
      const histAvg = hist.reduce((a, b) => a + b, 0) / hist.length;
      // Sensitivity intuitiv: hoch = mehr Beats (niedrigerer Threshold)
      const isBeat = bass > histAvg * (1.35 / sens) && bass > (0.25 / sens);

      // CSS-Variablen setzen — werden global auf :root vererbt
      const root = document.documentElement;
      root.style.setProperty("--adminfun-bass", bass.toFixed(3));
      root.style.setProperty("--adminfun-mid", mid.toFixed(3));
      root.style.setProperty("--adminfun-treble", treble.toFixed(3));
      root.style.setProperty("--adminfun-beat", isBeat ? "1" : "0");
      root.style.setProperty("--adminfun-active", "1");

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [settings?.sensitivity]);

  const stopAnalyzer = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    const root = document.documentElement;
    root.style.setProperty("--adminfun-bass", "0");
    root.style.setProperty("--adminfun-mid", "0");
    root.style.setProperty("--adminfun-treble", "0");
    root.style.setProperty("--adminfun-beat", "0");
    root.style.setProperty("--adminfun-active", "0");
  }, []);

  // Play/Pause-Handler
  const togglePlay = useCallback(async () => {
    if (!audioRef.current || !settings?.current_track) return;
    if (playing) {
      audioRef.current.pause();
    } else {
      try {
        await audioRef.current.play();
        startAnalyzer();
      } catch (e: any) {
        setErr(e?.message || "Play fehlgeschlagen");
      }
    }
  }, [playing, settings?.current_track, startAnalyzer]);

  const selectTrack = useCallback(async (name: string) => {
    await api.put<any>("/admin/adminfun/settings", { current_track: name });
    setSettings(prev => prev ? { ...prev, current_track: name } : prev);
    setPlaylistOpen(false);
    // Audio wird automatisch neu geladen via src-Change → Play manuell
    setTimeout(() => {
      if (audioRef.current) {
        audioRef.current.play().then(startAnalyzer).catch(() => {});
      }
    }, 100);
  }, [startAnalyzer]);

  const nextTrack = useCallback(() => {
    if (!tracks.length || !settings?.current_track) {
      if (tracks.length) selectTrack(tracks[0].name);
      return;
    }
    const idx = tracks.findIndex(t => t.name === settings.current_track);
    const next = tracks[(idx + 1) % tracks.length];
    selectTrack(next.name);
  }, [tracks, settings?.current_track, selectTrack]);

  const prevTrack = useCallback(() => {
    if (!tracks.length || !settings?.current_track) return;
    const idx = tracks.findIndex(t => t.name === settings.current_track);
    const prev = tracks[(idx - 1 + tracks.length) % tracks.length];
    selectTrack(prev.name);
  }, [tracks, settings?.current_track, selectTrack]);

  const setVolume = useCallback(async (v: number) => {
    if (audioRef.current) audioRef.current.volume = v;
    await api.put<any>("/admin/adminfun/settings", { volume: v });
    setSettings(prev => prev ? { ...prev, volume: v } : prev);
  }, []);

  const toggleEnabled = useCallback(async () => {
    if (!settings) return;
    const newEnabled = !settings.enabled;
    await api.put<any>("/admin/adminfun/settings", { enabled: newEnabled });
    setSettings(prev => prev ? { ...prev, enabled: newEnabled } : prev);
    if (!newEnabled && audioRef.current) {
      audioRef.current.pause();
      stopAnalyzer();
    }
  }, [settings, stopAnalyzer]);

  const uploadFile = useCallback(async (file: File) => {
    setBusy(true);
    setErr("");
    try {
      const form = new FormData();
      form.append("file", file);
      const token = localStorage.getItem("hydrahive_token") || "";
      const res = await fetch("/api/admin/adminfun/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      await loadTracks();
    } catch (e: any) {
      setErr(e?.message || "Upload fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }, [loadTracks]);

  const deleteTrack = useCallback(async (name: string) => {
    if (!confirm(`Track "${name}" wirklich löschen?`)) return;
    try {
      await api.delete<any>(`/admin/adminfun/tracks/${encodeURIComponent(name)}`);
      await loadTracks();
      if (settings?.current_track === name) {
        await api.put<any>("/admin/adminfun/settings", { current_track: "" });
        setSettings(prev => prev ? { ...prev, current_track: "" } : prev);
      }
    } catch (e: any) {
      setErr(e?.message || "Löschen fehlgeschlagen");
    }
  }, [loadTracks, settings?.current_track]);

  // Audio-Events
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnd = () => { setPlaying(false); nextTrack(); };
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnd);
    return () => {
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnd);
    };
  }, [nextTrack]);

  useEffect(() => () => { stopAnalyzer(); }, [stopAnalyzer]);

  // Ausblenden wenn kein Admin (settings=null)
  if (settings === null) return null;

  // Komplett aus → nur kleiner Aktivierungsbutton
  if (!settings.enabled) {
    return (
      <button
        onClick={toggleEnabled}
        title="AdminFun aktivieren"
        className="fixed bottom-4 left-4 z-50 rounded-full border bg-card/80 backdrop-blur p-2 shadow-lg hover:bg-accent transition"
      >
        <Sparkles className="h-4 w-4 text-muted-foreground" />
      </button>
    );
  }

  return (
    <>
      {/* Audio-Element (hidden, gesteuert per ref) */}
      {trackBlobUrl && (
        <audio
          ref={audioRef}
          src={trackBlobUrl}
          preload="auto"
        />
      )}

      {/* Floating Player */}
      <div
        className="fixed bottom-4 left-4 z-50 rounded-2xl border bg-card/95 backdrop-blur shadow-2xl transition-all"
        style={{
          width: collapsed ? "auto" : "320px",
          boxShadow: `0 4px 24px rgba(168, 85, 247, calc(0.3 + var(--adminfun-bass, 0) * 0.5))`,
        }}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2 border-b">
          <Music
            className="h-4 w-4 text-purple-500"
            style={{ transform: `scale(calc(1 + var(--adminfun-beat, 0) * 0.3))`, transition: "transform 0.05s" }}
          />
          {!collapsed && <span className="text-xs font-semibold flex-1">AdminFun</span>}
          {!collapsed && (
            <button onClick={() => setCollapsed(true)} className="text-muted-foreground hover:text-foreground">
              <X className="h-3 w-3" />
            </button>
          )}
          {collapsed && (
            <button onClick={() => setCollapsed(false)} className="text-xs text-muted-foreground hover:text-foreground px-1">
              ▲
            </button>
          )}
        </div>

        {!collapsed && (
          <>
            {/* Current Track + Controls */}
            <div className="p-3 space-y-2">
              <div className="text-[11px] text-muted-foreground truncate min-h-[14px]">
                {settings.current_track || "Kein Track ausgewählt"}
              </div>

              {/* Bass/Mid/Treble Visualizer */}
              <div className="flex items-end gap-0.5 h-8">
                {[...Array(32)].map((_, i) => {
                  const band = i < 6 ? "bass" : i < 20 ? "mid" : "treble";
                  const color = band === "bass" ? "rgb(168 85 247)" : band === "mid" ? "rgb(236 72 153)" : "rgb(59 130 246)";
                  return (
                    <div
                      key={i}
                      className="flex-1 rounded-t"
                      style={{
                        background: color,
                        height: `calc(${i < 6 ? "var(--adminfun-bass, 0)" : i < 20 ? "var(--adminfun-mid, 0)" : "var(--adminfun-treble, 0)"} * 100%)`,
                        transition: "height 0.05s",
                        opacity: 0.8,
                      }}
                    />
                  );
                })}
              </div>

              {/* Play Controls */}
              <div className="flex items-center justify-center gap-2">
                <button onClick={prevTrack} className="rounded p-1.5 hover:bg-accent" title="Vorheriger">
                  <SkipBack className="h-4 w-4" />
                </button>
                <button
                  onClick={togglePlay}
                  disabled={!settings.current_track}
                  className="rounded-full bg-purple-500 p-2 text-white hover:bg-purple-600 disabled:opacity-50"
                >
                  {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                </button>
                <button onClick={nextTrack} className="rounded p-1.5 hover:bg-accent" title="Nächster">
                  <SkipForward className="h-4 w-4" />
                </button>
              </div>

              {/* Volume */}
              <div className="flex items-center gap-2">
                {settings.volume > 0 ? <Volume2 className="h-3 w-3 text-muted-foreground" /> : <VolumeX className="h-3 w-3 text-muted-foreground" />}
                <input
                  type="range"
                  min={0} max={1} step={0.01}
                  value={settings.volume}
                  onChange={e => setVolume(parseFloat(e.target.value))}
                  className="flex-1 accent-purple-500"
                />
              </div>

              {/* Sensitivity */}
              <div className="flex items-center gap-2">
                <Sparkles className="h-3 w-3 text-muted-foreground" />
                <input
                  type="range"
                  min={0.5} max={2} step={0.05}
                  value={settings.sensitivity}
                  onChange={async e => {
                    const v = parseFloat(e.target.value);
                    await api.put<any>("/admin/adminfun/settings", { sensitivity: v });
                    setSettings(prev => prev ? { ...prev, sensitivity: v } : prev);
                  }}
                  className="flex-1 accent-pink-500"
                  title="Beat-Empfindlichkeit"
                />
                <span className="text-[10px] text-muted-foreground w-8 text-right">{settings.sensitivity.toFixed(2)}</span>
              </div>

              {/* Actions */}
              <div className="flex gap-1 pt-1">
                <button
                  onClick={() => setPlaylistOpen(p => !p)}
                  className="flex-1 rounded border px-2 py-1 text-[11px] hover:bg-accent transition"
                >
                  Playlist ({tracks.length})
                </button>
                <label className="rounded border px-2 py-1 text-[11px] hover:bg-accent transition cursor-pointer flex items-center gap-1">
                  <Upload className="h-3 w-3" />
                  <input
                    type="file"
                    accept=".mp3,.ogg,.wav,.m4a,.opus,audio/*"
                    className="hidden"
                    onChange={e => {
                      const f = e.target.files?.[0];
                      if (f) uploadFile(f);
                      e.target.value = "";
                    }}
                  />
                </label>
                <button
                  onClick={toggleEnabled}
                  title="AdminFun ausschalten"
                  className="rounded border px-2 py-1 text-[11px] hover:bg-accent transition"
                >
                  Aus
                </button>
              </div>

              {busy && <div className="text-[10px] text-muted-foreground">Lade hoch...</div>}
              {err && <div className="text-[10px] text-red-500">{err}</div>}
            </div>

            {/* Playlist */}
            {playlistOpen && (
              <div className="border-t max-h-48 overflow-y-auto">
                {tracks.length === 0 && (
                  <div className="p-3 text-[11px] text-muted-foreground text-center">Noch keine Tracks</div>
                )}
                {tracks.map(t => (
                  <div
                    key={t.name}
                    className={`flex items-center gap-1 px-2 py-1 text-[11px] hover:bg-accent cursor-pointer ${settings.current_track === t.name ? "bg-accent/50" : ""}`}
                    onClick={() => selectTrack(t.name)}
                  >
                    <span className="flex-1 truncate">{t.name}</span>
                    <span className="text-muted-foreground">{(t.size_bytes / 1024 / 1024).toFixed(1)}MB</span>
                    <button
                      onClick={e => { e.stopPropagation(); deleteTrack(t.name); }}
                      className="text-muted-foreground hover:text-red-500"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
