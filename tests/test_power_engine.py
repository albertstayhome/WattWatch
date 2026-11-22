import datetime
import struct
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import create_app
from core.config import ConfigManager
from core.database import (
    estimate_cpu_watts,
    estimate_gpu_watts,
    estimate_monitor_watts,
    get_psu_efficiency,
    is_virtual_gpu,
    lookup_cpu_profile,
    lookup_gpu_profile,
)
from core.hardware_discovery import parse_edid_bytes
from core.power_engine import PowerEngine
from core.storage import Storage
from core.tariff import get_rate_for_date_hour, get_tou_2tier_info


def _build_test_edid(mfg: str = "SGT", prod_code: int = 0x2700, w_cm: int = 60, h_cm: int = 33, year: int = 2023, name: str = "Monitor") -> bytes:
    buf = bytearray(128)
    buf[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    c1, c2, c3 = (ord(mfg[0]) - 64) & 0x1F, (ord(mfg[1]) - 64) & 0x1F, (ord(mfg[2]) - 64) & 0x1F
    mfg_word = (c1 << 10) | (c2 << 5) | c3
    buf[8] = (mfg_word >> 8) & 0xFF
    buf[9] = mfg_word & 0xFF
    buf[10] = prod_code & 0xFF
    buf[11] = (prod_code >> 8) & 0xFF
    buf[16] = 10
    buf[17] = year - 1990
    buf[21] = w_cm
    buf[22] = h_cm
    # Descriptor block 0 at 54: tag 0xFC monitor name
    buf[54:59] = b"\x00\x00\x00\xfc\x00"
    name_bytes = name.encode("ascii")[:13]
    buf[59 : 59 + len(name_bytes)] = name_bytes
    return bytes(buf)


class TestPowerEngineAndDatabase(unittest.TestCase):
    def test_edid_parser_and_dtd_fallback(self):
        edid = _build_test_edid("SGT", 0x2700, 60, 33, 2023, "Monitor")
        parsed = parse_edid_bytes(edid)
        self.assertEqual(parsed["mfg_code"], "SGT")
        self.assertEqual(parsed["pnp_id"], "SGT2700")
        self.assertEqual(parsed["year"], 2023)
        self.assertEqual(parsed["diagonal_in"], 27.0)

        # DTD fallback when basic cm bytes are 0
        edid_zero = bytearray(_build_test_edid("DEL", 0xA012, 0, 0, 2022, "U2723QE"))
        # Put non-zero pixel clock + 597mm x 336mm in DTD #1 (bytes 54..71)
        edid_zero[54] = 0x02
        edid_zero[55] = 0x3A
        edid_zero[66] = 597 & 0xFF
        edid_zero[67] = 336 & 0xFF
        edid_zero[68] = (((597 >> 8) & 0xF) << 4) | ((336 >> 8) & 0xF)
        parsed_dtd = parse_edid_bytes(bytes(edid_zero))
        self.assertAlmostEqual(parsed_dtd["width_cm"], 59.7, places=1)
        self.assertAlmostEqual(parsed_dtd["height_cm"], 33.6, places=1)
        self.assertAlmostEqual(parsed_dtd["diagonal_in"], 27.0, delta=0.2)

    def test_monitor_wattage_model(self):
        w_off = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "IPS", "off")
        w_disc = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "IPS", "disconnected")
        w_dim = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "IPS", "dimmed")
        w_low = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 30, "IPS", "on")
        w_high = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 100, "IPS", "on")
        w_custom = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "IPS", "on", custom_watts=42.5)
        w_oled = estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "OLED", "on")

        self.assertEqual(w_disc, 0.0)
        self.assertEqual(w_off, 0.5)
        self.assertLess(w_dim, w_low)
        self.assertLess(w_low, w_high)
        self.assertTrue(22.0 <= w_low <= 45.0)
        self.assertEqual(w_custom, 42.5)
        self.assertLess(w_oled, estimate_monitor_watts(60, 33, 27.0, 2560, 1440, 144, 70, "IPS", "on"))

    def test_cpu_gpu_lookup_and_curves(self):
        cpu = lookup_cpu_profile("Intel(R) Xeon(R) CPU E5-2666 v3 @ 2.90GHz", cores=10)
        self.assertTrue(cpu["matched"])
        self.assertEqual(cpu["tdp_w"], 135.0)
        c_idle = estimate_cpu_watts(cpu, 2.0, 0.5)
        c_mid = estimate_cpu_watts(cpu, 50.0, 0.9)
        c_full = estimate_cpu_watts(cpu, 100.0, 1.0)
        self.assertLess(c_idle, c_mid)
        self.assertLess(c_mid, c_full)

        self.assertTrue(is_virtual_gpu("ToDesk Virtual Display Adapter"))
        self.assertTrue(is_virtual_gpu("Microsoft Basic Display Driver"))
        self.assertFalse(is_virtual_gpu("NVIDIA GeForce GTX 1660 SUPER"))

        gpu = lookup_gpu_profile("NVIDIA GeForce GTX 1660 SUPER")
        self.assertTrue(gpu["matched"])
        self.assertEqual(gpu["tdp_w"], 125.0)
        self.assertLess(estimate_gpu_watts(gpu, 5.0), estimate_gpu_watts(gpu, 90.0))

    def test_psu_efficiency_curve(self):
        eff_low_load = get_psu_efficiency(25.0, 650, "bronze")
        eff_mid_load = get_psu_efficiency(300.0, 650, "bronze")
        eff_gold = get_psu_efficiency(300.0, 650, "gold")
        self.assertLess(eff_low_load, eff_mid_load)
        self.assertLess(eff_mid_load, eff_gold)
        self.assertGreaterEqual(eff_low_load, 0.65)
        self.assertLessEqual(eff_gold, 0.96)

    def test_storage_persistence_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store = Storage(db_path=db_path)
            today = datetime.date.today()
            today_str = today.isoformat()
            ts_min = int(datetime.datetime.combine(today, datetime.time(14, 0)).timestamp())

            # First 30s flush + second 30s flush into the same minute bucket
            b1 = {
                "ts": ts_min,
                "date": today_str,
                "hour": 14,
                "total_ac_w": 150.0,
                "pc_ac_w": 115.0,
                "monitors_w": 35.0,
                "cpu_w": 30.0,
                "gpu_w": 15.0,
                "ram_w": 8.0,
                "storage_w": 3.0,
                "mb_fans_w": 30.0,
                "peripherals_w": 4.0,
                "psu_loss_w": 25.0,
                "external_w": 0.0,
                "duration_sec": 30.0,
                "monitor_on_sec": 30.0,
                "monitor_idle_waste_sec": 0.0,
                "monitor_idle_waste_wh": 0.0,
            }
            b2 = dict(b1)
            b2["monitor_idle_waste_sec"] = 30.0
            b2["monitor_idle_waste_wh"] = 34.5 * 30.0 / 3600.0

            d1 = {
                "date": today_str,
                "device_id": "mon_1",
                "device_name": "螢幕: SGT2700",
                "category": "外接螢幕",
                "energy_wh": 35.0 * 60.0 / 3600.0,
                "active_sec": 60.0,
                "idle_waste_wh": b2["monitor_idle_waste_wh"],
                "idle_waste_sec": 30.0,
            }
            store.record_flush([b1], [d1])
            store.record_flush([b2], [])

            summary = store.get_summary("today", rate_ntd=3.13, ref_date=today)
            self.assertAlmostEqual(summary["duration_sec"], 60.0, places=1)
            self.assertAlmostEqual(summary["total_wh"], 150.0 / 60.0, places=2)
            self.assertAlmostEqual(summary["monitors_wh"], 35.0 / 60.0, places=2)
            self.assertAlmostEqual(summary["monitor_idle_waste_sec"], 30.0, places=1)
            self.assertEqual(len(summary["devices"]), 1)

            csv_txt = store.export_csv("today", rate_ntd=3.13, ref_date=today)
            self.assertTrue(csv_txt.startswith("\ufeff"))
            self.assertIn("總耗電_牆壁AC(W)", csv_txt)
            self.assertIn("150.0", csv_txt)
            store.close()

    def test_fastapi_endpoints_with_lifespan(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConfigManager(path=Path(tmp) / "config.json")
            store = Storage(db_path=Path(tmp) / "watt.db")
            eng = PowerEngine(cfg, store)
            app = create_app(cfg, store, eng)
            with TestClient(app) as client:
                r_health = client.get("/api/health")
                self.assertEqual(r_health.status_code, 200)
                self.assertEqual(r_health.json()["app"], "WattWatch")

                r_live = client.get("/api/live")
                self.assertEqual(r_live.status_code, 200)
                live = r_live.json()
                self.assertGreater(live["total_ac_w"], 20.0)
                self.assertIn("components", live)
                self.assertIn("monitors", live)

                # Verify monitor power is NOT multiplied by PSU efficiency
                expected_total = round(live["pc_ac_w"] + live["monitors_w"] + live["external_w"], 2)
                self.assertAlmostEqual(live["total_ac_w"], expected_total, places=1)

                r_hw = client.get("/api/hardware")
                self.assertEqual(r_hw.status_code, 200)
                self.assertGreaterEqual(len(r_hw.json()["cpus"]), 1)

                r_cfg = client.post("/api/config", json={"electricity_rate_ntd": 4.24, "psu_efficiency_tier": "gold"})
                self.assertEqual(r_cfg.status_code, 200)
                self.assertEqual(r_cfg.json()["config"]["electricity_rate_ntd"], 4.24)

                r_hist = client.get("/api/history?range=today")
                self.assertEqual(r_hist.status_code, 200)

                r_csv = client.get("/api/export.csv?range=today")
                self.assertEqual(r_csv.status_code, 200)
                self.assertIn("text/csv", r_csv.headers["content-type"])

                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/static/styles.css").status_code, 200)
                self.assertEqual(client.get("/static/app.js").status_code, 200)

                # Adaptive polling mode transition check
                eng.register_ui_client()
                poll_active, mode_active = eng._apply_adaptive_mode()
                self.assertEqual(mode_active, "realtime_1s")
                self.assertEqual(poll_active, 1.0)

                eng.unregister_ui_client()
                eng._last_deep_eco = False
                poll_bg, mode_bg = eng._apply_adaptive_mode()
                self.assertEqual(mode_bg, "eco_3s")
                self.assertEqual(poll_bg, 3.0)

                eng._last_deep_eco = True
                poll_deep, mode_deep = eng._apply_adaptive_mode()
                self.assertEqual(mode_deep, "deep_eco_5s")
                self.assertEqual(poll_deep, 5.0)
                self.assertIn("tariff", live)
                self.assertEqual(live["tariff"]["mode"], "tou_2tier")

    def test_taipower_tou_2tier_tariff(self):
        # Summer weekday (2026-07-15 Wed)
        s_off = get_tou_2tier_info(datetime.datetime(2026, 7, 15, 8, 59))
        s_peak = get_tou_2tier_info(datetime.datetime(2026, 7, 15, 9, 0))
        s_wknd = get_tou_2tier_info(datetime.datetime(2026, 7, 18, 15, 0))  # Sat
        self.assertEqual(s_off["rate_ntd"], 2.06)
        self.assertEqual(s_peak["rate_ntd"], 5.16)
        self.assertEqual(s_wknd["rate_ntd"], 2.06)

        # Non-summer weekday (2026-11-23 Mon)
        ns_off_early = get_tou_2tier_info(datetime.datetime(2026, 11, 23, 5, 30))
        ns_peak_am = get_tou_2tier_info(datetime.datetime(2026, 11, 23, 6, 0))
        ns_off_noon = get_tou_2tier_info(datetime.datetime(2026, 11, 23, 12, 30))
        ns_peak_pm = get_tou_2tier_info(datetime.datetime(2026, 11, 23, 14, 0))
        ns_wknd = get_tou_2tier_info(datetime.datetime(2026, 11, 22, 13, 30))  # Sun
        self.assertEqual(ns_off_early["rate_ntd"], 1.99)
        self.assertEqual(ns_peak_am["rate_ntd"], 4.93)
        self.assertEqual(ns_off_noon["rate_ntd"], 1.99)
        self.assertEqual(ns_peak_pm["rate_ntd"], 4.93)
        self.assertEqual(ns_wknd["rate_ntd"], 1.99)

        # Historical date/hour lookup
        r_mon_noon, _ = get_rate_for_date_hour("2026-11-23", 12, {"tariff_mode": "tou_2tier"})
        r_mon_eve, _ = get_rate_for_date_hour("2026-11-23", 20, {"tariff_mode": "tou_2tier"})
        self.assertEqual(r_mon_noon, 1.99)
        self.assertEqual(r_mon_eve, 4.93)


if __name__ == "__main__":
    unittest.main()
