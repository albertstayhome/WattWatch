from __future__ import annotations

import copy
import datetime
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:
    winreg = None

IS_FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WattWatch") if IS_FROZEN else (BASE_DIR / "data")
CONFIG_PATH = DATA_DIR / "config.json"
VBS_LAUNCHER = BASE_DIR / "啟動_WattWatch.vbs"
TASK_NAME = "WattWatch"
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_NAME = "WattWatch"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TAIPOWER_PRESETS: dict[str, dict[str, Any]] = {
    "taipower_nonsummer_t1": {"label": "非夏月 120度以下 (1.78元)", "rate": 1.78, "season": "非夏月"},
    "taipower_nonsummer_t2": {"label": "非夏月 121~330度 (2.26元)", "rate": 2.26, "season": "非夏月"},
    "taipower_nonsummer_mid": {"label": "非夏月 331~500度 (3.13元)", "rate": 3.13, "season": "非夏月"},
    "taipower_nonsummer_t4": {"label": "非夏月 501~700度 (4.24元)", "rate": 4.24, "season": "非夏月"},
    "taipower_nonsummer_t5": {"label": "非夏月 701~1000度 (5.27元)", "rate": 5.27, "season": "非夏月"},
    "taipower_nonsummer_t6": {"label": "非夏月 1001度以上 (7.03元)", "rate": 7.03, "season": "非夏月"},
    "taipower_summer_t1": {"label": "夏月 120度以下 (1.78元)", "rate": 1.78, "season": "夏月"},
    "taipower_summer_t2": {"label": "夏月 121~330度 (2.55元)", "rate": 2.55, "season": "夏月"},
    "taipower_summer_mid": {"label": "夏月 331~500度 (3.80元)", "rate": 3.80, "season": "夏月"},
    "taipower_summer_t4": {"label": "夏月 501~700度 (5.14元)", "rate": 5.14, "season": "夏月"},
    "taipower_summer_t5": {"label": "夏月 701~1000度 (6.44元)", "rate": 6.44, "season": "夏月"},
    "taipower_summer_t6": {"label": "夏月 1001度以上 (8.86元)", "rate": 8.86, "season": "夏月"},
    "rental_flat_5": {"label": "租屋常見一度5元 (5.00元)", "rate": 5.00, "season": "固定"},
}

PSU_TIERS: dict[str, dict[str, Any]] = {
    "standard": {"label": "一般無標示 / OEM (約 78%)", "nominal": 0.78},
    "white": {"label": "80 PLUS 白牌 (約 82%)", "nominal": 0.82},
    "bronze": {"label": "80 PLUS 銅牌 (約 85%)", "nominal": 0.85},
    "gold": {"label": "80 PLUS 金牌 (約 89%)", "nominal": 0.89},
    "platinum": {"label": "80 PLUS 白金牌 (約 92%)", "nominal": 0.92},
    "titanium": {"label": "80 PLUS 鈦金牌 (約 94%)", "nominal": 0.94},
}


def default_tariff_for_month(month: int | None = None) -> tuple[str, float]:
    m = month if month is not None else datetime.datetime.now().month
    if 6 <= m <= 9:
        return "taipower_summer_mid", 3.80
    return "taipower_nonsummer_mid", 3.13


def build_default_config() -> dict[str, Any]:
    preset, rate = default_tariff_for_month()
    return {
        "tariff_mode": "tou_2tier",
        "electricity_rate_ntd": rate,
        "tariff_preset": preset,
        "psu_efficiency_tier": "bronze",
        "psu_rated_watts": 650,
        "idle_waste_threshold_sec": 300,
        "monitor_overrides": {},
        "external_devices": [],
        "port": 18742,
    }


