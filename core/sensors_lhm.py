from __future__ import annotations

import ctypes
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

from core.config import BASE_DIR, RESOURCE_DIR


def _resolve_bridge_exe() -> Path:
    candidates = [
        RESOURCE_DIR / "vendor" / "lhm" / "LhmBridge.exe",
        BASE_DIR / "vendor" / "lhm" / "LhmBridge.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


BRIDGE_EXE = _resolve_bridge_exe()
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def check_pawnio_installed() -> bool:
    try:
        proc = subprocess.run(
            ["sc", "query", "PawnIO"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _parse_watts_text(val_str: str) -> float | None:
    if not val_str:
        return None
    parts = val_str.strip().split()
    if not parts:
        return None
    try:
        return float(parts[0].replace(",", "."))
    except ValueError:
        return None


def _extract_from_lhm_http_tree(node: dict[str, Any], out: dict[str, Any]) -> None:
    text = str(node.get("Text") or "")
    val_str = str(node.get("Value") or "")
    if text == "CPU Package" and "W" in val_str:
        w = _parse_watts_text(val_str)
        if w is not None:
            out["cpu_power_w"] = w
    elif text == "GPU Power" and "W" in val_str:
        w = _parse_watts_text(val_str)
        if w is not None:
            out.setdefault("gpu_powers", []).append(w)
    for child in node.get("Children", []):
        if isinstance(child, dict):
            _extract_from_lhm_http_tree(child, out)


class LhmSensorBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._http_thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] = {}
        self._last_update_ts: float = 0.0
        self._pawnio_cached: bool | None = None
        self._pawnio_check_ts: float = 0.0
        self._http_fallback_data: dict[str, Any] = {}
        self._http_last_ts: float = 0.0
        self._interval_ms: int = 1000
        self._stdin_lock = threading.Lock()

    def start(self) -> None:
        if self._proc is not None or not BRIDGE_EXE.exists():
            return
        self._stop_event.clear()
        try:
            self._proc = subprocess.Popen(
                [str(BRIDGE_EXE)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(BRIDGE_EXE.parent),
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception:
            self._proc = None
            return

        self._reader_thread = threading.Thread(target=self._read_loop, name="LhmBridgeReader", daemon=True)
        self._reader_thread.start()

        self._http_thread = threading.Thread(target=self._http_fallback_loop, name="LhmHttpProbe", daemon=True)
        self._http_thread.start()

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while not self._stop_event.is_set():
            try:
                line = proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    with self._lock:
                        self._snapshot = obj
                        self._last_update_ts = time.time()
            except Exception:
                continue

    def _http_fallback_loop(self) -> None:
        # Check external LHM Web Server (localhost:8085) every 15s ONLY if bridge lacks cpu_power_w
        while not self._stop_event.is_set():
            snap = self.get_snapshot()
            if snap.get("cpu_power_w") is None:
                try:
                    resp = requests.get("http://127.0.0.1:8085/data.json", timeout=1.0)
                    if resp.status_code == 200:
                        tree = resp.json()
                        parsed: dict[str, Any] = {}
                        _extract_from_lhm_http_tree(tree, parsed)
                        with self._lock:
                            self._http_fallback_data = parsed
                            self._http_last_ts = time.time()
                except Exception:
                    pass
            self._stop_event.wait(15.0)

    def set_interval_ms(self, ms: int) -> None:
        clamped = max(1000, min(10000, int(ms)))
        if clamped == self._interval_ms:
            return
        self._interval_ms = clamped
        with self._stdin_lock:
            proc = self._proc
            if proc is not None and proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(f"interval {clamped}\n")
                    proc.stdin.flush()
                except Exception:
                    pass

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            max_age = max(8.0, (self._interval_ms / 1000.0) * 2.5)
            data = dict(self._snapshot) if (now - self._last_update_ts) < max_age else {}
            if data.get("cpu_power_w") is None and (now - self._http_last_ts) < 20.0:
                if self._http_fallback_data.get("cpu_power_w") is not None:
                    data["cpu_power_w"] = self._http_fallback_data["cpu_power_w"]
                    data["from_http_server"] = True
            return data

    def status(self) -> dict[str, Any]:
        now = time.time()
        if self._pawnio_cached is None or (now - self._pawnio_check_ts) > 30.0:
            self._pawnio_cached = check_pawnio_installed()
            self._pawnio_check_ts = now
        snap = self.get_snapshot()
        bridge_alive = self._proc is not None and self._proc.poll() is None
        cpu_pw = snap.get("cpu_power_w")
        dram_pw = snap.get("dram_power_w")
        return {
            "bridge_running": bridge_alive,
            "cpu_sensor_active": cpu_pw is not None and float(cpu_pw) > 0.5,
            "dram_sensor_active": dram_pw is not None and float(dram_pw) > 0.1,
            "sensor_count": int(snap.get("sensor_count") or 0),
            "pawnio_installed": bool(self._pawnio_cached),
            "is_admin": is_admin(),
        }

    def stop(self) -> None:
        self._stop_event.set()
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.write("quit\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.5)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
