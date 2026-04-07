/**
 * VoiceChatButton — Mikrofon-Button für Chat-Eingabe (STT via /api/voice/stt)
 *
 * Nimmt Audio auf, konvertiert zu WAV, schickt an Backend, gibt Text zurück.
 * Verwendet MediaRecorder API im Browser.
 */
import { useState, useRef, useCallback } from "react";
import { Mic, MicOff, Loader2 } from "lucide-react";
import { api } from "../lib/api";

interface Props {
  onTranscript: (text: string) => void;
  disabled?: boolean;
  className?: string;
}

/** Convert Float32 PCM samples to 16-bit WAV Blob */
function float32ToWav(samples: Float32Array, sampleRate: number): Blob {
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const dataSize = samples.length * (bitsPerSample / 8);
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  // RIFF header
  const writeStr = (off: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeStr(36, "data");
  view.setUint32(40, dataSize, true);

  // PCM samples
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}

export default function VoiceChatButton({ onTranscript, disabled, className }: Props) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      mediaStreamRef.current = stream;
      chunksRef.current = [];

      const ctx = new AudioContext({ sampleRate: 16000 });
      contextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      // ScriptProcessor for raw PCM access (still widely supported)
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;
      processor.onaudioprocess = (e) => {
        const data = e.inputBuffer.getChannelData(0);
        chunksRef.current.push(new Float32Array(data));
      };
      source.connect(processor);
      processor.connect(ctx.destination);

      setRecording(true);
    } catch (err) {
      console.error("Mikrofon-Zugriff fehlgeschlagen:", err);
    }
  }, []);

  const stopRecording = useCallback(async () => {
    setRecording(false);

    // Stop media tracks
    mediaStreamRef.current?.getTracks().forEach(t => t.stop());
    processorRef.current?.disconnect();
    const ctx = contextRef.current;
    if (ctx && ctx.state !== "closed") await ctx.close();

    const chunks = chunksRef.current;
    if (chunks.length === 0) return;

    // Merge chunks into single Float32Array
    const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const c of chunks) { merged.set(c, offset); offset += c.length; }

    const wavBlob = float32ToWav(merged, 16000);

    setTranscribing(true);
    try {
      const result = await api.voiceStt(wavBlob);
      if (result.text?.trim()) {
        onTranscript(result.text.trim());
      }
    } catch (err) {
      console.error("STT fehlgeschlagen:", err);
    } finally {
      setTranscribing(false);
    }
  }, [onTranscript]);

  const toggle = useCallback(() => {
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [recording, startRecording, stopRecording]);

  if (transcribing) {
    return (
      <button type="button" disabled className={`flex items-center justify-center p-2 border rounded-md bg-background text-muted-foreground ${className || ""}`}>
        <Loader2 className="h-4 w-4 animate-spin" />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={disabled}
      className={`flex items-center justify-center p-2 border rounded-md transition-colors flex-shrink-0 ${
        recording
          ? "bg-destructive/10 border-destructive text-destructive hover:bg-destructive/20 animate-pulse"
          : "bg-background hover:bg-muted text-muted-foreground"
      } ${disabled ? "opacity-50 cursor-not-allowed" : ""} ${className || ""}`}
      aria-label={recording ? "Aufnahme stoppen" : "Spracheingabe"}
      title={recording ? "Aufnahme stoppen" : "Spracheingabe"}
    >
      {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
    </button>
  );
}
