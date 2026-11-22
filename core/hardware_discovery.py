from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import math
import re
import subprocess
from typing import Any

try:
    import winreg
except ImportError:
    winreg = None

from core.database import (
    classify_usb_peripheral,
    is_virtual_gpu,
    lookup_cpu_profile,
    lookup_gpu_profile,
    lookup_known_monitor,
    lookup_monitor_brand,
)

ATTACHED_TO_DESKTOP = 0x00000001
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001
ENUM_CURRENT_SETTINGS = -1

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmPositionX", wt.LONG),
        ("dmPositionY", wt.LONG),
        ("dmDisplayOrientation", wt.DWORD),
        ("dmDisplayFixedOutput", wt.DWORD),
        ("dmColor", wt.SHORT),
        ("dmDuplex", wt.SHORT),
        ("dmYResolution", wt.SHORT),
        ("dmTTOption", wt.SHORT),
        ("dmCollate", wt.SHORT),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
    ]


def parse_edid_bytes(edid: bytes) -> dict[str, Any]:
    if not edid or len(edid) < 128:
        return {}

    # Bytes 8..9: Big-endian 5-bit compressed PNP Manufacturer ID
    mfg_raw = (edid[8] << 8) | edid[9]
    c1 = ((mfg_raw >> 10) & 0x1F) + 64
    c2 = ((mfg_raw >> 5) & 0x1F) + 64
    c3 = (mfg_raw & 0x1F) + 64
    mfg_code = "".join(chr(c) if 65 <= c <= 90 else "?" for c in (c1, c2, c3))

    prod_num = edid[10] | (edid[11] << 8)
    prod_hex = f"{prod_num:04X}"
    pnp_id = f"{mfg_code}{prod_hex}"

    week = edid[16]
    year = 1990 + edid[17] if edid[17] < 100 else 0

    width_cm = float(edid[21])
    height_cm = float(edid[22])

    # Fallback to Detailed Timing Descriptor #1 (bytes 54..71) if basic size bytes are 0
    if (width_cm <= 0 or height_cm <= 0) and (edid[54] != 0 or edid[55] != 0):
        h_mm = edid[66] | ((edid[68] & 0xF0) << 4)
        v_mm = edid[67] | ((edid[68] & 0x0F) << 8)
        if h_mm > 50 and v_mm > 50:
            width_cm = round(h_mm / 10.0, 1)
            height_cm = round(v_mm / 10.0, 1)

    model_name = ""
    serial_text = ""
    for off in (54, 72, 90, 108):
        if off + 18 > len(edid):
            break
        blk = edid[off : off + 18]
        if blk[0] == 0 and blk[1] == 0 and blk[2] == 0:
            tag = blk[3]
            txt = blk[5:18].decode("ascii", errors="ignore").replace("\n", "").replace("\r", "").strip(" \x00")
            if tag == 0xFC and txt:
                model_name = txt
            elif tag == 0xFF and txt:
                serial_text = txt

    diagonal_in = round(math.hypot(width_cm, height_cm) / 2.54, 1) if (width_cm > 0 and height_cm > 0) else 0.0
    return {
        "mfg_code": mfg_code,
        "product_code": prod_hex,
        "pnp_id": pnp_id,
        "brand": lookup_monitor_brand(mfg_code),
        "edid_model_name": model_name,
        "serial": serial_text,
        "year": year,
        "week": week,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "diagonal_in": diagonal_in,
    }


def scan_registry_edid() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if winreg is None:
        return result
    base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    pnp_dir = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, pnp_dir) as mk:
                        j = 0
                        while True:
                            try:
                                inst = winreg.EnumKey(mk, j)
                                j += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(mk, inst + r"\Device Parameters") as dp:
                                    edid_raw, _ = winreg.QueryValueEx(dp, "EDID")
                                    parsed = parse_edid_bytes(bytes(edid_raw))
                                    if parsed:
                                        parsed["registry_pnp"] = pnp_dir
                                        parsed["registry_instance"] = inst
                                        key_exact = f"{pnp_dir}\\{inst}".upper()
                                        result[key_exact] = parsed
                                        result.setdefault(pnp_dir.upper(), parsed)
                            except OSError:
                                continue
                except OSError:
                    continue
    except OSError:
        pass
    return result


