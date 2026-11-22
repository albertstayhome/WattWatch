from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from typing import Any

from core.hardware_discovery import ATTACHED_TO_DESKTOP, _DISPLAY_DEVICEW

# Win32 constants for display power notification & idle time
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000
WM_QUIT = 0x0012
CCHDEVICENAME = 32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# GUID_CONSOLE_DISPLAY_STATE = {6FE69556-704A-47A0-8F24-C28D936FDA47}
_GUID_CONSOLE_DISPLAY_STATE = _GUID(
    0x6FE69556,
    0x704A,
    0x47A0,
    (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47),
)


class _POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", _GUID),
        ("DataLength", wt.DWORD),
        ("Data", ctypes.c_ubyte * 4),
    ]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("dwTime", wt.DWORD),
    ]


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", wt.HANDLE),
        ("szPhysicalMonitorDescription", wt.WCHAR * 128),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * CCHDEVICENAME),
    ]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HANDLE),
    ]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


def get_user_idle_seconds() -> float:
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        now_tick = kernel32.GetTickCount() & 0xFFFFFFFF
        elapsed_ms = (now_tick - lii.dwTime) & 0xFFFFFFFF
        return round(elapsed_ms / 1000.0, 2)
    except Exception:
        return 0.0


def _get_attached_display_names() -> set[str]:
    user32 = ctypes.windll.user32
    names: set[str] = set()
    idx = 0
    while True:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, idx, ctypes.byref(dd), 0):
            break
        idx += 1
        if dd.StateFlags & ATTACHED_TO_DESKTOP:
            names.add(dd.DeviceName)
    return names


def _poll_ddcci_once() -> dict[str, dict[str, Any]]:
    user32 = ctypes.windll.user32
    dxva2 = ctypes.windll.dxva2
    results: dict[str, dict[str, Any]] = {}

    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wt.BOOL

    def _cb(hmon: int, hdc: int, lprc: Any, dw_data: int) -> int:
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        dev_name = ""
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dev_name = mi.szDevice

        cnt = wt.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(cnt)) or cnt.value == 0:
            return 1

        arr = (_PHYSICAL_MONITOR * cnt.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, cnt, arr):
            return 1

        try:
            pm = arr[0]
            mn, cur_b, mx_b = wt.DWORD(), wt.DWORD(), wt.DWORD()
            ok_b = bool(dxva2.GetMonitorBrightness(pm.hPhysicalMonitor, ctypes.byref(mn), ctypes.byref(cur_b), ctypes.byref(mx_b)))

            pvct = wt.DWORD()
            cur_c, mx_c = wt.DWORD(), wt.DWORD()
            ok_c = bool(
                dxva2.GetVCPFeatureAndVCPFeatureReply(
                    pm.hPhysicalMonitor, ctypes.c_ubyte(0x12), ctypes.byref(pvct), ctypes.byref(cur_c), ctypes.byref(mx_c)
                )
            )

            cur_p, mx_p = wt.DWORD(), wt.DWORD()
            ok_p = bool(
                dxva2.GetVCPFeatureAndVCPFeatureReply(
                    pm.hPhysicalMonitor, ctypes.c_ubyte(0xD6), ctypes.byref(pvct), ctypes.byref(cur_p), ctypes.byref(mx_p)
                )
            )

            b_pct = None
            if ok_b and mx_b.value > mn.value:
                b_pct = round(((cur_b.value - mn.value) / (mx_b.value - mn.value)) * 100.0, 1)

            # Firmware Stub Guard: many HDMI/DP monitors report Brightness=0 & Contrast=0 even when brightly lit
            reliable = ok_b and not (cur_b.value == 0 and ok_c and cur_c.value == 0)

            key = dev_name or f"HMONITOR_{hmon}"
            results[key] = {
                "ddcci_supported": ok_b or ok_p,
                "ddcci_brightness": b_pct,
                "ddcci_brightness_reliable": reliable,
                "ddcci_contrast": int(cur_c.value) if ok_c else None,
                "ddcci_power_mode": int(cur_p.value) if ok_p else None,
                "ddcci_power_ok": ok_p,
            }
        finally:
            dxva2.DestroyPhysicalMonitors(cnt, arr)
        return 1

    cb_fn = _MONITORENUMPROC(_cb)
    user32.EnumDisplayMonitors(None, None, cb_fn, 0)
    return results