def _cleanup_legacy_run_key() -> None:
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, RUN_REG_NAME)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def get_autostart() -> bool:
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    _cleanup_legacy_run_key()
    try:
        if enabled:
            task_cmd = f'"{sys.executable}" --no-browser' if IS_FROZEN else f'wscript.exe "{str(VBS_LAUNCHER)}" --no-browser'
            proc = subprocess.run(
                ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/RL", "HIGHEST", "/F", "/TR", task_cmd],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode == 0:
                return True, "ok"
            return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        else:
            if not get_autostart():
                return True, "ok"
            proc = subprocess.run(
                ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode == 0 or not get_autostart():
                return True, "ok"
            return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    except Exception as exc:
        return False, str(exc)


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_PATH
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        defaults = build_default_config()
        if not self.path.exists():
            self._save_raw(defaults)
            return defaults
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return defaults
        except Exception:
            return defaults

        merged = copy.deepcopy(defaults)
        for k, v in raw.items():
            if k in merged:
                if isinstance(merged[k], dict) and isinstance(v, dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
        return merged

    def _save_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self) -> dict[str, Any]:
        with self._lock:
            snap = copy.deepcopy(self._data)
            snap["autostart"] = get_autostart()
            return snap

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if "tariff_mode" in patch:
                tm = str(patch["tariff_mode"]).lower()
                if tm in ("tou_2tier", "fixed"):
                    self._data["tariff_mode"] = tm

            if "tariff_preset" in patch:
                preset_key = str(patch["tariff_preset"])
                if preset_key == "tou_2tier":
                    self._data["tariff_mode"] = "tou_2tier"
                elif preset_key in TAIPOWER_PRESETS:
                    self._data["tariff_preset"] = preset_key
                    if "electricity_rate_ntd" not in patch:
                        self._data["electricity_rate_ntd"] = TAIPOWER_PRESETS[preset_key]["rate"]
                elif preset_key == "custom":
                    self._data["tariff_preset"] = "custom"

            if "electricity_rate_ntd" in patch:
                rate = float(patch["electricity_rate_ntd"])
                self._data["electricity_rate_ntd"] = round(max(0.1, min(50.0, rate)), 4)

            if "psu_efficiency_tier" in patch:
                tier = str(patch["psu_efficiency_tier"]).lower()
                if tier in PSU_TIERS:
                    self._data["psu_efficiency_tier"] = tier

            if "psu_rated_watts" in patch:
                w = int(float(patch["psu_rated_watts"]))
                self._data["psu_rated_watts"] = max(150, min(2500, w))

            if "idle_waste_threshold_sec" in patch:
                sec = int(float(patch["idle_waste_threshold_sec"]))
                self._data["idle_waste_threshold_sec"] = max(30, min(7200, sec))

            if "monitor_overrides" in patch and isinstance(patch["monitor_overrides"], dict):
                overrides = self._data.setdefault("monitor_overrides", {})
                for mon_id, mon_cfg in patch["monitor_overrides"].items():
                    if not isinstance(mon_cfg, dict):
                        continue
                    cur = overrides.get(mon_id, {})
                    if "panel_type" in mon_cfg and mon_cfg["panel_type"] in ("IPS", "VA", "TN", "OLED", "Mini-LED"):
                        cur["panel_type"] = mon_cfg["panel_type"]
                    if "manual_brightness" in mon_cfg:
                        b = int(float(mon_cfg["manual_brightness"]))
                        cur["manual_brightness"] = max(0, min(100, b))
                    if "custom_watts" in mon_cfg:
                        cw = mon_cfg["custom_watts"]
                        cur["custom_watts"] = None if cw in (None, "", 0, "0") else round(max(1.0, min(500.0, float(cw))), 2)
                    if "display_name" in mon_cfg:
                        cur["display_name"] = str(mon_cfg["display_name"])[:80]
                    overrides[mon_id] = cur

            if "external_devices" in patch and isinstance(patch["external_devices"], list):
                cleaned = []
                for item in patch["external_devices"]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    watts = round(max(0.1, min(3000.0, float(item.get("watts", 0)))), 2)
                    cleaned.append(
                        {
                            "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                            "name": name[:60],
                            "watts": watts,
                            "enabled": bool(item.get("enabled", True)),
                        }
                    )
                self._data["external_devices"] = cleaned

            if "autostart" in patch:
                set_autostart(bool(patch["autostart"]))

            self._save_raw(self._data)
            return self.get()
