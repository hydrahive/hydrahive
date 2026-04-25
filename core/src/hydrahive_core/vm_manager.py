"""
HydraHive VM-Manager — QEMU/KVM VMs via Python (#895)
===================================================================
async-first, aiosqlite-backed, keine shell=True subprocess calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .vnc_proxy import VNCProxy

logger = logging.getLogger(__name__)

# ── Status-Konstanten ─────────────────────────────────────────────────────────
VM_STATUS_CREATED = "created"
VM_STATUS_STARTING = "starting"
VM_STATUS_RUNNING = "running"
VM_STATUS_STOPPING = "stopping"
VM_STATUS_STOPPED = "stopped"
VM_STATUS_ERROR = "error"

# ── Dataclass ─────────────────────────────────────────────────────────────────
@dataclass
class VMConfig:
    vm_id: str
    name: str
    cpu: int
    ram_mb: int
    disk_gb: int
    iso_file: str | None
    status: str
    pid: int | None
    vnc_port: int | None
    vnc_token: str | None
    owner: str
    created_at: float
    disk_path: str
    network_mode: str = "user"    # "user" (NAT) oder "bridge"
    bridge_iface: str = "br0"     # Bridge-Interface für network_mode=bridge

    def to_dict(self) -> dict:
        return asdict(self)


# ── VMManager ─────────────────────────────────────────────────────────────────
class VMManager:
    def __init__(self, storage_base: Path, db_path: Path):
        self._storage_base = storage_base
        self._iso_dir = storage_base / "isos"
        self._vms_dir = storage_base / "vms"
        self._vnc_token_dir = storage_base / "vnc-tokens"
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._vnc_proxy = VNCProxy(self._vnc_token_dir)

    async def _init_db(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS vms (
                vm_id         TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                cpu           INTEGER NOT NULL,
                ram_mb        INTEGER NOT NULL,
                disk_gb       INTEGER NOT NULL,
                iso_file      TEXT,
                status        TEXT NOT NULL DEFAULT 'created',
                pid           INTEGER,
                vnc_port      INTEGER,
                vnc_token     TEXT,
                owner         TEXT NOT NULL,
                created_at    REAL NOT NULL,
                disk_path     TEXT NOT NULL,
                network_mode  TEXT NOT NULL DEFAULT 'user',
                bridge_iface  TEXT NOT NULL DEFAULT 'br0'
            )
        """)
        # Migration: Spalten nachrüsten falls DB älter ist
        for col, default in [("network_mode", "'user'"), ("bridge_iface", "'br0'")]:
            try:
                await self._db.execute(f"ALTER TABLE vms ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            except Exception:
                pass
        await self._db.commit()

    def _row_to_vm(self, row: aiosqlite.Row) -> VMConfig:
        return VMConfig(
            vm_id=row["vm_id"],
            name=row["name"],
            cpu=row["cpu"],
            ram_mb=row["ram_mb"],
            disk_gb=row["disk_gb"],
            iso_file=row["iso_file"],
            status=row["status"],
            pid=row["pid"],
            vnc_port=row["vnc_port"],
            vnc_token=row["vnc_token"],
            owner=row["owner"],
            created_at=row["created_at"],
            disk_path=row["disk_path"],
            network_mode=row["network_mode"] if "network_mode" in row.keys() else "user",
            bridge_iface=row["bridge_iface"] if "bridge_iface" in row.keys() else "br0",
        )

    async def create_vm(
        self,
        name: str,
        cpu: int,
        ram_mb: int,
        disk_gb: int,
        iso_file: str | None,
        owner: str,
        import_disk_path: str | None = None,
        network_mode: str = "user",
        bridge_iface: str = "br0",
    ) -> VMConfig:
        """Erstellt eine neue VM mit QCOW2-Disk."""
        await self._init_db()
        if not name or not name.strip():
            raise ValueError("VM name must not be empty")
        if cpu < 1:
            raise ValueError("cpu must be >= 1")
        if ram_mb < 256:
            raise ValueError("ram_mb must be >= 256")

        vm_id = uuid.uuid4().hex
        vm_dir = self._vms_dir / vm_id
        vm_dir.mkdir(parents=True, exist_ok=True)

        if import_disk_path:
            # Importierte Disk verschieben (rename, kein Copy)
            import_src = Path(import_disk_path)
            disk_path = vm_dir / "disk.qcow2"
            import_src.rename(disk_path)
            # disk_gb aus der tatsächlichen Disk-Größe ermitteln
            actual_size = disk_path.stat().st_size
            disk_gb = max(1, actual_size // (1024 ** 3))
        else:
            # Neue leere Disk erstellen
            disk_path = vm_dir / "disk.qcow2"
            proc = await asyncio.create_subprocess_exec(
                "qemu-img", "create", "-f", "qcow2", str(disk_path), f"{disk_gb}G",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                shutil.rmtree(vm_dir, ignore_errors=True)
                raise RuntimeError(f"qemu-img create failed: {stderr.decode().strip()}")

        vm = VMConfig(
            vm_id=vm_id,
            name=name.strip(),
            cpu=cpu,
            ram_mb=ram_mb,
            disk_gb=disk_gb,
            iso_file=iso_file,
            status=VM_STATUS_CREATED,
            pid=None,
            vnc_port=None,
            vnc_token=None,
            owner=owner,
            created_at=time.time(),
            disk_path=str(disk_path),
            network_mode=network_mode,
            bridge_iface=bridge_iface,
        )

        await self._db.execute(
            """INSERT INTO vms
               (vm_id, name, cpu, ram_mb, disk_gb, iso_file, status, pid, vnc_port, vnc_token, owner, created_at, disk_path, network_mode, bridge_iface)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vm.vm_id, vm.name, vm.cpu, vm.ram_mb, vm.disk_gb, vm.iso_file,
             vm.status, vm.pid, vm.vnc_port, vm.vnc_token, vm.owner, vm.created_at, vm.disk_path,
             vm.network_mode, vm.bridge_iface),
        )
        await self._db.commit()
        logger.info("VM erstellt: %s (%s)", name, vm_id)
        return vm

    async def start_vm(self, vm_id: str) -> VMConfig:
        """Startet eine VM und weist VNC-Port + Token zu."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        if not vm_row:
            raise ValueError(f"VM nicht gefunden: {vm_id}")

        vm = self._row_to_vm(vm_row)
        if vm.status not in (VM_STATUS_CREATED, VM_STATUS_STOPPED, VM_STATUS_ERROR):
            raise RuntimeError(f"VM {vm_id} kann nicht gestartet werden (status={vm.status})")

        # VNC-Port finden
        vnc_port = self._find_free_vnc_port()
        vnc_display = vnc_port - 5900  # display number

        # Token über VNCProxy registrieren
        token = self._vnc_proxy.register(vm_id, vnc_port)

        # QEMU-Kommando bauen und starten
        cmd = self._build_qemu_cmd(vm, vnc_display)
        vm_dir = Path(vm.disk_path).parent
        pidfile = vm_dir / "qemu.pid"
        log_path = vm_dir / "qemu.log"

        # Log-Datei statt PIPE: QEMU überlebt Core-Neustarts (kein SIGPIPE bei geschlossener Pipe)
        log_fd = open(log_path, "wb")
        full_cmd = cmd + ["-pidfile", str(pidfile)]
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,  # eigene Session → kein SIGHUP bei Parent-Tod
        )
        log_fd.close()  # Parent schließt seinen Fd; QEMU hält seinen offen

        # 1.5s warten und prüfen ob QEMU noch lebt
        await asyncio.sleep(1.5)
        if proc.returncode is not None:
            err = log_path.read_text(errors="replace").strip()[-300:] if log_path.exists() else "unbekannter Fehler"
            self._vnc_proxy.unregister(vm_id)
            await self._db.execute(
                "UPDATE vms SET status=? WHERE vm_id=?",
                (VM_STATUS_ERROR, vm_id),
            )
            await self._db.commit()
            raise RuntimeError(f"QEMU beendet sich sofort (rc={proc.returncode}): {err}")

        pid_str = pidfile.read_text().strip() if pidfile.exists() else ""
        pid = int(pid_str) if pid_str.isdigit() else proc.pid

        # Status updaten
        await self._db.execute(
            "UPDATE vms SET status=?, pid=?, vnc_port=?, vnc_token=? WHERE vm_id=?",
            (VM_STATUS_RUNNING, pid, vnc_port, token, vm_id),
        )
        await self._db.commit()
        vm.status = VM_STATUS_RUNNING
        vm.pid = pid
        vm.vnc_port = vnc_port
        vm.vnc_token = token
        logger.info("VM gestartet: %s (pid=%s, vnc_port=%d)", vm_id, pid, vnc_port)
        return vm

    async def stop_vm(self, vm_id: str, force: bool = False) -> VMConfig:
        """Stoppt eine VM (SIGTERM, dann SIGKILL nach 5s)."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        if not vm_row:
            raise ValueError(f"VM nicht gefunden: {vm_id}")

        vm = self._row_to_vm(vm_row)
        if vm.status not in (VM_STATUS_RUNNING,):
            return vm

        vm.status = VM_STATUS_STOPPING
        await self._db.execute(
            "UPDATE vms SET status=? WHERE vm_id=?",
            (VM_STATUS_STOPPING, vm_id),
        )
        await self._db.commit()

        if vm.pid:
            try:
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(vm.pid, sig)
                if not force:
                    time.sleep(5)
                    try:
                        os.kill(vm.pid, 0)  # check if still alive
                        os.kill(vm.pid, signal.SIGKILL)
                    except OSError:
                        pass
            except OSError as e:
                logger.warning("stop_vm %s: signal fehlgeschlagen: %s", vm_id, e)

        # VNC-Token aufräumen
        self._vnc_proxy.unregister(vm_id)

        await self._db.execute(
            "UPDATE vms SET status=?, pid=NULL, vnc_port=NULL, vnc_token=NULL WHERE vm_id=?",
            (VM_STATUS_STOPPED, vm_id),
        )
        await self._db.commit()
        vm.status = VM_STATUS_STOPPED
        vm.pid = None
        vm.vnc_port = None
        vm.vnc_token = None
        logger.info("VM gestoppt: %s", vm_id)
        return vm

    async def delete_vm(self, vm_id: str) -> None:
        """Löscht eine VM (stop + Verzeichnis + DB-Eintrag)."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        if vm_row:
            vm = self._row_to_vm(vm_row)
            if vm.status == VM_STATUS_RUNNING:
                await self.stop_vm(vm_id, force=True)
        else:
            # Auch ohne DB-Eintrag das Verzeichnis löschen
            pass

        vm_dir = self._vms_dir / vm_id
        if vm_dir.exists():
            shutil.rmtree(vm_dir)
        # VNC-Token aufräumen (auch ohne aktive VM)
        self._vnc_proxy.unregister(vm_id)
        await self._db.execute("DELETE FROM vms WHERE vm_id = ?", (vm_id,))
        await self._db.commit()
        logger.info("VM gelöscht: %s", vm_id)

    async def get_vm(self, vm_id: str) -> VMConfig | None:
        """Lädt eine VM nach ID."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        return self._row_to_vm(vm_row) if vm_row else None

    async def list_vms(self, owner: str | None = None) -> list[VMConfig]:
        """Listet alle VMs, optional gefiltert nach owner."""
        await self._init_db()
        if owner:
            rows = await self._db.execute(
                "SELECT * FROM vms WHERE owner = ? ORDER BY created_at DESC", (owner,)
            )
        else:
            rows = await self._db.execute("SELECT * FROM vms ORDER BY created_at DESC")
        return [self._row_to_vm(r) async for r in rows]

    async def update_vm(self, vm_id: str, *, network_mode: str | None = None, bridge_iface: str | None = None) -> VMConfig:
        """Ändert konfigurierbare VM-Felder (nur wenn gestoppt/created/error)."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        if not vm_row:
            raise ValueError(f"VM nicht gefunden: {vm_id}")
        vm = self._row_to_vm(vm_row)
        if vm.status not in (VM_STATUS_STOPPED, VM_STATUS_CREATED, VM_STATUS_ERROR):
            raise ValueError(f"VM muss gestoppt sein um geändert zu werden (status={vm.status})")
        if network_mode is not None:
            vm.network_mode = network_mode
        if bridge_iface is not None:
            vm.bridge_iface = bridge_iface
        await self._db.execute(
            "UPDATE vms SET network_mode=?, bridge_iface=? WHERE vm_id=?",
            (vm.network_mode, vm.bridge_iface, vm_id),
        )
        await self._db.commit()
        return vm

    async def refresh_status(self, vm_id: str) -> VMConfig:
        """Prüft ob PID noch lebt, setzt status=error falls nicht."""
        await self._init_db()
        row = await self._db.execute("SELECT * FROM vms WHERE vm_id = ?", (vm_id,))
        vm_row = await row.fetchone()
        if not vm_row:
            raise ValueError(f"VM nicht gefunden: {vm_id}")

        vm = self._row_to_vm(vm_row)
        if vm.status == VM_STATUS_RUNNING and vm.pid:
            try:
                os.kill(vm.pid, 0)  # signal 0 = probe only
            except OSError:
                await self._db.execute(
                    "UPDATE vms SET status=? WHERE vm_id=?",
                    (VM_STATUS_ERROR, vm_id),
                )
                await self._db.commit()
                vm.status = VM_STATUS_ERROR
                logger.warning("VM %s: PID %d nicht mehr in /proc — als error markiert", vm_id, vm.pid)
        return vm

    def _find_free_vnc_port(self) -> int:
        """Findet ersten freien VNC-Port 5900-5999."""
        used: set[int] = set()
        if self._db and self._db in getattr(self, "_open_ports", []):
            # sync wrapper für startup phase
            pass
        # Collect ports from DB via sync query (called from sync context in __init__)
        db_path_str = str(self._db_path)
        try:
            import sqlite3
            con = sqlite3.connect(db_path_str)
            rows = con.execute("SELECT vnc_port FROM vms WHERE vnc_port IS NOT NULL").fetchall()
            used.update(r[0] for r in rows if r[0])
            con.close()
        except Exception:
            pass
        for port in range(5900, 6000):
            if port not in used:
                return port
        raise RuntimeError("Kein freier VNC-Port mehr verfügbar (5900-5999)")

    def _build_qemu_cmd(self, vm: VMConfig, vnc_display: int) -> list[str]:
        """Baut das QEMU-Kommando. KVM wird genutzt wenn /dev/kvm verfügbar, sonst TCG."""
        kvm = Path("/dev/kvm").exists()
        cmd = [
            "qemu-system-x86_64",
            "-m", str(vm.ram_mb),
            "-smp", str(vm.cpu),
            "-drive", f"file={vm.disk_path},format=qcow2",
            "-vnc", f"127.0.0.1:{vnc_display}",
        ]
        # pc (i440FX/PIIX) ist kompatibler mit importierten VMs (VirtualBox-Standard-Chipsatz).
        # q35 (ICH9) ist moderner aber bricht FreeBSD/ältere Gäste beim Import.
        if kvm:
            cmd += ["-enable-kvm", "-machine", "type=pc,accel=kvm", "-cpu", "host"]
        else:
            cmd += ["-machine", "type=pc", "-cpu", "qemu64"]
        if vm.network_mode == "bridge":
            # Bridge-Networking: VM bekommt IP vom Router-DHCP
            # Voraussetzung: Bridge-Interface (br0) auf dem Host existiert
            # und /etc/qemu/bridge.conf erlaubt es (via qemu-bridge-helper)
            cmd += [
                "-device", "virtio-net-pci,netdev=net0",
                "-netdev", f"bridge,id=net0,br={vm.bridge_iface}",
            ]
        else:
            # user (NAT): QEMU-internes Netz 10.0.2.x — kein Router-Zugriff
            cmd += [
                "-device", "virtio-net-pci,netdev=net0",
                "-netdev", "user,id=net0",
            ]
        if vm.iso_file:
            iso_path = Path(vm.iso_file)
            if iso_path.exists():
                cmd += ["-cdrom", str(iso_path), "-boot", "order=dc"]
        # SeaBIOS (QEMU-Default) — kompatibler mit importierten Legacy-BIOS-Disks (VDI/VMDK).
        # OVMF nur wenn explizit angefordert (zukünftiges VM-Setting).
        logger.info("QEMU-Cmd (kvm=%s): %s", kvm, " ".join(cmd))
        return cmd