class MonitorSensor:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._console_display_state: int = 1  # 0=Off, 1=On, 2=Dimmed
        self._ddcci_by_device: dict[str, dict[str, Any]] = {}
        self._attached_devices: set[str] = set()
        self._wnd_thread: threading.Thread | None = None
        self._poll_thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._wnd_tid: int | None = None
        self._wndproc_ref = None
        self._eco_mode: bool = False

    def start(self) -> None:
        self._stop_event.clear()
        self._attached_devices = _get_attached_display_names()
        try:
            self._ddcci_by_device = _poll_ddcci_once()
        except Exception:
            pass

        self._wnd_thread = threading.Thread(target=self._power_listener_loop, name="MonitorPowerNotify", daemon=True)
        self._wnd_thread.start()

        self._poll_thread = threading.Thread(target=self._ddcci_loop, name="MonitorDdcciPoll", daemon=True)
        self._poll_thread.start()

    def _power_listener_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._wnd_tid = kernel32.GetCurrentThreadId()

        def _wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and lparam:
                try:
                    setting = ctypes.cast(lparam, ctypes.POINTER(_POWERBROADCAST_SETTING)).contents
                    g = setting.PowerSetting
                    if (
                        g.Data1 == _GUID_CONSOLE_DISPLAY_STATE.Data1
                        and g.Data2 == _GUID_CONSOLE_DISPLAY_STATE.Data2
                        and g.Data3 == _GUID_CONSOLE_DISPLAY_STATE.Data3
                    ):
                        state_val = int(setting.Data[0])
                        with self._lock:
                            self._console_display_state = state_val
                except Exception:
                    pass
                return 1
            return user32.DefWindowProcW(hwnd, msg, wt.WPARAM(wparam), wt.LPARAM(lparam))

        self._wndproc_ref = _WNDPROC(_wndproc)
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]
        user32.CreateWindowExW.restype = wt.HWND
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.DestroyWindow.restype = wt.BOOL
        user32.RegisterPowerSettingNotification.argtypes = [wt.HANDLE, ctypes.POINTER(_GUID), wt.DWORD]
        user32.RegisterPowerSettingNotification.restype = wt.HANDLE
        user32.UnregisterPowerSettingNotification.argtypes = [wt.HANDLE]
        user32.UnregisterPowerSettingNotification.restype = wt.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        user32.GetMessageW.restype = wt.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.TranslateMessage.restype = wt.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostThreadMessageW.restype = wt.BOOL

        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"WattWatchPowerMsg_{id(self)}"
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = cls_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            return

        hwnd = user32.CreateWindowExW(0, cls_name, "WattWatchPower", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            return
        self._hwnd = hwnd

        hnotify = user32.RegisterPowerSettingNotification(
            hwnd, ctypes.byref(_GUID_CONSOLE_DISPLAY_STATE), DEVICE_NOTIFY_WINDOW_HANDLE
        )

        msg = wt.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if hnotify:
            user32.UnregisterPowerSettingNotification(hnotify)
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(cls_name, hinst)

    def set_eco_mode(self, eco: bool) -> None:
        with self._lock:
            self._eco_mode = bool(eco)

    def _ddcci_loop(self) -> None:
        while True:
            with self._lock:
                eco = self._eco_mode
                console_state = self._console_display_state
            wait_sec = 15.0 if eco else 5.0
            if self._stop_event.wait(wait_sec):
                break
            with self._lock:
                eco = self._eco_mode
                console_state = self._console_display_state
            # When screen is already turned off by OS and in eco mode, skip DDC/CI probe
            if eco and console_state == 0:
                continue
            try:
                attached = _get_attached_display_names()
                ddcci = _poll_ddcci_once()
                with self._lock:
                    self._attached_devices = attached
                    self._ddcci_by_device = ddcci
            except Exception:
                continue

    def get_monitor_states(self, discovered_monitors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        with self._lock:
            console_state = self._console_display_state
            attached = set(self._attached_devices)
            ddcci_map = dict(self._ddcci_by_device)

        states: dict[str, dict[str, Any]] = {}
        for mon in discovered_monitors:
            dev_name = mon["device_name"]
            ddcci = ddcci_map.get(dev_name, {})
            ddcci_power_ok = bool(ddcci.get("ddcci_power_ok", False))
            ddcci_power_mode = ddcci.get("ddcci_power_mode")

            if dev_name not in attached:
                state = "disconnected"
            elif console_state == 0:
                state = "off"
            elif ddcci_power_ok and ddcci_power_mode in (2, 3, 4, 5):
                state = "off"
            elif console_state == 2:
                state = "dimmed"
            else:
                state = "on"

            states[mon["id"]] = {
                "state": state,
                "console_display_state": console_state,
                "ddcci_supported": bool(ddcci.get("ddcci_supported", False)),
                "ddcci_brightness": ddcci.get("ddcci_brightness"),
                "ddcci_brightness_reliable": bool(ddcci.get("ddcci_brightness_reliable", False)),
                "ddcci_contrast": ddcci.get("ddcci_contrast"),
                "ddcci_power_mode": ddcci_power_mode,
            }
        return states

    def stop(self) -> None:
        self._stop_event.set()
        if self._wnd_tid is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._wnd_tid, WM_QUIT, 0, 0)
            except Exception:
                pass
