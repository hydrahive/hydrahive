import { useCallback, useEffect, useRef, useState } from "react";
import {
  Plus, Play, Square, Trash2, Upload, Loader2,
  Monitor, Server, HardDrive, Cpu, Activity,
  X, ChevronRight, AlertCircle, Network, Pencil, ScrollText, RefreshCw,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { api } from "@/lib/api";

// ── Helpers ────────────────────────────────────────────────────────────────────
function fmtDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e: any) => e.msg || String(e)).join(", ");
  return fallback;
}

// ── Types ──────────────────────────────────────────────────────────────────────
interface VMInfo {
  vm_id: string;
  name: string;
  cpu: number;
  ram_mb: number;
  disk_gb: number;
  iso_file: string | null;
  status: string;
  pid: number | null;
  vnc_port: number | null;
  owner: string;
  created_at: number;
  disk_path: string;
  network_mode: string;
  bridge_iface: string;
}

interface ISOInfo {
  filename: string;
  size_bytes: number;
  size_human: string;
  uploaded_at: number;
  path: string;
}

// ── Helper Components ──────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { cls: string; label: string; dot: string }> = {
    running:   { cls: "status-pill-ok",   label: "Läuft",   dot: "bg-green-500" },
    stopped:   { cls: "",                  label: "Gestoppt", dot: "bg-gray-400" },
    created:   { cls: "",                  label: "Bereit",   dot: "bg-gray-400" },
    starting:  { cls: "status-pill-warn", label: "Startet…", dot: "bg-yellow-500 animate-pulse" },
    stopping:  { cls: "status-pill-warn", label: "Stoppt…", dot: "bg-yellow-500 animate-pulse" },
    error:     { cls: "text-red-600",     label: "Fehler",   dot: "bg-red-500" },
  };
  const c = cfg[status] ?? { cls: "", label: status, dot: "bg-gray-400" };
  return (
    <span className={`status-pill ${c.cls}`}>
      <span className={`w-2 h-2 rounded-full shrink-0 ${c.dot}`} />
      {c.label}
    </span>
  );
}