def enumerate_active_displays(edid_map: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    edid_map = edid_map if edid_map is not None else scan_registry_edid()
    user32 = ctypes.windll.user32
    displays: list[dict[str, Any]] = []

    idx = 0
    while True:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, idx, ctypes.byref(dd), 0):
            break
        idx += 1
        if not (dd.StateFlags & ATTACHED_TO_DESKTOP):
            continue

        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        user32.EnumDisplaySettingsW(dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))

        mon = _DISPLAY_DEVICEW()
        mon.cb = ctypes.sizeof(mon)
        has_mon = user32.EnumDisplayDevicesW(dd.DeviceName, 0, ctypes.byref(mon), EDD_GET_DEVICE_INTERFACE_NAME)

        mon_iface = mon.DeviceID if has_mon else ""
        mon_desc = mon.DeviceString if has_mon else "Generic PnP Monitor"

        # Parse \\?\DISPLAY#SGT2700#5&3b82ca56&0&UID8449#{guid}
        pnp_id = ""
        instance = ""
        m = re.search(r"DISPLAY#([^#]+)#([^#]+)#", mon_iface, re.IGNORECASE)
        if m:
            pnp_id = m.group(1).upper()
            instance = m.group(2).upper()

        edid = edid_map.get(f"{pnp_id}\\{instance}") or edid_map.get(pnp_id) or {}
        mfg_code = edid.get("mfg_code") or (pnp_id[:3] if len(pnp_id) >= 3 else "MON")
        brand = edid.get("brand") or lookup_monitor_brand(mfg_code)
        edid_model = edid.get("edid_model_name", "").strip()

        if edid_model and edid_model.lower() not in ("monitor", "generic", "pnp monitor", "generic pnp monitor"):
            friendly_model = edid_model if brand.upper() in edid_model.upper() else f"{brand} {edid_model}"
        elif pnp_id:
            friendly_model = f"{brand} {pnp_id}"
        else:
            friendly_model = mon_desc or "外接顯示器"

        width_cm = float(edid.get("width_cm") or 0.0)
        height_cm = float(edid.get("height_cm") or 0.0)
        diagonal_in = float(edid.get("diagonal_in") or 0.0)
        known_spec = lookup_known_monitor(f"{friendly_model} {pnp_id} {edid_model}")
        default_panel = known_spec[3] if known_spec else "IPS"

        mon_uid = f"{pnp_id}_{instance}" if pnp_id and instance else dd.DeviceName.replace("\\", "").replace(".", "")

        displays.append(
            {
                "id": mon_uid,
                "device_name": dd.DeviceName,
                "adapter_name": dd.DeviceString,
                "pnp_id": pnp_id or "UNKNOWN",
                "instance": instance,
                "brand": brand,
                "model_name": friendly_model,
                "edid_model_name": edid_model,
                "year": edid.get("year") or 0,
                "width_cm": width_cm,
                "height_cm": height_cm,
                "diagonal_in": diagonal_in if diagonal_in > 0 else 27.0,
                "diagonal_known": diagonal_in > 0,
                "width_px": int(dm.dmPelsWidth or 1920),
                "height_px": int(dm.dmPelsHeight or 1080),
                "refresh_hz": int(dm.dmDisplayFrequency or 60),
                "default_panel": default_panel,
                "known_spec": list(known_spec) if known_spec else None,
            }
        )

    return displays


def _ensure_list(val: Any) -> list[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def run_cim_inventory() -> dict[str, Any]:
    ps_script = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$out = @{}
$out.computer = @(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, PCSystemType, TotalPhysicalMemory)
$out.baseboard = @(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product)
$out.cpus = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed)
$out.gpus = @(Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, PNPDeviceID, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate)
$out.memory = @(Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, Capacity, Speed, SMBIOSMemoryType, MemoryType, DeviceLocator)
$disks = @(Get-CimInstance -Namespace root\Microsoft\Windows\Storage -ClassName MSFT_PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, Size)
if ($disks.Count -eq 0) {
    $disks = @(Get-CimInstance Win32_DiskDrive | Select-Object @{n='FriendlyName';e={$_.Model}}, @{n='MediaType';e={0}}, @{n='BusType';e={0}}, Size, InterfaceType, @{n='DriveMedia';e={$_.MediaType}})
}
$out.disks = $disks
$out.batteries = @(Get-CimInstance Win32_Battery | Select-Object Name, EstimatedChargeRemaining, BatteryStatus)
$out.pnp = @(Get-PnpDevice -Status OK -Class USB,HIDClass,Camera,Bluetooth,AudioEndpoint | Select-Object Class, FriendlyName, InstanceId)
$out | ConvertTo-Json -Depth 4 -Compress
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        raw = proc.stdout.strip()
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


