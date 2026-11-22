from __future__ import annotations

import collections
import datetime
import threading
import time
from typing import Any

import psutil

from core.config import ConfigManager, PSU_TIERS
from core.database import (
    estimate_cpu_watts,
    estimate_gpu_watts,
    estimate_monitor_watts,
    get_psu_efficiency,
)
from core.hardware_discovery import discover_all_hardware
from core.sensors_lhm import LhmSensorBridge
from core.sensors_monitor import MonitorSensor, get_user_idle_seconds
from core.sensors_nvml import NvmlSensor
from core.storage import Storage
from core.tariff import get_effective_rate_info

SOURCE_LABELS = {
    "sensor_rapl": "實測 (RAPL感測器)",
    "sensor_nvml": "實測 (NVML感測器)",
    "sensor_lhm": "實測 (硬體感測器)",
    "ddcci_model": "DDC/CI + 物理模型",
    "model": "物理功耗模型估算",
    "calibrated": "使用者手動校準",
}

CONFIDENCE_BADGE = {
    "sensor_rapl": "measured",
    "sensor_nvml": "measured",
    "sensor_lhm": "measured",
    "ddcci_model": "estimated",
    "model": "estimated",
    "calibrated": "calibrated",
}


class PowerEngine:
    def __init__(self, config: ConfigManager, storage: Storage):
        self.config = config
        self.storage = storage
        self.nvml = NvmlSensor()
        self.lhm = LhmSensorBridge()
        self.monitor_sensor = MonitorSensor()

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._loop_wake = threading.Event()
        self._thread: threading.Thread | None = None

        self._ui_clients: int = 0
        self._last_deep_eco: bool = False
        self._power_mode: str = "eco_3s"
        self._poll_interval_sec: float = 3.0

        self.hardware: dict[str, Any] = {}
        self.history_buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=180)
        self._latest_snapshot: dict[str, Any] = {}

        self._session = self._new_accum()
        self._pending_buckets: dict[int, dict[str, Any]] = {}
        self._pending_devices: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_flush_ts: float = time.time()

        self._prev_disk_bytes: int | None = None
        self._prev_disk_ts: float = 0.0

        self._cached_today_base: dict[str, Any] | None = None
        self._cached_today_date: str = ""
        self._cached_today_ts: float = 0.0

    @staticmethod
    def _new_accum() -> dict[str, Any]:
        return {
            "started_ts": time.time(),
            "duration_sec": 0.0,
            "total_wh": 0.0,
            "pc_ac_wh": 0.0,
            "monitors_wh": 0.0,
            "cpu_wh": 0.0,
            "gpu_wh": 0.0,
            "ram_wh": 0.0,
            "storage_wh": 0.0,
            "mb_fans_wh": 0.0,
            "peripherals_wh": 0.0,
            "psu_loss_wh": 0.0,
            "external_wh": 0.0,
            "monitor_on_sec": 0.0,
            "monitor_idle_waste_sec": 0.0,
            "monitor_idle_waste_wh": 0.0,
            "total_cost_ntd": 0.0,
            "monitors_cost_ntd": 0.0,
            "monitor_idle_waste_cost_ntd": 0.0,
            "devices": {},
        }

    def register_ui_client(self) -> None:
        with self._lock:
            self._ui_clients += 1
        self._apply_adaptive_mode()
        self._loop_wake.set()

    def unregister_ui_client(self) -> None:
        with self._lock:
            self._ui_clients = max(0, self._ui_clients - 1)
        self._apply_adaptive_mode()

    def _apply_adaptive_mode(self) -> tuple[float, str]:
        with self._lock:
            ui_cnt = self._ui_clients
            deep = self._last_deep_eco
        if ui_cnt > 0:
            poll_sec, mode, eco, lhm_ms = 1.0, "realtime_1s", False, 1000
        elif deep:
            poll_sec, mode, eco, lhm_ms = 5.0, "deep_eco_5s", True, 5000
        else:
            poll_sec, mode, eco, lhm_ms = 3.0, "eco_3s", True, 3000
        with self._lock:
            self._power_mode = mode
            self._poll_interval_sec = poll_sec
        self.lhm.set_interval_ms(lhm_ms)
        self.monitor_sensor.set_eco_mode(eco)
        return poll_sec, mode

    def start(self) -> None:
        self.hardware = discover_all_hardware()
        self.lhm.start()
        self.monitor_sensor.start()
        try:
            psutil.cpu_percent(interval=None)
            dc = psutil.disk_io_counters()
            if dc:
                self._prev_disk_bytes = dc.read_bytes + dc.write_bytes
                self._prev_disk_ts = time.time()
        except Exception:
            pass

        # Collect one immediate sample so API/UI has instant data
        self._sample_once(dt=1.0)
        self._apply_adaptive_mode()
        self._stop_event.clear()
        self._loop_wake.clear()
        self._thread = threading.Thread(target=self._loop, name="PowerEngineLoop", daemon=True)
        self._thread.start()

    def rescan_hardware(self) -> dict[str, Any]:
        hw = discover_all_hardware()
        with self._lock:
            self.hardware = hw
        return hw

    def reset_session(self) -> None:
        with self._lock:
            self._session = self._new_accum()

    def _loop(self) -> None:
        last_ts = time.time()
        first_tick = True
        while not self._stop_event.is_set():
            poll_sec, _ = self._apply_adaptive_mode()
            wait_sec = 1.2 if first_tick else poll_sec
            first_tick = False
            self._loop_wake.wait(wait_sec)
            self._loop_wake.clear()
            if self._stop_event.is_set():
                break
            now = time.time()
            elapsed = now - last_ts
            last_ts = now
            # Sleep/Hibernate Spike Guard: never integrate long suspended gaps as active power
            spike_thresh = max(15.0, poll_sec * 3.0)
            if elapsed > spike_thresh:
                dt = poll_sec
            else:
                dt = max(0.2, min(poll_sec + 3.0, elapsed))
            try:
                self._sample_once(dt=dt)
            except Exception:
                continue

    def _get_disk_mb_per_sec(self, now: float) -> float:
        try:
            dc = psutil.disk_io_counters()
            if not dc:
                return 0.0
            cur = dc.read_bytes + dc.write_bytes
            if self._prev_disk_bytes is None or now <= self._prev_disk_ts:
                self._prev_disk_bytes = cur
                self._prev_disk_ts = now
                return 0.0
            delta = max(0, cur - self._prev_disk_bytes)
            dt = max(0.1, now - self._prev_disk_ts)
            self._prev_disk_bytes = cur
            self._prev_disk_ts = now
            return (delta / (1024 * 1024)) / dt
        except Exception:
            return 0.0

    def _sample_once(self, dt: float) -> None:
        now = time.time()
        dt_now = datetime.datetime.fromtimestamp(now)
        date_str = dt_now.strftime("%Y-%m-%d")
        hour_int = dt_now.hour

        cfg = self.config.get()
        tariff_info = get_effective_rate_info(cfg, dt_now)
        rate_ntd = float(tariff_info["rate_ntd"])
        psu_tier = cfg["psu_efficiency_tier"]
        psu_rated = int(cfg["psu_rated_watts"])
        idle_thresh = float(cfg["idle_waste_threshold_sec"])
        mon_overrides = cfg.get("monitor_overrides", {})
        ext_devices = cfg.get("external_devices", [])

        with self._lock:
            hw = dict(self.hardware)

        is_laptop = bool(hw.get("system", {}).get("is_laptop", False))
        lhm_snap = self.lhm.get_snapshot()
        nvml_gpus = self.nvml.poll()
        mon_states = self.monitor_sensor.get_monitor_states(hw.get("monitors", []))
        idle_sec = get_user_idle_seconds()

        try:
            cpu_util = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu_util = 5.0
        try:
            freq = psutil.cpu_freq()
            cur_mhz = float(freq.current) if freq and freq.current else 0.0
        except Exception:
            cur_mhz = 0.0
        try:
            mem_pct = float(psutil.virtual_memory().percent)
        except Exception:
            mem_pct = 35.0
        disk_mb_s = self._get_disk_mb_per_sec(now)

        # 1. CPU
        cpus = hw.get("cpus", [])
        cpu_name = cpus[0]["name"] if cpus else "CPU"
        cpu_max_mhz = float(cpus[0]["max_mhz"]) if cpus and cpus[0].get("max_mhz") else 3000.0
        lhm_cpu_w = lhm_snap.get("cpu_power_w")
        if lhm_cpu_w is not None and float(lhm_cpu_w) > 0.5:
            cpu_w = round(float(lhm_cpu_w), 2)
            cpu_source = "sensor_rapl"
        else:
            freq_ratio = (cur_mhz / cpu_max_mhz) if (cur_mhz > 100 and cpu_max_mhz > 100) else 1.0
            cpu_w = 0.0
            for c in cpus or [{"profile": {"idle_w": 12.0, "max_w": 95.0}}]:
                cpu_w += estimate_cpu_watts(c["profile"], cpu_util, freq_ratio)
            cpu_w = round(cpu_w, 2)
            cpu_source = "model"

        # 2. GPUs
        gpu_items: list[dict[str, Any]] = []
        gpu_total_w = 0.0
        gpu_sources: set[str] = set()
        lhm_gpus = lhm_snap.get("gpus") or []

        for g in hw.get("gpus", []):
            gname = g["name"]
            gprof = g["profile"]
            gw = None
            gsource = "model"
            gtemp = None
            gutil = None

            # Try NVML exact match
            for nv in nvml_gpus:
                if nv["name"].upper() in gname.upper() or gname.upper() in nv["name"].upper():
                    if nv["power_w"] is not None:
                        gw = float(nv["power_w"])
                        gsource = "sensor_nvml"
                    gtemp = nv.get("temp_c")
                    gutil = nv.get("util_pct")
                    break

            # Fallback to LHM GPU sensor
            if gw is None:
                for lg in lhm_gpus:
                    lname = str(lg.get("name") or "")
                    if lname.upper() in gname.upper() or gname.upper() in lname.upper():
                        if lg.get("power_w") is not None:
                            gw = float(lg["power_w"])
                            gsource = "sensor_lhm"
                        gtemp = gtemp if gtemp is not None else lg.get("temp_c")
                        gutil = gutil if gutil is not None else lg.get("load_pct")
                        break

            # Fallback to model (avoid double counting iGPU when CPU RAPL is active)
            if gw is None:
                if gprof.get("is_igpu") and cpu_source == "sensor_rapl":
                    gw = 0.0
                else:
                    gw = estimate_gpu_watts(gprof, gutil if gutil is not None else cpu_util * 0.4)
                gsource = "model"

            gw = round(float(gw), 2)
            gpu_total_w += gw
            gpu_sources.add(gsource)
            gpu_items.append(
                {
                    "id": g["id"],
                    "name": gname,
                    "watts": gw,
                    "temp_c": gtemp,
                    "util_pct": gutil,
                    "source": gsource,
                    "confidence": CONFIDENCE_BADGE[gsource],
                }
            )
        gpu_total_w = round(gpu_total_w, 2)
        gpu_source = "sensor_nvml" if "sensor_nvml" in gpu_sources else ("sensor_lhm" if "sensor_lhm" in gpu_sources else "model")

        # 3. RAM
        ram_info = hw.get("ram", {})
        dimm_count = int(ram_info.get("dimm_count") or 1)
        ddr_type = ram_info.get("ddr_type", "DDR4")
        lhm_dram_w = lhm_snap.get("dram_power_w")
        if lhm_dram_w is not None and float(lhm_dram_w) > 0:
            ram_w = round(float(lhm_dram_w), 2)
            ram_source = "sensor_rapl"
        else:
            per_dimm_base = {"DDR3": 2.2, "DDR4": 1.4, "DDR5": 1.9}.get(ddr_type, 1.5)
            per_dimm_span = {"DDR3": 1.3, "DDR4": 1.2, "DDR5": 1.5}.get(ddr_type, 1.2)
            ram_w = round(dimm_count * (per_dimm_base + per_dimm_span * (mem_pct / 100.0)), 2)
            ram_source = "model"

        # 4. Storage
        disks = hw.get("disks", [])
        storage_w = 0.0
        io_factor = min(1.0, disk_mb_s / 150.0)
        for d in disks:
            storage_w += d["idle_w"] + (d["max_w"] - d["idle_w"]) * io_factor
        storage_w = round(storage_w, 2)

        # 5. Motherboard, Chipset & Cooling Fans
        mb_name = hw.get("system", {}).get("motherboard", "主機板")
        if is_laptop:
            mb_base = 4.5
        elif any(k in (mb_name + " " + cpu_name).upper() for k in ("X99", "X299", "X399", "TRX40", "WRX80", "XEON")):
            mb_base = 24.0
        else:
            mb_base = 15.0

        lhm_fans = lhm_snap.get("fans") or []
        if lhm_fans:
            fans_w = sum(0.8 + 1.8 * min(1.0, float(f.get("rpm", 1000)) / 2200.0) for f in lhm_fans)
        elif is_laptop:
            fans_w = 0.5 + 1.5 * (cpu_util / 100.0)
        else:
            fans_w = 3.2 + 2.5 * (cpu_util / 100.0)
        mb_fans_w = round(mb_base + fans_w, 2)

        # 6. USB Peripherals
        peripherals = hw.get("peripherals", [])
        activity_bump = 1.05 if idle_sec < 5.0 else 0.90
        peripherals_w = round(sum(float(p["watts"]) * activity_bump for p in peripherals), 2)

        # 7. PC Tower DC & PSU Conversion Loss -> PC AC
        pc_dc_w = round(cpu_w + gpu_total_w + ram_w + storage_w + mb_fans_w + peripherals_w, 2)
        bat_info = lhm_snap.get("battery")
        if is_laptop and bat_info and bat_info.get("discharge_w") and float(bat_info["discharge_w"]) > 1.0:
            psu_eff = 1.0
            psu_loss_w = 0.0
            pc_ac_w = round(float(bat_info["discharge_w"]), 2)
        else:
            eff_tier = "gold" if is_laptop else psu_tier
            psu_eff = get_psu_efficiency(pc_dc_w, psu_rated, eff_tier)
            pc_ac_w = round(pc_dc_w / psu_eff, 2)
            psu_loss_w = round(pc_ac_w - pc_dc_w, 2)

        # 8. Monitors (Direct Wall AC - NOT multiplied by PSU efficiency!)
        monitor_items: list[dict[str, Any]] = []
        monitors_w = 0.0
        active_monitor_count = 0
        active_monitors_wasted_rate = 0.0

        for m in hw.get("monitors", []):
            mid = m["id"]
            st_info = mon_states.get(mid, {"state": "on", "ddcci_supported": False, "ddcci_brightness": None, "ddcci_brightness_reliable": False})
            ov = mon_overrides.get(mid, {})
            panel_type = ov.get("panel_type") or m.get("default_panel", "IPS")
            custom_watts = ov.get("custom_watts")
            ddcci_reliable = bool(st_info.get("ddcci_brightness_reliable", False))
            if ddcci_reliable and st_info.get("ddcci_brightness") is not None:
                brightness = float(st_info["ddcci_brightness"])
            else:
                brightness = float(ov.get("manual_brightness", 70))

            state = st_info.get("state", "on")
            mw = estimate_monitor_watts(
                width_cm=m.get("width_cm", 0.0),
                height_cm=m.get("height_cm", 0.0),
                diagonal_in=m.get("diagonal_in", 27.0),
                width_px=m.get("width_px", 1920),
                height_px=m.get("height_px", 1080),
                refresh_hz=m.get("refresh_hz", 60),
                brightness_pct=brightness,
                panel_type=panel_type,
                state=state,
                custom_watts=custom_watts,
                model_spec=tuple(m["known_spec"]) if m.get("known_spec") else None,
            )

            if custom_watts:
                msource = "calibrated"
            elif st_info.get("ddcci_supported"):
                msource = "ddcci_model"
            else:
                msource = "model"

            monitors_w += mw
            if state in ("on", "dimmed"):
                active_monitor_count += 1
                active_monitors_wasted_rate += max(0.0, mw - 0.5)

            monitor_items.append(
                {
                    "id": mid,
                    "name": ov.get("display_name") or m["model_name"],
                    "device_name": m["device_name"],
                    "diagonal_in": m["diagonal_in"],
                    "resolution": f"{m['width_px']}×{m['height_px']}",
                    "refresh_hz": m["refresh_hz"],
                    "panel_type": panel_type,
                    "brightness_pct": round(brightness, 0),
                    "ddcci_supported": bool(st_info.get("ddcci_supported", False)),
                    "ddcci_brightness_reliable": ddcci_reliable,
                    "ddcci_power_mode": st_info.get("ddcci_power_mode"),
                    "custom_watts": custom_watts,
                    "state": state,
                    "watts": mw,
                    "source": msource,
                    "confidence": CONFIDENCE_BADGE[msource],
                }
            )
        monitors_w = round(monitors_w, 2)

        # 9. External Custom AC Devices
        enabled_ext = [d for d in ext_devices if d.get("enabled", True)]
        external_w = round(sum(float(d.get("watts", 0.0)) for d in enabled_ext), 2)

        # 10. Grand Total Wall AC Power
        total_ac_w = round(pc_ac_w + monitors_w + external_w, 2)

        # 11. Idle-Screen Waste Detection
        is_idle_screen_waste = (idle_sec >= idle_thresh) and (active_monitor_count > 0)
        waste_w = round(active_monitors_wasted_rate, 2) if is_idle_screen_waste else 0.0
        waste_wh_delta = waste_w * dt / 3600.0
        mon_on_delta = dt if active_monitor_count > 0 else 0.0
        mon_waste_sec_delta = dt if is_idle_screen_waste else 0.0

        # 12. Accumulate Session + Pending DB Buckets
        minute_ts = int(now // 60) * 60
        with self._lock:
            s = self._session
            s["duration_sec"] += dt
            s["total_wh"] += total_ac_w * dt / 3600.0
            s["pc_ac_wh"] += pc_ac_w * dt / 3600.0
            s["monitors_wh"] += monitors_w * dt / 3600.0
            s["cpu_wh"] += cpu_w * dt / 3600.0
            s["gpu_wh"] += gpu_total_w * dt / 3600.0
            s["ram_wh"] += ram_w * dt / 3600.0
            s["storage_wh"] += storage_w * dt / 3600.0
            s["mb_fans_wh"] += mb_fans_w * dt / 3600.0
            s["peripherals_wh"] += peripherals_w * dt / 3600.0
            s["psu_loss_wh"] += psu_loss_w * dt / 3600.0
            s["external_wh"] += external_w * dt / 3600.0
            s["monitor_on_sec"] += mon_on_delta
            s["monitor_idle_waste_sec"] += mon_waste_sec_delta
            s["monitor_idle_waste_wh"] += waste_wh_delta
            s["total_cost_ntd"] += (total_ac_w * dt / 3600.0 / 1000.0) * rate_ntd
            s["monitors_cost_ntd"] += (monitors_w * dt / 3600.0 / 1000.0) * rate_ntd
            s["monitor_idle_waste_cost_ntd"] += (waste_wh_delta / 1000.0) * rate_ntd

            # Minute bucket accumulation (time-weighted)
            b = self._pending_buckets.get(minute_ts)
            if b is None:
                self._pending_buckets[minute_ts] = {
                    "ts": minute_ts,
                    "date": date_str,
                    "hour": hour_int,
                    "total_ac_w": total_ac_w,
                    "pc_ac_w": pc_ac_w,
                    "monitors_w": monitors_w,
                    "cpu_w": cpu_w,
                    "gpu_w": gpu_total_w,
                    "ram_w": ram_w,
                    "storage_w": storage_w,
                    "mb_fans_w": mb_fans_w,
                    "peripherals_w": peripherals_w,
                    "psu_loss_w": psu_loss_w,
                    "external_w": external_w,
                    "duration_sec": dt,
                    "monitor_on_sec": mon_on_delta,
                    "monitor_idle_waste_sec": mon_waste_sec_delta,
                    "monitor_idle_waste_wh": waste_wh_delta,
                    "cost_ntd": (total_ac_w * dt / 3600.0 / 1000.0) * rate_ntd,
                    "monitors_cost_ntd": (monitors_w * dt / 3600.0 / 1000.0) * rate_ntd,
                    "waste_cost_ntd": (waste_wh_delta / 1000.0) * rate_ntd,
                }
            else:
                old_d = b["duration_sec"]
                new_d = old_d + dt
                for key, val in (
                    ("total_ac_w", total_ac_w),
                    ("pc_ac_w", pc_ac_w),
                    ("monitors_w", monitors_w),
                    ("cpu_w", cpu_w),
                    ("gpu_w", gpu_total_w),
                    ("ram_w", ram_w),
                    ("storage_w", storage_w),
                    ("mb_fans_w", mb_fans_w),
                    ("peripherals_w", peripherals_w),
                    ("psu_loss_w", psu_loss_w),
                    ("external_w", external_w),
                ):
                    b[key] = (b[key] * old_d + val * dt) / new_d
                b["duration_sec"] = new_d
                b["monitor_on_sec"] += mon_on_delta
                b["monitor_idle_waste_sec"] += mon_waste_sec_delta
                b["monitor_idle_waste_wh"] += waste_wh_delta
                b["cost_ntd"] += (total_ac_w * dt / 3600.0 / 1000.0) * rate_ntd
                b["monitors_cost_ntd"] += (monitors_w * dt / 3600.0 / 1000.0) * rate_ntd
                b["waste_cost_ntd"] += (waste_wh_delta / 1000.0) * rate_ntd

            # Device-level tracking (CPU, GPUs, Monitors, RAM, Storage, MB, Peripherals, PSU Loss, External)
            dev_entries = [
                ("cpu", f"CPU: {cpu_name}", "處理器", cpu_w, dt, 0.0, 0.0),
                ("ram", f"記憶體: {ram_info.get('label', 'RAM')}", "記憶體", ram_w, dt, 0.0, 0.0),
                ("storage", f"儲存裝置 ({len(disks)} 顆)", "硬碟儲存", storage_w, dt, 0.0, 0.0),
                ("mb_fans", f"主機板與散熱風扇 ({mb_name})", "主機板/風扇", mb_fans_w, dt, 0.0, 0.0),
                ("peripherals", f"USB 週邊 ({len(peripherals)} 項)", "USB 週邊", peripherals_w, dt, 0.0, 0.0),
                ("psu_loss", f"電供轉換損耗 ({PSU_TIERS.get(psu_tier, PSU_TIERS['bronze'])['label']})", "電源損耗", psu_loss_w, dt, 0.0, 0.0),
            ]
            for gi in gpu_items:
                dev_entries.append((gi["id"], f"顯示卡: {gi['name']}", "顯示卡", gi["watts"], dt, 0.0, 0.0))
            for mi in monitor_items:
                m_act = dt if mi["state"] in ("on", "dimmed") else 0.0
                m_waste_s = dt if (is_idle_screen_waste and mi["state"] in ("on", "dimmed")) else 0.0
                m_waste_wh = max(0.0, mi["watts"] - 0.5) * m_waste_s / 3600.0
                dev_entries.append((f"mon_{mi['id']}", f"螢幕: {mi['name']}", "外接螢幕", mi["watts"], m_act, m_waste_wh, m_waste_s))
            for ext in enabled_ext:
                dev_entries.append((f"ext_{ext['id']}", ext["name"], "外接電器", float(ext["watts"]), dt, 0.0, 0.0))

            for did, dname, dcat, dw, dact, dwwh, dws in dev_entries:
                e_wh = dw * dt / 3600.0
                e_cost = (e_wh / 1000.0) * rate_ntd
                sd = s["devices"].setdefault(
                    did,
                    {"device_id": did, "device_name": dname, "category": dcat, "energy_wh": 0.0, "active_sec": 0.0, "idle_waste_wh": 0.0, "idle_waste_sec": 0.0, "cost_ntd": 0.0},
                )
                sd["energy_wh"] += e_wh
                sd["active_sec"] += dact
                sd["idle_waste_wh"] += dwwh
                sd["idle_waste_sec"] += dws
                sd["cost_ntd"] += e_cost

                pd = self._pending_devices.setdefault(
                    (date_str, did),
                    {"date": date_str, "device_id": did, "device_name": dname, "category": dcat, "energy_wh": 0.0, "active_sec": 0.0, "idle_waste_wh": 0.0, "idle_waste_sec": 0.0, "cost_ntd": 0.0},
                )
                pd["device_name"] = dname
                pd["energy_wh"] += e_wh
                pd["active_sec"] += dact
                pd["idle_waste_wh"] += dwwh
                pd["idle_waste_sec"] += dws
                pd["cost_ntd"] += e_cost

            # Flush every 30s or when multiple minutes buffered
            if (now - self._last_flush_ts) >= 30.0 or len(self._pending_buckets) >= 2:
                self._flush_locked()

            today_totals = self._compute_today_totals_locked(date_str, rate_ntd, cfg)
            eff_today_rate = (
                (today_totals["total_cost_ntd"] / today_totals["total_kwh"])
                if today_totals["total_kwh"] > 0
                else rate_ntd
            )

            components = [
                {
                    "id": "monitors",
                    "name": "外接螢幕 (直接市電)",
                    "detail": " / ".join(f"{m['name']} ({m['diagonal_in']}\" {m['resolution']}@{m['refresh_hz']}Hz)" for m in monitor_items) or "未偵測到螢幕",
                    "watts": monitors_w,
                    "source": monitor_items[0]["source"] if monitor_items else "model",
                    "source_label": SOURCE_LABELS[monitor_items[0]["source"]] if monitor_items else SOURCE_LABELS["model"],
                    "confidence": monitor_items[0]["confidence"] if monitor_items else "estimated",
                    "today_wh": today_totals["monitors_wh"],
                    "today_cost_ntd": today_totals["monitors_cost_ntd"],
                    "color": "#38bdf8",
                },
                {
                    "id": "gpu",
                    "name": "顯示卡 GPU",
                    "detail": " / ".join(g["name"] for g in gpu_items) or "內顯/無獨立顯卡",
                    "watts": gpu_total_w,
                    "source": gpu_source,
                    "source_label": SOURCE_LABELS[gpu_source],
                    "confidence": CONFIDENCE_BADGE[gpu_source],
                    "today_wh": today_totals["gpu_wh"],
                    "today_cost_ntd": round((today_totals["gpu_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#a855f7",
                },
                {
                    "id": "cpu",
                    "name": "處理器 CPU",
                    "detail": f"{cpu_name} (負載 {cpu_util:.0f}%)",
                    "watts": cpu_w,
                    "source": cpu_source,
                    "source_label": SOURCE_LABELS[cpu_source],
                    "confidence": CONFIDENCE_BADGE[cpu_source],
                    "today_wh": today_totals["cpu_wh"],
                    "today_cost_ntd": round((today_totals["cpu_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#22c55e",
                },
                {
                    "id": "mb_fans",
                    "name": "主機板與風扇",
                    "detail": f"{mb_name} + {len(lhm_fans) if lhm_fans else ('1' if is_laptop else '3')}組風扇",
                    "watts": mb_fans_w,
                    "source": "model",
                    "source_label": SOURCE_LABELS["model"],
                    "confidence": "estimated",
                    "today_wh": today_totals["mb_fans_wh"],
                    "today_cost_ntd": round((today_totals["mb_fans_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#f59e0b",
                },
                {
                    "id": "ram",
                    "name": "記憶體 RAM",
                    "detail": f"{ram_info.get('label', 'RAM')} (使用率 {mem_pct:.0f}%)",
                    "watts": ram_w,
                    "source": ram_source,
                    "source_label": SOURCE_LABELS[ram_source],
                    "confidence": CONFIDENCE_BADGE[ram_source],
                    "today_wh": today_totals["ram_wh"],
                    "today_cost_ntd": round((today_totals["ram_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#06b6d4",
                },
                {
                    "id": "storage",
                    "name": "硬碟 / SSD",
                    "detail": " / ".join(f"{d['name']} ({d['kind']})" for d in disks) or "無偵測磁碟",
                    "watts": storage_w,
                    "source": "model",
                    "source_label": SOURCE_LABELS["model"],
                    "confidence": "estimated",
                    "today_wh": today_totals["storage_wh"],
                    "today_cost_ntd": round((today_totals["storage_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#14b8a6",
                },
                {
                    "id": "peripherals",
                    "name": "USB 鍵鼠與週邊",
                    "detail": "、".join(p["name"] for p in peripherals) or "無外接 USB 週邊",
                    "watts": peripherals_w,
                    "source": "model",
                    "source_label": SOURCE_LABELS["model"],
                    "confidence": "estimated",
                    "today_wh": today_totals["peripherals_wh"],
                    "today_cost_ntd": round((today_totals["peripherals_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#ec4899",
                },
                {
                    "id": "psu_loss",
                    "name": "電源轉換損耗",
                    "detail": f"轉換效率 {psu_eff * 100:.1f}% ({PSU_TIERS.get(psu_tier, PSU_TIERS['bronze'])['label']})" if not is_laptop else "筆記型電腦變壓器轉換",
                    "watts": psu_loss_w,
                    "source": "model",
                    "source_label": SOURCE_LABELS["model"],
                    "confidence": "estimated",
                    "today_wh": today_totals["psu_loss_wh"],
                    "today_cost_ntd": round((today_totals["psu_loss_wh"] / 1000.0) * eff_today_rate, 2),
                    "color": "#64748b",
                },
            ]
            if external_w > 0 or enabled_ext:
                components.append(
                    {
                        "id": "external",
                        "name": "自訂外接設備",
                        "detail": "、".join(f"{d['name']}({d['watts']}W)" for d in enabled_ext),
                        "watts": external_w,
                        "source": "calibrated",
                        "source_label": SOURCE_LABELS["calibrated"],
                        "confidence": "calibrated",
                        "today_wh": today_totals["external_wh"],
                        "today_cost_ntd": round((today_totals["external_wh"] / 1000.0) * eff_today_rate, 2),
                        "color": "#fb7185",
                    }
                )

            for c in components:
                c["share_pct"] = round((c["watts"] / total_ac_w * 100.0) if total_ac_w > 0 else 0.0, 1)

            point = {
                "ts": round(now, 1),
                "time": dt_now.strftime("%H:%M:%S"),
                "total_ac_w": total_ac_w,
                "pc_ac_w": pc_ac_w,
                "monitors_w": monitors_w,
                "cpu_w": cpu_w,
                "gpu_w": gpu_total_w,
            }
            self.history_buffer.append(point)

            self._last_deep_eco = (active_monitor_count == 0) or (idle_sec >= 60.0)
            lhm_status = self.lhm.status()
            snapshot = {
                "ts": round(now, 1),
                "time": dt_now.strftime("%H:%M:%S"),
                "power_mode": self._power_mode,
                "poll_interval_sec": self._poll_interval_sec,
                "ui_clients": self._ui_clients,
                "electricity_rate_ntd": rate_ntd,
                "tariff": tariff_info,
                "total_ac_w": total_ac_w,
                "pc_ac_w": pc_ac_w,
                "pc_dc_w": pc_dc_w,
                "monitors_w": monitors_w,
                "external_w": external_w,
                "cpu_w": cpu_w,
                "gpu_w": gpu_total_w,
                "ram_w": ram_w,
                "storage_w": storage_w,
                "mb_fans_w": mb_fans_w,
                "peripherals_w": peripherals_w,
                "psu_loss_w": psu_loss_w,
                "psu_efficiency_pct": round(psu_eff * 100.0, 1),
                "cpu_util_pct": round(cpu_util, 1),
                "cpu_temp_c": lhm_snap.get("cpu_temp_c"),
                "mem_util_pct": round(mem_pct, 1),
                "idle_sec": round(idle_sec, 1),
                "idle_threshold_sec": idle_thresh,
                "is_idle_screen_waste": is_idle_screen_waste,
                "waste_w": waste_w,
                "monitors": monitor_items,
                "gpus": gpu_items,
                "components": components,
                "today": today_totals,
                "session": self._build_session_summary_locked(rate_ntd),
                "sensors": {
                    "nvml_active": self.nvml.available,
                    "lhm_bridge": lhm_status["bridge_running"],
                    "cpu_rapl_active": lhm_status["cpu_sensor_active"],
                    "pawnio_installed": lhm_status["pawnio_installed"],
                    "is_admin": lhm_status["is_admin"],
                    "ddcci_any": any(m["ddcci_supported"] for m in monitor_items),
                    "ddcci_brightness_reliable": any(m["ddcci_brightness_reliable"] for m in monitor_items),
                },
                "chart_point": point,
            }
            self._latest_snapshot = snapshot

    def _compute_today_totals_locked(self, date_str: str, rate_ntd: float, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.time()
        if (
            self._cached_today_base is None
            or self._cached_today_date != date_str
            or (now - self._cached_today_ts) >= 15.0
        ):
            self._cached_today_base = self.storage.get_summary("today", rate_ntd=rate_ntd, cfg=cfg)
            self._cached_today_date = date_str
            self._cached_today_ts = now

        base = dict(self._cached_today_base)
        # Add unflushed pending buckets for live accuracy
        add_wh = {
            "total_wh": 0.0, "pc_ac_wh": 0.0, "monitors_wh": 0.0, "cpu_wh": 0.0,
            "gpu_wh": 0.0, "ram_wh": 0.0, "storage_wh": 0.0, "mb_fans_wh": 0.0,
            "peripherals_wh": 0.0, "psu_loss_wh": 0.0, "external_wh": 0.0,
            "monitor_on_sec": 0.0, "monitor_idle_waste_sec": 0.0, "monitor_idle_waste_wh": 0.0,
            "duration_sec": 0.0,
            "cost_ntd": 0.0, "monitors_cost_ntd": 0.0, "waste_cost_ntd": 0.0,
        }
        for b in self._pending_buckets.values():
            if b["date"] != date_str:
                continue
            dur = b["duration_sec"]
            add_wh["duration_sec"] += dur
            add_wh["total_wh"] += b["total_ac_w"] * dur / 3600.0
            add_wh["pc_ac_wh"] += b["pc_ac_w"] * dur / 3600.0
            add_wh["monitors_wh"] += b["monitors_w"] * dur / 3600.0
            add_wh["cpu_wh"] += b["cpu_w"] * dur / 3600.0
            add_wh["gpu_wh"] += b["gpu_w"] * dur / 3600.0
            add_wh["ram_wh"] += b["ram_w"] * dur / 3600.0
            add_wh["storage_wh"] += b["storage_w"] * dur / 3600.0
            add_wh["mb_fans_wh"] += b["mb_fans_w"] * dur / 3600.0
            add_wh["peripherals_wh"] += b["peripherals_w"] * dur / 3600.0
            add_wh["psu_loss_wh"] += b["psu_loss_w"] * dur / 3600.0
            add_wh["external_wh"] += b["external_w"] * dur / 3600.0
            add_wh["monitor_on_sec"] += b["monitor_on_sec"]
            add_wh["monitor_idle_waste_sec"] += b["monitor_idle_waste_sec"]
            add_wh["monitor_idle_waste_wh"] += b["monitor_idle_waste_wh"]
            add_wh["cost_ntd"] += b.get("cost_ntd", (b["total_ac_w"] * dur / 3600.0 / 1000.0) * rate_ntd)
            add_wh["monitors_cost_ntd"] += b.get("monitors_cost_ntd", (b["monitors_w"] * dur / 3600.0 / 1000.0) * rate_ntd)
            add_wh["waste_cost_ntd"] += b.get("waste_cost_ntd", (b["monitor_idle_waste_wh"] / 1000.0) * rate_ntd)

        tot_wh = float(base.get("total_wh", 0.0)) + add_wh["total_wh"]
        mon_wh = float(base.get("monitors_wh", 0.0)) + add_wh["monitors_wh"]
        waste_wh = float(base.get("monitor_idle_waste_wh", 0.0)) + add_wh["monitor_idle_waste_wh"]
        tot_cost = float(base.get("total_cost_ntd", 0.0)) + add_wh["cost_ntd"]
        mon_cost = float(base.get("monitors_cost_ntd", 0.0)) + add_wh["monitors_cost_ntd"]
        waste_cost = float(base.get("monitor_idle_waste_cost_ntd", 0.0)) + add_wh["waste_cost_ntd"]
        return {
            "total_wh": round(tot_wh, 2),
            "total_kwh": round(tot_wh / 1000.0, 4),
            "total_cost_ntd": round(tot_cost, 2),
            "pc_ac_wh": round(float(base.get("pc_ac_wh", 0.0)) + add_wh["pc_ac_wh"], 2),
            "monitors_wh": round(mon_wh, 2),
            "monitors_kwh": round(mon_wh / 1000.0, 4),
            "monitors_cost_ntd": round(mon_cost, 2),
            "monitor_pct": round((mon_wh / tot_wh * 100.0) if tot_wh > 0 else 0.0, 1),
            "cpu_wh": round(float(base.get("cpu_wh", 0.0)) + add_wh["cpu_wh"], 2),
            "gpu_wh": round(float(base.get("gpu_wh", 0.0)) + add_wh["gpu_wh"], 2),
            "ram_wh": round(float(base.get("ram_wh", 0.0)) + add_wh["ram_wh"], 2),
            "storage_wh": round(float(base.get("storage_wh", 0.0)) + add_wh["storage_wh"], 2),
            "mb_fans_wh": round(float(base.get("mb_fans_wh", 0.0)) + add_wh["mb_fans_wh"], 2),
            "peripherals_wh": round(float(base.get("peripherals_wh", 0.0)) + add_wh["peripherals_wh"], 2),
            "psu_loss_wh": round(float(base.get("psu_loss_wh", 0.0)) + add_wh["psu_loss_wh"], 2),
            "external_wh": round(float(base.get("external_wh", 0.0)) + add_wh["external_wh"], 2),
            "duration_sec": round(float(base.get("duration_sec", 0.0)) + add_wh["duration_sec"], 1),
            "monitor_on_sec": round(float(base.get("monitor_on_sec", 0.0)) + add_wh["monitor_on_sec"], 1),
            "monitor_idle_waste_sec": round(float(base.get("monitor_idle_waste_sec", 0.0)) + add_wh["monitor_idle_waste_sec"], 1),
            "monitor_idle_waste_wh": round(waste_wh, 2),
            "monitor_idle_waste_cost_ntd": round(waste_cost, 2),
        }

    def _build_session_summary_locked(self, rate_ntd: float) -> dict[str, Any]:
        s = self._session
        tot_wh = s["total_wh"]
        mon_wh = s["monitors_wh"]
        waste_wh = s["monitor_idle_waste_wh"]
        devs = sorted(s["devices"].values(), key=lambda x: x["energy_wh"], reverse=True)
        return {
            "range": "session",
            "duration_sec": round(s["duration_sec"], 1),
            "total_wh": round(tot_wh, 2),
            "total_kwh": round(tot_wh / 1000.0, 4),
            "total_cost_ntd": round(s.get("total_cost_ntd", (tot_wh / 1000.0) * rate_ntd), 2),
            "pc_ac_wh": round(s["pc_ac_wh"], 2),
            "monitors_wh": round(mon_wh, 2),
            "monitors_kwh": round(mon_wh / 1000.0, 4),
            "monitors_cost_ntd": round(s.get("monitors_cost_ntd", (mon_wh / 1000.0) * rate_ntd), 2),
            "monitor_pct": round((mon_wh / tot_wh * 100.0) if tot_wh > 0 else 0.0, 1),
            "cpu_wh": round(s["cpu_wh"], 2),
            "gpu_wh": round(s["gpu_wh"], 2),
            "ram_wh": round(s["ram_wh"], 2),
            "storage_wh": round(s["storage_wh"], 2),
            "mb_fans_wh": round(s["mb_fans_wh"], 2),
            "peripherals_wh": round(s["peripherals_wh"], 2),
            "psu_loss_wh": round(s["psu_loss_wh"], 2),
            "external_wh": round(s["external_wh"], 2),
            "monitor_on_sec": round(s["monitor_on_sec"], 1),
            "monitor_idle_waste_sec": round(s["monitor_idle_waste_sec"], 1),
            "monitor_idle_waste_wh": round(waste_wh, 2),
            "monitor_idle_waste_kwh": round(waste_wh / 1000.0, 4),
            "monitor_idle_waste_cost_ntd": round(s.get("monitor_idle_waste_cost_ntd", (waste_wh / 1000.0) * rate_ntd), 2),
            "devices": [
                {
                    **d,
                    "energy_wh": round(d["energy_wh"], 2),
                    "energy_kwh": round(d["energy_wh"] / 1000.0, 4),
                    "cost_ntd": round(d.get("cost_ntd", (d["energy_wh"] / 1000.0) * rate_ntd), 2),
                    "active_sec": round(d["active_sec"], 1),
                    "idle_waste_wh": round(d["idle_waste_wh"], 2),
                    "idle_waste_sec": round(d["idle_waste_sec"], 1),
                }
                for d in devs
            ],
        }

    def _flush_locked(self) -> None:
        buckets = list(self._pending_buckets.values())
        devices = list(self._pending_devices.values())
        self._pending_buckets.clear()
        self._pending_devices.clear()
        self._last_flush_ts = time.time()
        self._cached_today_base = None  # Refresh base next check
        if buckets or devices:
            self.storage.record_flush(buckets, devices)

    def get_live_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = dict(self._latest_snapshot)
            snap["power_mode"] = self._power_mode
            snap["poll_interval_sec"] = self._poll_interval_sec
            snap["ui_clients"] = self._ui_clients
            snap["chart_history"] = list(self.history_buffer)
            return snap

    def get_history(self, range_key: str = "today") -> dict[str, Any]:
        cfg = self.config.get()
        rate = float(get_effective_rate_info(cfg)["rate_ntd"])
        with self._lock:
            if range_key == "session":
                data = self._build_session_summary_locked(rate)
                data["series"] = [
                    {
                        "label": pt["time"],
                        "pc_wh": round(pt["pc_ac_w"] / 3600.0, 4),
                        "monitors_wh": round(pt["monitors_w"] / 3600.0, 4),
                        "total_wh": round(pt["total_ac_w"] / 3600.0, 4),
                        "idle_waste_wh": 0.0,
                    }
                    for pt in list(self.history_buffer)[-60:]
                ]
                return data
            self._flush_locked()
        return self.storage.get_summary(range_key=range_key, rate_ntd=rate, cfg=cfg)

    def flush_now(self) -> None:
        with self._lock:
            self._flush_locked()

    def stop(self) -> None:
        self._stop_event.set()
        self._loop_wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self.flush_now()
        self.lhm.stop()
        self.monitor_sensor.stop()
        self.nvml.shutdown()