// ── VMLogModal ─────────────────────────────────────────────────────────────────
function VMLogModal({ vm, onClose }: { vm: VMInfo; onClose: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [sizeBytes, setSizeBytes] = useState(0);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function fetchLog() {
    setLoading(true);
    try {
      const d = await api.vmsLog(vm.vm_id, 200);
      setLines(d.lines ?? []);
      setSizeBytes(d.size_bytes ?? 0);
    } finally {
      setLoading(false);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  }

  useEffect(() => { fetchLog(); }, [vm.vm_id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="card w-full max-w-3xl mx-4 flex flex-col" style={{ maxHeight: "80vh" }}>
        <div className="flex items-center justify-between mb-3 shrink-0">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <ScrollText className="w-4 h-4" /> Log — {vm.name}
            <span className="text-xs text-muted-foreground font-normal">({(sizeBytes / 1024).toFixed(1)} KB)</span>
          </h2>
          <div className="flex items-center gap-2">
            <button className="btn btn-sm flex items-center gap-1" onClick={fetchLog} disabled={loading}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button className="btn btn-sm" onClick={onClose}><X className="w-4 h-4" /></button>
          </div>
        </div>
        <div className="overflow-auto flex-1 bg-black rounded-lg p-3 font-mono text-xs text-green-400 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin mr-2" /> Lade Log…
            </div>
          ) : lines.length === 0 ? (
            <span className="text-muted-foreground">Noch kein Log vorhanden — VM starten um Ausgabe zu sehen.</span>
          ) : (
            lines.map((l, i) => <div key={i} className="leading-5 whitespace-pre-wrap break-all">{l}</div>)
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}

// ── EditNetworkModal ───────────────────────────────────────────────────────────
interface EditNetworkModalProps {
  vm: VMInfo;
  onClose: () => void;
  onUpdated: () => void;
}

function EditNetworkModal({ vm, onClose, onUpdated }: EditNetworkModalProps) {
  const [cpu, setCpu] = useState(vm.cpu);
  const [ramMb, setRamMb] = useState(vm.ram_mb);
  const [diskGb, setDiskGb] = useState(vm.disk_gb);
  const [networkMode, setNetworkMode] = useState<"user" | "bridge">(vm.network_mode === "bridge" ? "bridge" : "user");
  const [bridgeIface, setBridgeIface] = useState(vm.bridge_iface || "br0");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setLoading(true);
    setError(null);
    try {
      await api.vmsPatch(vm.vm_id, { cpu, ram_mb: ramMb, disk_gb: diskGb, network_mode: networkMode, bridge_iface: bridgeIface });
      onUpdated();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="card w-full max-w-sm mx-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold flex items-center gap-2"><Pencil className="w-4 h-4" /> VM bearbeiten — {vm.name}</h2>
          <button className="btn btn-sm" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">CPU (Kerne)</label>
              <input type="number" className="w-full px-3 py-2 border rounded-lg text-sm" value={cpu} min={1} max={16} onChange={e => setCpu(Number(e.target.value))} />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">RAM</label>
              <select className="w-full px-3 py-2 border rounded-lg text-sm" value={ramMb} onChange={e => setRamMb(Number(e.target.value))}>
                {RAM_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Disk (GB) — nur vergrößern möglich</label>
            <input type="number" className="w-full px-3 py-2 border rounded-lg text-sm" value={diskGb} min={vm.disk_gb} max={500} onChange={e => setDiskGb(Number(e.target.value))} />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Netzwerk</label>
            <select className="w-full px-3 py-2 border rounded-lg text-sm" value={networkMode} onChange={e => setNetworkMode(e.target.value as "user" | "bridge")}>
              <option value="user">NAT (intern, 10.0.2.x)</option>
              <option value="bridge">Bridge (Router-DHCP, eigene IP)</option>
            </select>
          </div>
          {networkMode === "bridge" && (
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Bridge-Interface</label>
              <input
                className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                value={bridgeIface}
                onChange={e => setBridgeIface(e.target.value.replace(/[^a-zA-Z0-9_\-]/g, ""))}
                placeholder="br0"
                maxLength={15}
              />
            </div>
          )}
          {error && (
            <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 dark:bg-red-950/50 rounded p-2">
              <AlertCircle className="w-4 h-4 shrink-0" />{error}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button className="btn" onClick={onClose} disabled={loading}>Abbrechen</button>
            <button className="btn btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={loading}>
              {loading && <Loader2 className="w-4 h-4 animate-spin" />}Speichern
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── CreateVMModal ──────────────────────────────────────────────────────────────
interface CreateVMModalProps {
  isos: ISOInfo[];
  onClose: () => void;
  onCreated: () => void;
}

const RAM_OPTIONS = [
  { label: "512 MB",  value: 512 },
  { label: "1 GB",    value: 1024 },
  { label: "2 GB",    value: 2048 },
  { label: "4 GB",    value: 4096 },
  { label: "8 GB",    value: 8192 },
  { label: "16 GB",   value: 16384 },
  { label: "32 GB",   value: 32768 },
];

function CreateVMModal({ isos, onClose, onCreated }: CreateVMModalProps) {
  const [step, setStep] = useState(1);
  const [selectedIso, setSelectedIso] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [cpu, setCpu] = useState(2);
  const [ramMb, setRamMb] = useState(2048);
  const [diskGb, setDiskGb] = useState(20);
  const [networkMode, setNetworkMode] = useState<"user" | "bridge">("user");
  const [bridgeIface, setBridgeIface] = useState("br0");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setLoading(true);
    setError(null);
    try {
      await api.vmsCreate({ name, cpu, ram_mb: ramMb, disk_gb: diskGb, iso_file: selectedIso, network_mode: networkMode, bridge_iface: bridgeIface });
      onCreated();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="card w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Neue VM erstellen</h2>
          <button className="btn btn-sm" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-1 mb-4 text-sm text-muted-foreground">
          {[1, 2, 3].map(s => (
            <span key={s} className={`flex items-center gap-1 ${step === s ? "text-primary font-medium" : ""}`}>
              <span className={`w-5 h-5 rounded-full border flex items-center justify-center text-xs ${step === s ? "border-primary text-primary" : "border-muted"}`}>{s}</span>
              {s < 3 && <ChevronRight className="w-3 h-3" />}
            </span>
          ))}
        </div>

        {/* Step 1 — ISO */}
        {step === 1 && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">Boot-Medium auswählen:</p>
            <label className="flex items-center gap-2 p-3 border rounded-lg cursor-pointer hover:bg-muted/50">
              <input type="radio" name="iso" value="" checked={selectedIso === null} onChange={() => setSelectedIso(null)} />
              <span className="text-sm">Ohne ISO starten</span>
            </label>
            {isos.map(iso => (
              <label key={iso.filename} className="flex items-center gap-2 p-3 border rounded-lg cursor-pointer hover:bg-muted/50">
                <input type="radio" name="iso" value={iso.filename} checked={selectedIso === iso.filename} onChange={() => setSelectedIso(iso.filename)} />
                <span className="text-sm flex-1">{iso.filename}</span>
                <span className="text-xs text-muted-foreground">{iso.size_human}</span>
              </label>
            ))}
            <div className="flex justify-end pt-2">
              <button className="btn btn-primary" onClick={() => setStep(2)} disabled={isos.length === 0 && selectedIso !== null ? false : false}>
                Weiter <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Step 2 — Resources */}
        {step === 2 && (
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Name</label>
              <input
                className="w-full px-3 py-2 border rounded-lg text-sm"
                value={name}
                onChange={e => setName(e.target.value.replace(/[^a-zA-Z0-9_\-]/g, ""))}
                placeholder="meine-vm"
                maxLength={64}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">CPU (Kerne)</label>
                <input type="number" className="w-full px-3 py-2 border rounded-lg text-sm" value={cpu} min={1} max={16} onChange={e => setCpu(Number(e.target.value))} />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">RAM</label>
                <select className="w-full px-3 py-2 border rounded-lg text-sm" value={ramMb} onChange={e => setRamMb(Number(e.target.value))}>
                  {RAM_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Disk (GB)</label>
              <input type="number" className="w-full px-3 py-2 border rounded-lg text-sm" value={diskGb} min={5} max={500} onChange={e => setDiskGb(Number(e.target.value))} />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Netzwerk</label>
              <select className="w-full px-3 py-2 border rounded-lg text-sm" value={networkMode} onChange={e => setNetworkMode(e.target.value as "user" | "bridge")}>
                <option value="user">NAT (intern, 10.0.2.x)</option>
                <option value="bridge">Bridge (Router-DHCP, eigene IP)</option>
              </select>
            </div>
            {networkMode === "bridge" && (
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Bridge-Interface</label>
                <input
                  className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                  value={bridgeIface}
                  onChange={e => setBridgeIface(e.target.value.replace(/[^a-zA-Z0-9_\-]/g, ""))}
                  placeholder="br0"
                  maxLength={15}
                />
                <p className="text-xs text-muted-foreground mt-1">Bridge muss auf dem Host existieren (z.B. br0)</p>
              </div>
            )}
            <div className="flex justify-between pt-2">
              <button className="btn" onClick={() => setStep(1)}>Zurück</button>
              <button className="btn btn-primary" onClick={() => setStep(3)} disabled={!name.trim()}>Weiter</button>
            </div>
          </div>
        )}

        {/* Step 3 — Confirm */}
        {step === 3 && (
          <div className="space-y-3">
            <div className="border rounded-lg p-3 space-y-1 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">Name</span><span className="font-medium">{name}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">CPU</span><span>{cpu} Kerne</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">RAM</span><span>{RAM_OPTIONS.find(o => o.value === ramMb)?.label ?? `${ramMb} MB`}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Disk</span><span>{diskGb} GB</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">ISO</span><span>{selectedIso ?? "— (keine)"}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Netzwerk</span><span>{networkMode === "bridge" ? `Bridge (${bridgeIface})` : "NAT (10.0.2.x)"}</span></div>
            </div>
            {error && (
              <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 dark:bg-red-950/50 rounded p-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {error}
              </div>
            )}
            <div className="flex justify-between pt-2">
              <button className="btn" onClick={() => setStep(2)} disabled={loading}>Zurück</button>
              <button className="btn btn-primary flex items-center gap-1.5" onClick={handleCreate} disabled={loading}>
                {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                VM erstellen
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── ImportVMModal ──────────────────────────────────────────────────────────────
interface ImportVMModalProps {
  onClose: () => void;
  onCreated: () => void;
}

type ImportStep = "params" | "uploading" | "converting" | "creating" | "error";
type ImportSource = "upload" | "path";

function ImportVMModal({ onClose, onCreated }: ImportVMModalProps) {
  const [step, setStep] = useState<ImportStep>("params");
  const [source, setSource] = useState<ImportSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [serverPath, setServerPath] = useState("");
  const [name, setName] = useState("");
  const [cpu, setCpu] = useState(2);
  const [ramMb, setRamMb] = useState(2048);
  const [uploadPct, setUploadPct] = useState(0);
  const [convertPct, setConvertPct] = useState(0);
  const [networkMode, setNetworkMode] = useState<"user" | "bridge">("user");
  const [bridgeIface, setBridgeIface] = useState("br0");
  const [jobId, setJobId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function cleanup() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }

  async function startImportFromPath() {
    setStep("converting");
    try {
      const d = await api.vmsImportFromPath({ path: serverPath.trim() });
      setJobId(d.job_id);
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep("error");
    }
  }

  async function startImport() {
    if (!name.trim()) return;
    if (source === "path") { await startImportFromPath(); return; }
    if (!file) return;
    setStep("uploading");
    setUploadPct(0);

    const formData = new FormData();
    formData.append("file", file);

    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener("progress", e => {
        if (e.lengthComputable) setUploadPct(Math.round((e.loaded / e.total) * 100));
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const d = JSON.parse(xhr.responseText);
            setJobId(d.job_id);
            resolve();
          } catch {
            reject(new Error("Ungültige Server-Antwort beim Upload"));
          }
        } else {
          try {
            const d = JSON.parse(xhr.responseText);
            reject(new Error(fmtDetail(d.detail, `HTTP ${xhr.status}`)));
          } catch {
            reject(new Error(`Upload fehlgeschlagen (${xhr.status})`));
          }
        }
      });
      xhr.addEventListener("error", () => reject(new Error("Netzwerkfehler beim Upload")));
      xhr.open("POST", "/api/admin/vms/import/upload");
      xhr.withCredentials = true;
      xhr.send(formData);
    }).then(() => {
      setStep("converting");
    }).catch((e: unknown) => {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep("error");
    });
  }

  // Polling während converting
  useEffect(() => {
    if (step !== "converting" || !jobId) return;
    cleanup();
    pollRef.current = setInterval(async () => {
      try {
        const d = await api.vmsImportStatus(jobId);
        setConvertPct(d.progress_pct ?? 0);
        if (d.status === "done") {
          cleanup();
          createVM(jobId);
        } else if (d.status === "error") {
          cleanup();
          setErrorMsg(d.error ?? "Konvertierung fehlgeschlagen");
          setStep("error");
        }
      } catch (e: unknown) {
        cleanup();
        setErrorMsg(e instanceof Error ? e.message : String(e));
        setStep("error");
      }
    }, 2000);
    return cleanup;
  }, [step, jobId]);

  async function createVM(jid: string) {
    setStep("creating");
    try {
      await api.vmsCreate({ name: name.trim(), cpu, ram_mb: ramMb, disk_gb: 20, import_job_id: jid, network_mode: networkMode, bridge_iface: bridgeIface });
      onCreated();
      onClose();
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep("error");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="card w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Upload className="w-5 h-5" /> VM importieren
          </h2>
          <button className="btn btn-sm" onClick={() => { cleanup(); onClose(); }}><X className="w-4 h-4" /></button>
        </div>

        {/* Step: params */}
        {step === "params" && (
          <div className="space-y-3">
            {/* Source tabs */}
            <div className="flex rounded-lg border overflow-hidden text-sm">
              <button
                className={`flex-1 py-1.5 ${source === "upload" ? "bg-primary text-primary-foreground" : "bg-muted/40 text-muted-foreground"}`}
                onClick={() => setSource("upload")}
              >Datei hochladen</button>
              <button
                className={`flex-1 py-1.5 ${source === "path" ? "bg-primary text-primary-foreground" : "bg-muted/40 text-muted-foreground"}`}
                onClick={() => setSource("path")}
              >Serverpfad</button>
            </div>
            {source === "upload" ? (
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Disk-Image (.vdi, .vmdk, .vhd, .vhdx, .raw, .img, .qcow2, .vma, .vma.gz, .vma.zst)</label>
                <input
                  type="file"
                  accept=".vdi,.vmdk,.vhd,.vhdx,.raw,.img,.qcow2,.vma,.gz,.zst"
                  className="w-full text-sm"
                  onChange={e => { const f = e.target.files?.[0]; if (f) setFile(f); }}
                />
                {file && <p className="text-xs text-muted-foreground mt-1">{file.name} ({(file.size / 1024 / 1024 / 1024).toFixed(2)} GB)</p>}
              </div>
            ) : (
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Absoluter Pfad auf dem Server</label>
                <input
                  className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                  value={serverPath}
                  onChange={e => setServerPath(e.target.value)}
                  placeholder="/home/backup.vma.zst"
                />
                <p className="text-xs text-muted-foreground mt-1">Unterstützt: .vma, .vma.zst, .vma.gz, .vdi, .vmdk, .qcow2, .raw</p>
              </div>
            )}
            <div>
              <label className="block text-xs text-muted-foreground mb-1">VM-Name</label>
              <input
                className="w-full px-3 py-2 border rounded-lg text-sm"
                value={name}
                onChange={e => setName(e.target.value.replace(/[^a-zA-Z0-9_\-]/g, ""))}
                placeholder="meine-vm"
                maxLength={64}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">CPU (Kerne)</label>
                <input type="number" className="w-full px-3 py-2 border rounded-lg text-sm" value={cpu} min={1} max={16} onChange={e => setCpu(Number(e.target.value))} />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">RAM</label>
                <select className="w-full px-3 py-2 border rounded-lg text-sm" value={ramMb} onChange={e => setRamMb(Number(e.target.value))}>
                  {RAM_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Netzwerk</label>
              <select className="w-full px-3 py-2 border rounded-lg text-sm" value={networkMode} onChange={e => setNetworkMode(e.target.value as "user" | "bridge")}>
                <option value="user">NAT (intern, 10.0.2.x)</option>
                <option value="bridge">Bridge (Router-DHCP, eigene IP)</option>
              </select>
            </div>
            {networkMode === "bridge" && (
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Bridge-Interface</label>
                <input
                  className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                  value={bridgeIface}
                  onChange={e => setBridgeIface(e.target.value.replace(/[^a-zA-Z0-9_\-]/g, ""))}
                  placeholder="br0"
                  maxLength={15}
                />
              </div>
            )}
            <div className="flex justify-between pt-2">
              <button className="btn" onClick={onClose}>Abbrechen</button>
              <button className="btn btn-primary flex items-center gap-2" onClick={startImport}
                disabled={!name.trim() || (source === "upload" ? !file : !serverPath.trim())}>
                <Upload className="w-4 h-4" /> Importieren
              </button>
            </div>
          </div>
        )}

        {/* Step: uploading */}
        {step === "uploading" && (
          <div className="space-y-4 py-4">
            <p className="text-sm text-center text-muted-foreground">
              {uploadPct < 100 ? "Datei wird hochgeladen…" : "Server speichert Datei, bitte warten…"}
            </p>
            <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-primary transition-all" style={{ width: `${uploadPct}%` }} />
            </div>
            <p className="text-center text-sm font-medium">
              {uploadPct < 100 ? `${uploadPct}%` : <Loader2 className="w-4 h-4 animate-spin inline" />}
            </p>
          </div>
        )}

        {/* Step: converting */}
        {step === "converting" && (
          <div className="space-y-4 py-4">
            <p className="text-sm text-center text-muted-foreground">
              {convertPct < 10 ? "Dekomprimiere…" : convertPct < 80 ? "Extrahiere VMA…" : "Konvertiere zu QCOW2…"}
            </p>
            <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-primary transition-all" style={{ width: `${convertPct}%` }} />
            </div>
            <p className="text-center text-sm font-medium">{convertPct}%</p>
          </div>
        )}

        {/* Step: creating */}
        {step === "creating" && (
          <div className="flex items-center justify-center gap-3 py-8 text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm">VM wird erstellt…</span>
          </div>
        )}

        {/* Step: error */}
        {step === "error" && (
          <div className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 dark:bg-red-950/50 rounded p-3">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{errorMsg ?? "Unbekannter Fehler"}</span>
            </div>
            <div className="flex justify-between pt-2">
              <button className="btn" onClick={onClose}>Schließen</button>
              <button className="btn btn-primary" onClick={() => { setStep("params"); setErrorMsg(null); setJobId(null); }}>
                Nochmal versuchen
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── VMsPage ────────────────────────────────────────────────────────────────────
export function VMsPage() {
  const { isAdmin } = useAuth();

  const [vms, setVms] = useState<VMInfo[]>([]);
  const [isos, setIsos] = useState<ISOInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"vms" | "isos">("vms");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [editNetworkVm, setEditNetworkVm] = useState<VMInfo | null>(null);
  const [logVm, setLogVm] = useState<VMInfo | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchVms = useCallback(async () => {
    try {
      const data = await api.vmsList();
      setVms(Array.isArray(data) ? data : data.vms ?? data.items ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const fetchIsos = useCallback(async () => {
    try {
      const data = await api.vmsIsoList();
      setIsos(Array.isArray(data) ? data : data.isos ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchVms(), fetchIsos()]).finally(() => setLoading(false));
  }, [fetchVms, fetchIsos]);

  // Polling für starting/stopping VMs
  useEffect(() => {
    const pending = vms.filter(v => v.status === "starting" || v.status === "stopping");
    if (pending.length > 0 && !pollingRef.current) {
      pollingRef.current = setInterval(fetchVms, 5000);
    } else if (pending.length === 0 && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, [vms, fetchVms]);

  async function handleAction(vmId: string, action: "start" | "stop" | "poweroff" | "delete") {
    setActionLoading(prev => ({ ...prev, [vmId]: true }));
    try {
      if (action === "delete") await api.vmsDelete(vmId);
      else if (action === "start")    await api.vmsStart(vmId);
      else if (action === "stop")     await api.vmsStop(vmId);
      else if (action === "poweroff") await api.vmsPoweroff(vmId);
      await fetchVms();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionLoading(prev => { const n = { ...prev }; delete n[vmId]; return n; });
    }
  }

  async function handleDeleteIso(filename: string) {
    if (!window.confirm(`ISO "${filename}" wirklich löschen?`)) return;
    try {
      await api.vmsIsoDelete(filename);
      setIsos(prev => prev.filter(i => i.filename !== filename));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleUploadIso(file: File) {
    setUploadError(null);
    setUploadProgress(0);
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener("progress", e => {
      if (e.lengthComputable) setUploadProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.addEventListener("load", () => {
      setUploadProgress(null);
      if (xhr.status >= 200 && xhr.status < 300) {
        fetchIsos();
      } else {
        try { const d = JSON.parse(xhr.responseText); setUploadError(fmtDetail(d.detail, `HTTP ${xhr.status}`)); }
        catch { setUploadError(`Upload fehlgeschlagen (${xhr.status})`); }
      }
    });
    xhr.addEventListener("error", () => { setUploadProgress(null); setUploadError("Netzwerkfehler beim Upload"); });
    xhr.open("POST", "/api/admin/vms/isos/upload");
    xhr.withCredentials = true;
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  }

  const busy = (id: string) => actionLoading[id] ?? false;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Server className="w-6 h-6" /> VMs & ISO-Images
          </h1>
          {!isAdmin && <p className="text-sm text-muted-foreground mt-1">Admin-Rechte erforderlich</p>}
        </div>
        {activeTab === "vms" && isAdmin && (
          <div className="flex items-center gap-2">
            <button
              className="btn btn-secondary flex items-center gap-2"
              onClick={() => setShowImportModal(true)}
            >
              <Upload className="w-4 h-4" /> VM importieren
            </button>
            <button className="btn btn-primary flex items-center gap-2" onClick={() => setShowCreateModal(true)}>
              <Plus className="w-4 h-4" /> Neue VM
            </button>
          </div>
        )}
      </div>

      {/* Error Banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/50 text-sm">
          <AlertCircle className="w-4 h-4 text-red-600 shrink-0" />
          <span>{error}</span>
          <button className="ml-auto btn btn-sm" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${activeTab === "vms" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("vms")}
        >
          VMs ({vms.length})
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${activeTab === "isos" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("isos")}
        >
          ISO-Images ({isos.length})
        </button>
      </div>

      {/* ── VM Tab ── */}
      {activeTab === "vms" && (
        loading ? (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="w-6 h-6 animate-spin mr-2" /> Lade VMs…
          </div>
        ) : vms.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <Server className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p>Noch keine VMs — erste VM erstellen</p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {vms.map(vm => (
              <div key={vm.vm_id} className="card flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm truncate">{vm.name}</span>
                  <StatusBadge status={vm.status} />
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                  <div className="flex items-center gap-1"><Cpu className="w-3.5 h-3.5" />{vm.cpu} CPU</div>
                  <div className="flex items-center gap-1"><HardDrive className="w-3.5 h-3.5" />{vm.disk_gb} GB</div>
                  <div className="flex items-center gap-1"><Activity className="w-3.5 h-3.5" />{(vm.ram_mb / 1024).toFixed(1)} GB</div>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Network className="w-3.5 h-3.5" />
                  {vm.network_mode === "bridge" ? `Bridge (${vm.bridge_iface || "br0"})` : "NAT (10.0.2.x)"}
                </div>
                {vm.vnc_port && vm.status === "running" && (
                  <div className="text-xs text-muted-foreground">VNC: Port {vm.vnc_port}</div>
                )}
                <div className="flex items-center gap-2 mt-auto">
                  {vm.status === "running" && (
                    <>
                      <button
                        className="btn btn-sm flex items-center gap-1"
                        onClick={() => window.open(`/vms/${vm.vm_id}/console`, `hh-vm-console-${vm.vm_id}`)}
                        disabled={busy(vm.vm_id)}
                      >
                        <Monitor className="w-3.5 h-3.5" />Konsole
                      </button>
                      <button
                        className="btn btn-sm flex items-center gap-1"
                        onClick={() => handleAction(vm.vm_id, "stop")}
                        disabled={busy(vm.vm_id)}
                      >
                        {busy(vm.vm_id) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
                        Stop
                      </button>
                    </>
                  )}
                  {vm.status === "stopped" || vm.status === "created" || vm.status === "error" ? (
                    <>
                      <button
                        className="btn btn-sm btn-primary flex items-center gap-1"
                        onClick={() => handleAction(vm.vm_id, "start")}
                        disabled={busy(vm.vm_id)}
                      >
                        {busy(vm.vm_id) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                        Start
                      </button>
                    </>
                  ) : (vm.status === "starting" || vm.status === "stopping") && (
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />{vm.status === "starting" ? "Startet…" : "Stoppt…"}
                    </span>
                  )}
                  <button
                    className="btn btn-sm flex items-center gap-1 ml-auto"
                    title="QEMU-Log anzeigen"
                    onClick={() => setLogVm(vm)}
                  >
                    <ScrollText className="w-3.5 h-3.5" />
                  </button>
                  {(vm.status === "stopped" || vm.status === "created" || vm.status === "error") && (
                    <button
                      className="btn btn-sm flex items-center gap-1"
                      title="Netzwerk ändern"
                      onClick={() => setEditNetworkVm(vm)}
                      disabled={busy(vm.vm_id)}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    className={`btn btn-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-950 border border-red-200 dark:border-red-800 ${vm.status === "running" ? "ml-auto" : ""}`}
                    onClick={() => { if (window.confirm(`VM "${vm.name}" wirklich löschen?`)) handleAction(vm.vm_id, "delete"); }}
                    disabled={busy(vm.vm_id) || vm.status === "starting" || vm.status === "stopping"}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* ── ISO Tab ── */}
      {activeTab === "isos" && (
        <div className="space-y-4">
          {/* Upload Area */}
          {isAdmin && (
            <div className="card">
              <h3 className="text-sm font-medium mb-3">ISO hochladen</h3>
              <div className="flex items-center gap-3">
                <input
                  type="file"
                  accept=".iso"
                  id="iso-upload"
                  className="hidden"
                  onChange={e => { const f = e.target.files?.[0]; if (f) handleUploadIso(f); e.target.value = ""; }}
                />
                <label htmlFor="iso-upload" className="btn btn-sm cursor-pointer flex items-center gap-2">
                  <Upload className="w-4 h-4" /> ISO-Datei wählen
                </label>
                {uploadProgress !== null && (
                  <div className="flex items-center gap-2 flex-1">
                    <div className="h-2 flex-1 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-primary transition-all" style={{ width: `${uploadProgress}%` }} />
                    </div>
                    <span className="text-xs text-muted-foreground w-10">{uploadProgress}%</span>
                  </div>
                )}
              </div>
              {uploadError && <p className="text-xs text-red-500 mt-2">{uploadError}</p>}
            </div>
          )}

          {/* ISO List */}
          {isos.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <HardDrive className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p>Noch keine ISO-Images</p>
            </div>
          ) : (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-muted-foreground">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium">Datei</th>
                    <th className="text-left px-4 py-2 font-medium">Größe</th>
                    <th className="text-left px-4 py-2 font-medium">Hochgeladen</th>
                    {isAdmin && <th className="text-right px-4 py-2 font-medium">Aktion</th>}
                  </tr>
                </thead>
                <tbody>
                  {isos.map(iso => (
                    <tr key={iso.filename} className="border-t hover:bg-muted/30">
                      <td className="px-4 py-2 font-medium">{iso.filename}</td>
                      <td className="px-4 py-2 text-muted-foreground">{iso.size_human}</td>
                      <td className="px-4 py-2 text-muted-foreground">{new Date(iso.uploaded_at * 1000).toLocaleDateString("de-DE")}</td>
                      {isAdmin && (
                        <td className="px-4 py-2 text-right">
                          <button className="btn btn-sm text-red-500 hover:bg-red-50" onClick={() => handleDeleteIso(iso.filename)}>
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateVMModal
          isos={isos}
          onClose={() => setShowCreateModal(false)}
          onCreated={() => { setShowCreateModal(false); fetchVms(); }}
        />
      )}

      {/* Import Modal */}
      {showImportModal && (
        <ImportVMModal
          onClose={() => setShowImportModal(false)}
          onCreated={() => { setShowImportModal(false); fetchVms(); }}
        />
      )}

      {/* Log Modal */}
      {logVm && <VMLogModal vm={logVm} onClose={() => setLogVm(null)} />}

      {/* Edit Network Modal */}
      {editNetworkVm && (
        <EditNetworkModal
          vm={editNetworkVm}
          onClose={() => setEditNetworkVm(null)}
          onUpdated={() => { setEditNetworkVm(null); fetchVms(); }}
        />
      )}
    </div>
  );
}