_GENERIC_HID_NAMES = (
    "USB 輸入裝置",
    "USB INPUT DEVICE",
    "USB COMPOSITE DEVICE",
    "符合 HID 標準",
    "HID-COMPLIANT",
    "GENERIC USB HUB",
)


def _name_score(name: str) -> int:
    up = (name or "").upper()
    if not up:
        return 0
    if any(g in up for g in _GENERIC_HID_NAMES):
        return 1
    return 10 + len(up)


def deduplicate_peripherals(pnp_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in pnp_list:
        if not isinstance(item, dict):
            continue
        inst = str(item.get("InstanceId") or "")
        inst_up = inst.upper()
        name = str(item.get("FriendlyName") or "").strip()
        pnp_class = str(item.get("Class") or "")
        name_up = name.upper()

        # Exclude internal buses, wireless endpoints, virtual apps, root hubs
        if not inst_up.startswith(("USB\\", "HID\\")):
            continue
        if "ROOT_HUB" in inst_up:
            continue
        if any(v in name_up for v in ("TODESK", "VIRTUAL", "PARSEC", "SPACEDESK", "TEAMVIEWER")):
            continue
        if "GENERIC USB HUB" in name_up or "USB HUB" in name_up:
            continue

        m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", inst_up)
        if not m:
            continue
        vid, pid = m.group(1), m.group(2)

        # Skip internal Intel chipset USB hub (VID_8087&PID_8002 / 800A)
        if vid == "8087" and pid in ("8000", "8001", "8002", "8008", "800A"):
            continue

        key = (vid, pid)
        score = _name_score(name)
        existing = grouped.get(key)
        if existing is None or score > existing["score"]:
            grouped[key] = {
                "vid": vid,
                "pid": pid,
                "name": name or f"USB Device {vid}:{pid}",
                "pnp_class": pnp_class,
                "score": score,
            }

    results: list[dict[str, Any]] = []
    for (vid, pid), entry in grouped.items():
        cls_info = classify_usb_peripheral(entry["name"], entry["pnp_class"], vid, pid)
        display_name = entry["name"]
        if _name_score(display_name) <= 1 and cls_info["brand"]:
            if cls_info["brand"].upper() in cls_info["label"].upper():
                display_name = f"{cls_info['label']} ({vid}:{pid})"
            else:
                display_name = f"{cls_info['brand']} {cls_info['label']}"
        elif _name_score(display_name) <= 1:
            display_name = f"{cls_info['label']} ({vid}:{pid})"
        results.append(
            {
                "id": f"usb_{vid}_{pid}",
                "vid": vid,
                "pid": pid,
                "name": display_name,
                "category": cls_info["category"],
                "label": cls_info["label"],
                "watts": cls_info["watts"],
            }
        )
    return results


def _classify_disk(disk: dict[str, Any]) -> dict[str, Any]:
    name = str(disk.get("FriendlyName") or "Disk Drive").strip()
    size_bytes = int(disk.get("Size") or 0)
    size_gb = round(size_bytes / (1024 ** 3)) if size_bytes else 0
    bus = int(disk.get("BusType") or 0)
    media = int(disk.get("MediaType") or 0)
    up = name.upper()
    iface = str(disk.get("InterfaceType") or "").upper()

    if bus == 17 or "NVME" in up:
        kind = "NVMe SSD"
        idle_w, max_w = 1.5, 6.0
    elif media == 3 or ("HDD" in up or "WDC" in up or "ST" in up[:2] and "SSD" not in up):
        kind = "HDD 機械硬碟"
        idle_w, max_w = 4.2, 8.8
    elif media == 4 or bus == 11 or "SSD" in up or "KINGSTON" in up or "SAMSUNG" in up or "CRUCIAL" in up:
        kind = "SATA SSD"
        idle_w, max_w = 0.5, 3.0
    elif "SCSI" in iface and "SSD" in up:
        kind = "NVMe / SCSI SSD"
        idle_w, max_w = 1.4, 5.5
    else:
        kind = "SATA / 儲存裝置"
        idle_w, max_w = 1.2, 4.5

    return {
        "name": name,
        "kind": kind,
        "size_gb": size_gb,
        "idle_w": idle_w,
        "max_w": max_w,
    }


def _smbios_ddr_label(code: int) -> str:
    return {24: "DDR3", 26: "DDR4", 34: "DDR5", 20: "DDR", 21: "DDR2"}.get(code, "DDR")


def discover_all_hardware() -> dict[str, Any]:
    cim = run_cim_inventory()
    computers = _ensure_list(cim.get("computer"))
    comp = computers[0] if computers else {}
    pc_sys_type = int(comp.get("PCSystemType") or 1)
    batteries = _ensure_list(cim.get("batteries"))
    is_laptop = (pc_sys_type == 2) or (len(batteries) > 0)

    baseboards = _ensure_list(cim.get("baseboard"))
    bb = baseboards[0] if baseboards else {}
    mb_mfg = str(bb.get("Manufacturer") or "").strip()
    mb_prod = str(bb.get("Product") or comp.get("Model") or "").strip()
    mb_name = f"{mb_mfg} {mb_prod}".strip() or "主機板"

    cpus_raw = _ensure_list(cim.get("cpus"))
    cpus: list[dict[str, Any]] = []
    for idx, c in enumerate(cpus_raw):
        cname = str(c.get("Name") or "Unknown CPU").strip()
        cores = int(c.get("NumberOfCores") or 4)
        threads = int(c.get("NumberOfLogicalProcessors") or cores)
        max_mhz = int(c.get("MaxClockSpeed") or 3000)
        profile = lookup_cpu_profile(cname, cores=cores, is_laptop=is_laptop)
        cpus.append(
            {
                "id": f"cpu_{idx}",
                "name": cname,
                "cores": cores,
                "threads": threads,
                "max_mhz": max_mhz,
                "profile": profile,
            }
        )

    gpus_raw = _ensure_list(cim.get("gpus"))
    gpus: list[dict[str, Any]] = []
    for idx, g in enumerate(gpus_raw):
        gname = str(g.get("Name") or "").strip()
        if is_virtual_gpu(gname):
            continue
        profile = lookup_gpu_profile(gname, is_laptop=is_laptop)
        gpus.append(
            {
                "id": f"gpu_{idx}",
                "name": gname,
                "pnp_id": str(g.get("PNPDeviceID") or ""),
                "profile": profile,
            }
        )

    mem_raw = _ensure_list(cim.get("memory"))
    dimm_count = len(mem_raw)
    total_ram_gb = round(sum(int(m.get("Capacity") or 0) for m in mem_raw) / (1024 ** 3), 1)
    if total_ram_gb <= 0 and comp.get("TotalPhysicalMemory"):
        total_ram_gb = round(int(comp.get("TotalPhysicalMemory")) / (1024 ** 3), 1)
    ddr_type = _smbios_ddr_label(int(mem_raw[0].get("SMBIOSMemoryType") or 0)) if mem_raw else "DDR4"
    ram_speed = int(mem_raw[0].get("Speed") or 0) if mem_raw else 0
    ram_mfg = str(mem_raw[0].get("Manufacturer") or "").strip() if mem_raw else ""

    disks_raw = _ensure_list(cim.get("disks"))
    disks = [_classify_disk(d) for d in disks_raw]

    monitors = enumerate_active_displays()
    peripherals = deduplicate_peripherals(_ensure_list(cim.get("pnp")))

    return {
        "system": {
            "form_factor": "筆記型電腦" if is_laptop else "桌上型電腦",
            "is_laptop": is_laptop,
            "manufacturer": str(comp.get("Manufacturer") or "").strip(),
            "model": str(comp.get("Model") or "").strip(),
            "motherboard": mb_name,
        },
        "cpus": cpus,
        "gpus": gpus,
        "ram": {
            "dimm_count": max(1, dimm_count),
            "total_gb": total_ram_gb,
            "ddr_type": ddr_type,
            "speed_mhz": ram_speed,
            "manufacturer": ram_mfg,
            "label": f"{max(1, dimm_count)}× {int(round(total_ram_gb / max(1, dimm_count)))}GB {ddr_type}"
            + (f"-{ram_speed}" if ram_speed else ""),
        },
        "disks": disks,
        "monitors": monitors,
        "peripherals": peripherals,
        "has_battery": len(batteries) > 0,
    }
