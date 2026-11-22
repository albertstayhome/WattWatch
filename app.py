from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import subprocess
import sys
import threading
import traceback
import webbrowser

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core.config import BASE_DIR, ConfigManager, DATA_DIR, PSU_TIERS, RESOURCE_DIR, TAIPOWER_PRESETS, set_autostart
from core.power_engine import PowerEngine
from core.sensors_lhm import check_pawnio_installed
from core.storage import Storage
from core.tariff import TOU_2TIER_RATES, get_effective_rate_info
from core.tray import TrayController

WEB_DIR = (RESOURCE_DIR / "web") if (RESOURCE_DIR / "web").exists() else (BASE_DIR / "web")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_app_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def open_app_window(url: str) -> None:
    browser = find_app_browser()
    if browser:
        profile_dir = DATA_DIR / "ui_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    "--window-size=1280,860",
                    f"--user-data-dir={str(profile_dir)}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass
    webbrowser.open(url)


def check_existing_instance(port: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/api/health", timeout=0.8)
        if r.status_code == 200 and r.json().get("app") == "WattWatch":
            return True
    except Exception:
        pass
    return False


def create_app(
    config: ConfigManager | None = None,
    storage: Storage | None = None,
    engine: PowerEngine | None = None,
) -> FastAPI:
    cfg = config or ConfigManager()
    store = storage or Storage()
    eng = engine or PowerEngine(cfg, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng.start()
        try:
            yield
        finally:
            eng.stop()
            store.close()

    app = FastAPI(title="WattWatch 電量偵測管家", version="1.0.0", lifespan=lifespan)
    app.state.config = cfg
    app.state.storage = store
    app.state.engine = eng

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def index():
        idx = WEB_DIR / "index.html"
        if not idx.exists():
            raise HTTPException(status_code=404, detail="UI not found")
        return FileResponse(str(idx))

    @app.get("/api/health")
    async def health():
        return {"app": "WattWatch", "version": "1.0.0", "status": "ok"}

    @app.get("/api/live")
    async def api_live():
        return eng.get_live_snapshot()

    @app.websocket("/ws")
    async def ws_live(websocket: WebSocket):
        await websocket.accept()
        eng.register_ui_client()
        try:
            while True:
                snap = eng.get_live_snapshot()
                await websocket.send_json(snap)
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            eng.unregister_ui_client()

    @app.get("/api/history")
    async def api_history(range: str = Query("today", pattern="^(today|7d|30d|session)$")):
        return eng.get_history(range)

    @app.get("/api/hardware")
    async def api_hardware():
        return eng.hardware

    @app.post("/api/hardware/rescan")
    async def api_hardware_rescan():
        hw = await asyncio.to_thread(eng.rescan_hardware)
        return {"ok": True, "hardware": hw}

    @app.get("/api/config")
    async def api_get_config():
        return {
            "config": cfg.get(),
            "presets": TAIPOWER_PRESETS,
            "psu_tiers": PSU_TIERS,
            "tou_rates": TOU_2TIER_RATES,
        }

    @app.post("/api/config")
    async def api_update_config(payload: dict[str, Any]):
        updated = cfg.update(payload)
        return {"ok": True, "config": updated}

    @app.post("/api/session/reset")
    async def api_reset_session():
        eng.reset_session()
        return {"ok": True}

    @app.post("/api/pawnio/install")
    async def api_install_pawnio():
        if check_pawnio_installed():
            return {"ok": True, "already_installed": True, "message": "PawnIO 驅動已安裝，請以系統管理員權限重新啟動 WattWatch 即可啟用 CPU 實測功耗。"}
        try:
            ps_cmd = (
                "Start-Process winget -Verb RunAs -Wait "
                "-ArgumentList 'install --id namazso.PawnIO --accept-package-agreements --accept-source-agreements'"
            )
            proc = await asyncio.to_thread(
                subprocess.run,
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
                creationflags=_CREATE_NO_WINDOW,
            )
            installed = check_pawnio_installed()
            if installed:
                return {"ok": True, "installed": True, "message": "PawnIO 安裝完成！請重新以系統管理員身分啟動 WattWatch 以啟用 CPU 實測感測器。"}
            return JSONResponse(
                status_code=200,
                content={
                    "ok": False,
                    "installed": False,
                    "message": f"未能確認 PawnIO 安裝狀態 (code={proc.returncode})，您可手動執行 winget install namazso.PawnIO。",
                },
            )
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "message": f"啟動安裝失敗：{exc}"},
            )

    @app.get("/api/export.csv")
    async def api_export_csv(range: str = Query("today", pattern="^(today|7d|30d)$")):
        eng.flush_now()
        current_cfg = cfg.get()
        rate = float(get_effective_rate_info(current_cfg)["rate_ntd"])
        csv_text = store.export_csv(range_key=range, rate_ntd=rate, cfg=current_cfg)
        date_tag = datetime.date.today().strftime("%Y%m%d")
        filename = f"wattwatch_{range}_{date_tag}.csv"
        return Response(
            content=csv_text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="WattWatch 電量偵測管家")
    parser.add_argument("--port", type=int, default=None, help="HTTP server port")
    parser.add_argument("--no-browser", "--background", dest="no_browser", action="store_true", help="Do not launch desktop UI window")
    parser.add_argument("--no-tray", action="store_true", help="Disable system tray icon")
    parser.add_argument("--enable-autostart", action="store_true", help="Register boot auto-start scheduled task and exit")
    parser.add_argument("--disable-autostart", action="store_true", help="Remove boot auto-start scheduled task and exit")
    args = parser.parse_args()

    if args.enable_autostart:
        ok, _ = set_autostart(True)
        sys.exit(0 if ok else 1)
    if args.disable_autostart:
        ok, _ = set_autostart(False)
        sys.exit(0 if ok else 1)

    cfg = ConfigManager()
    port = args.port or int(cfg.get().get("port", 18742))
    url = f"http://127.0.0.1:{port}"

    if check_existing_instance(port):
        if not args.no_browser:
            open_app_window(url)
        return

    store = Storage()
    eng = PowerEngine(cfg, store)
    fastapi_app = create_app(cfg, store, eng)

    uv_config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        use_colors=False,
    )
    server = uvicorn.Server(uv_config)
    tray: TrayController | None = None

    def _quit_app() -> None:
        server.should_exit = True
        if tray:
            tray.stop()

    if not args.no_tray:
        tray = TrayController(
            engine=eng,
            on_open_ui=lambda: open_app_window(url),
            on_rescan=lambda: threading.Thread(target=eng.rescan_hardware, daemon=True).start(),
            on_quit=_quit_app,
        )
        tray.start()

    if not args.no_browser:
        threading.Timer(1.3, lambda: open_app_window(url)).start()

    try:
        server.run()
    finally:
        if tray:
            tray.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "startup_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise
