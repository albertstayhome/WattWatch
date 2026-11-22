from __future__ import annotations

import ctypes
import os
from typing import Any


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


class NvmlSensor:
    def __init__(self):
        self._lib = None
        self._initialized = False
        self._handles: list[tuple[int, ctypes.c_void_p, str]] = []
        self._init_nvml()

    def _init_nvml(self) -> None:
        candidates = [
            r"C:\Windows\System32\nvml.dll",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll",
        ]
        dll_path = next((p for p in candidates if os.path.exists(p)), None)
        if not dll_path:
            return
        try:
            lib = ctypes.CDLL(dll_path)
            rc = lib.nvmlInit_v2()
            if rc != 0:
                return
            cnt = ctypes.c_uint()
            if lib.nvmlDeviceGetCount_v2(ctypes.byref(cnt)) != 0:
                lib.nvmlShutdown()
                return

            handles = []
            for i in range(cnt.value):
                h = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)) == 0:
                    buf = ctypes.create_string_buffer(128)
                    name = f"NVIDIA GPU {i}"
                    if lib.nvmlDeviceGetName(h, buf, 128) == 0:
                        name = buf.value.decode("utf-8", errors="ignore").strip()
                    handles.append((i, h, name))

            self._lib = lib
            self._handles = handles
            self._initialized = len(handles) > 0
        except Exception:
            self._initialized = False

    @property
    def available(self) -> bool:
        return self._initialized

    def poll(self) -> list[dict[str, Any]]:
        if not self._initialized or self._lib is None:
            return []
        out: list[dict[str, Any]] = []
        for idx, h, name in self._handles:
            power_w = None
            limit_w = None
            temp_c = None
            util_pct = None
            fan_pct = None

            mw = ctypes.c_uint()
            if self._lib.nvmlDeviceGetPowerUsage(h, ctypes.byref(mw)) == 0:
                power_w = round(mw.value / 1000.0, 2)

            lim = ctypes.c_uint()
            if self._lib.nvmlDeviceGetPowerManagementLimit(h, ctypes.byref(lim)) == 0:
                limit_w = round(lim.value / 1000.0, 1)

            t = ctypes.c_uint()
            if self._lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) == 0:
                temp_c = int(t.value)

            util = _NvmlUtilization()
            if self._lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(util)) == 0:
                util_pct = int(util.gpu)

            fan = ctypes.c_uint()
            if self._lib.nvmlDeviceGetFanSpeed(h, ctypes.byref(fan)) == 0:
                fan_pct = int(fan.value)

            out.append(
                {
                    "index": idx,
                    "name": name,
                    "power_w": power_w,
                    "power_limit_w": limit_w,
                    "temp_c": temp_c,
                    "util_pct": util_pct,
                    "fan_pct": fan_pct,
                }
            )
        return out

    def shutdown(self) -> None:
        if self._initialized and self._lib is not None:
            try:
                self._lib.nvmlShutdown()
            except Exception:
                pass
        self._initialized = False
        self._handles = []
