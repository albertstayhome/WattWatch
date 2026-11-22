from __future__ import annotations

import threading
import time
from typing import Callable

from PIL import Image, ImageDraw
import pystray

from core.config import get_autostart, set_autostart
from core.power_engine import PowerEngine


def create_bolt_icon() -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_color = (15, 23, 42, 255)
    ring_color = (56, 189, 248, 255)
    bolt_color = (34, 211, 238, 255)

    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=bg_color, outline=ring_color, width=3)
    bolt_pts = [(36, 11), (18, 35), (30, 35), (26, 53), (46, 28), (34, 28)]
    draw.polygon(bolt_pts, fill=bolt_color)
    return img


class TrayController:
    def __init__(
        self,
        engine: PowerEngine,
        on_open_ui: Callable[[], None],
        on_rescan: Callable[[], None],
        on_quit: Callable[[], None],
    ):
        self.engine = engine
        self.on_open_ui = on_open_ui
        self.on_rescan = on_rescan
        self.on_quit = on_quit
        self._stop_event = threading.Event()
        self._icon: pystray.Icon | None = None
        self._updater_thread: threading.Thread | None = None

    def start(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("開啟 WattWatch 主畫面", lambda icon, item: self.on_open_ui(), default=True),
            pystray.MenuItem(
                "開機自動啟動",
                self._toggle_autostart,
                checked=lambda item: get_autostart(),
            ),
            pystray.MenuItem("重新掃描硬體", lambda icon, item: self.on_rescan()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("結束背景監控", lambda icon, item: self.on_quit()),
        )
        self._icon = pystray.Icon(
            "WattWatch",
            create_bolt_icon(),
            "WattWatch 電量偵測管家 (啟動中...)",
            menu,
        )
        try:
            self._icon.run_detached()
        except Exception:
            threading.Thread(target=self._icon.run, daemon=True).start()

        self._stop_event.clear()
        self._updater_thread = threading.Thread(target=self._update_loop, name="TrayTooltipLoop", daemon=True)
        self._updater_thread.start()

    @staticmethod
    def _toggle_autostart(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        set_autostart(not get_autostart())
        try:
            icon.update_menu()
        except Exception:
            pass

    def _update_loop(self) -> None:
        while not self._stop_event.wait(5.0):
            if self._icon is None:
                continue
            try:
                snap = self.engine.get_live_snapshot()
                total_w = snap.get("total_ac_w", 0.0)
                mon_w = snap.get("monitors_w", 0.0)
                cost = snap.get("today", {}).get("total_cost_ntd", 0.0)
                # Windows tray title max 127 chars
                self._icon.title = f"WattWatch: 總耗電 {total_w:.0f}W | 螢幕 {mon_w:.0f}W | 今日 NT${cost:.1f}"[:120]
            except Exception:
                continue

    def stop(self) -> None:
        self._stop_event.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
