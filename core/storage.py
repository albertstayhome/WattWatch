from __future__ import annotations

import calendar
import csv
import datetime
import io
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.config import DATA_DIR
from core.tariff import get_rate_for_date_hour

DB_PATH = DATA_DIR / "wattwatch.db"


class Storage:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS minute_samples (
                    ts INTEGER PRIMARY KEY,
                    date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    total_ac_w REAL NOT NULL,
                    pc_ac_w REAL NOT NULL,
                    monitors_w REAL NOT NULL,
                    cpu_w REAL NOT NULL,
                    gpu_w REAL NOT NULL,
                    ram_w REAL NOT NULL,
                    storage_w REAL NOT NULL,
                    mb_fans_w REAL NOT NULL,
                    peripherals_w REAL NOT NULL,
                    psu_loss_w REAL NOT NULL,
                    external_w REAL NOT NULL,
                    duration_sec REAL NOT NULL,
                    monitor_on_sec REAL NOT NULL,
                    monitor_idle_waste_sec REAL NOT NULL,
                    monitor_idle_waste_wh REAL NOT NULL
                );
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_minute_date ON minute_samples(date);")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_daily (
                    date TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    energy_wh REAL NOT NULL,
                    active_sec REAL NOT NULL,
                    idle_waste_wh REAL NOT NULL,
                    idle_waste_sec REAL NOT NULL,
                    cost_ntd REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (date, device_id)
                );
                """
            )
            try:
                self._conn.execute("ALTER TABLE device_daily ADD COLUMN cost_ntd REAL NOT NULL DEFAULT 0.0;")
            except sqlite3.OperationalError:
                pass
            self._conn.commit()

    def record_flush(
        self,
        minute_buckets: list[dict[str, Any]],
        device_deltas: list[dict[str, Any]],
    ) -> None:
        if not minute_buckets and not device_deltas:
            return
        with self._lock:
            cur = self._conn.cursor()
            for b in minute_buckets:
                dur = float(b.get("duration_sec", 0.0))
                if dur <= 0:
                    continue
                cur.execute(
                    """
                    INSERT INTO minute_samples (
                        ts, date, hour,
                        total_ac_w, pc_ac_w, monitors_w, cpu_w, gpu_w,
                        ram_w, storage_w, mb_fans_w, peripherals_w, psu_loss_w, external_w,
                        duration_sec, monitor_on_sec, monitor_idle_waste_sec, monitor_idle_waste_wh
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ts) DO UPDATE SET
                        total_ac_w = (minute_samples.total_ac_w * minute_samples.duration_sec + excluded.total_ac_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        pc_ac_w = (minute_samples.pc_ac_w * minute_samples.duration_sec + excluded.pc_ac_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        monitors_w = (minute_samples.monitors_w * minute_samples.duration_sec + excluded.monitors_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        cpu_w = (minute_samples.cpu_w * minute_samples.duration_sec + excluded.cpu_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        gpu_w = (minute_samples.gpu_w * minute_samples.duration_sec + excluded.gpu_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        ram_w = (minute_samples.ram_w * minute_samples.duration_sec + excluded.ram_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        storage_w = (minute_samples.storage_w * minute_samples.duration_sec + excluded.storage_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        mb_fans_w = (minute_samples.mb_fans_w * minute_samples.duration_sec + excluded.mb_fans_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        peripherals_w = (minute_samples.peripherals_w * minute_samples.duration_sec + excluded.peripherals_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        psu_loss_w = (minute_samples.psu_loss_w * minute_samples.duration_sec + excluded.psu_loss_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        external_w = (minute_samples.external_w * minute_samples.duration_sec + excluded.external_w * excluded.duration_sec) / (minute_samples.duration_sec + excluded.duration_sec),
                        duration_sec = minute_samples.duration_sec + excluded.duration_sec,
                        monitor_on_sec = minute_samples.monitor_on_sec + excluded.monitor_on_sec,
                        monitor_idle_waste_sec = minute_samples.monitor_idle_waste_sec + excluded.monitor_idle_waste_sec,
                        monitor_idle_waste_wh = minute_samples.monitor_idle_waste_wh + excluded.monitor_idle_waste_wh
                    """,
                    (
                        int(b["ts"]),
                        b["date"],
                        int(b["hour"]),
                        float(b["total_ac_w"]),
                        float(b["pc_ac_w"]),
                        float(b["monitors_w"]),
                        float(b["cpu_w"]),
                        float(b["gpu_w"]),
                        float(b["ram_w"]),
                        float(b["storage_w"]),
                        float(b["mb_fans_w"]),
                        float(b["peripherals_w"]),
                        float(b["psu_loss_w"]),
                        float(b["external_w"]),
                        dur,
                        float(b.get("monitor_on_sec", 0.0)),
                        float(b.get("monitor_idle_waste_sec", 0.0)),
                        float(b.get("monitor_idle_waste_wh", 0.0)),
                    ),
                )

            for d in device_deltas:
                cur.execute(
                    """
                    INSERT INTO device_daily (
                        date, device_id, device_name, category,
                        energy_wh, active_sec, idle_waste_wh, idle_waste_sec, cost_ntd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, device_id) DO UPDATE SET
                        device_name = excluded.device_name,
                        category = excluded.category,
                        energy_wh = device_daily.energy_wh + excluded.energy_wh,
                        active_sec = device_daily.active_sec + excluded.active_sec,
                        idle_waste_wh = device_daily.idle_waste_wh + excluded.idle_waste_wh,
                        idle_waste_sec = device_daily.idle_waste_sec + excluded.idle_waste_sec,
                        cost_ntd = device_daily.cost_ntd + excluded.cost_ntd
                    """,
                    (
                        d["date"],
                        d["device_id"],
                        d["device_name"],
                        d["category"],
                        float(d["energy_wh"]),
                        float(d.get("active_sec", 0.0)),
                        float(d.get("idle_waste_wh", 0.0)),
                        float(d.get("idle_waste_sec", 0.0)),
                        float(d.get("cost_ntd", 0.0)),
                    ),
                )
            self._conn.commit()

    @staticmethod
    def _date_bounds(range_key: str, ref_date: datetime.date | None = None) -> tuple[str, str]:
        today = ref_date or datetime.date.today()
        if range_key == "7d":
            start = today - datetime.timedelta(days=6)
        elif range_key == "30d":
            start = today - datetime.timedelta(days=29)
        else:
            start = today
        return start.isoformat(), today.isoformat()

    @staticmethod
    def _month_bounds(year: int, month: int) -> tuple[str, str, int]:
        num_days = calendar.monthrange(year, month)[1]
        start = datetime.date(year, month, 1).isoformat()
        end = datetime.date(year, month, num_days).isoformat()
        return start, end, num_days

    def get_available_months(self) -> list[str]:
        cur_ym = datetime.date.today().strftime("%Y-%m")
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT substr(date, 1, 7) AS ym FROM minute_samples ORDER BY ym DESC"
            ).fetchall()
        months = [str(r["ym"]) for r in rows if r["ym"]]
        if cur_ym not in months:
            months.insert(0, cur_ym)
        return sorted(set(months), reverse=True)

    def _query_range_summary(
        self,
        start_date: str,
        end_date: str,
        range_key: str,
        rate_ntd: float = 3.13,
        cfg: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_ac_w * duration_sec / 3600.0), 0.0) AS total_wh,
                    COALESCE(SUM(pc_ac_w * duration_sec / 3600.0), 0.0) AS pc_ac_wh,
                    COALESCE(SUM(monitors_w * duration_sec / 3600.0), 0.0) AS monitors_wh,
                    COALESCE(SUM(cpu_w * duration_sec / 3600.0), 0.0) AS cpu_wh,
                    COALESCE(SUM(gpu_w * duration_sec / 3600.0), 0.0) AS gpu_wh,
                    COALESCE(SUM(ram_w * duration_sec / 3600.0), 0.0) AS ram_wh,
                    COALESCE(SUM(storage_w * duration_sec / 3600.0), 0.0) AS storage_wh,
                    COALESCE(SUM(mb_fans_w * duration_sec / 3600.0), 0.0) AS mb_fans_wh,
                    COALESCE(SUM(peripherals_w * duration_sec / 3600.0), 0.0) AS peripherals_wh,
                    COALESCE(SUM(psu_loss_w * duration_sec / 3600.0), 0.0) AS psu_loss_wh,
                    COALESCE(SUM(external_w * duration_sec / 3600.0), 0.0) AS external_wh,
                    COALESCE(SUM(duration_sec), 0.0) AS duration_sec,
                    COALESCE(SUM(monitor_on_sec), 0.0) AS monitor_on_sec,
                    COALESCE(SUM(monitor_idle_waste_sec), 0.0) AS monitor_idle_waste_sec,
                    COALESCE(SUM(monitor_idle_waste_wh), 0.0) AS monitor_idle_waste_wh
                FROM minute_samples
                WHERE date >= ? AND date <= ?
                """,
                (start_date, end_date),
            ).fetchone()

            dh_rows = self._conn.execute(
                """
                SELECT
                    date,
                    hour,
                    COALESCE(SUM(pc_ac_w * duration_sec / 3600.0), 0.0) AS pc_wh,
                    COALESCE(SUM(monitors_w * duration_sec / 3600.0), 0.0) AS monitors_wh,
                    COALESCE(SUM(external_w * duration_sec / 3600.0), 0.0) AS external_wh,
                    COALESCE(SUM(total_ac_w * duration_sec / 3600.0), 0.0) AS total_wh,
                    COALESCE(SUM(monitor_idle_waste_wh), 0.0) AS idle_waste_wh,
                    COALESCE(SUM(duration_sec), 0.0) AS duration_sec,
                    COALESCE(SUM(monitor_on_sec), 0.0) AS monitor_on_sec
                FROM minute_samples
                WHERE date >= ? AND date <= ?
                GROUP BY date, hour
                ORDER BY date ASC, hour ASC
                """,
                (start_date, end_date),
            ).fetchall()

            total_cost_ntd = 0.0
            monitors_cost_ntd = 0.0
            waste_cost_ntd = 0.0
            by_hour: dict[int, dict[str, float]] = {}
            by_date: dict[str, dict[str, float]] = {}

            for r in dh_rows:
                r_date = str(r["date"])
                r_hour = int(r["hour"])
                row_rate, _ = get_rate_for_date_hour(r_date, r_hour, cfg) if cfg is not None else (rate_ntd, "")
                t_wh = float(r["total_wh"])
                m_wh = float(r["monitors_wh"])
                p_wh = float(r["pc_wh"])
                e_wh = float(r["external_wh"])
                w_wh = float(r["idle_waste_wh"])
                d_sec = float(r["duration_sec"])
                mon_sec = float(r["monitor_on_sec"])
                c_tot = (t_wh / 1000.0) * row_rate
                c_mon = (m_wh / 1000.0) * row_rate
                c_wst = (w_wh / 1000.0) * row_rate
                total_cost_ntd += c_tot
                monitors_cost_ntd += c_mon
                waste_cost_ntd += c_wst

                if r_date == end_date:
                    hb = by_hour.setdefault(r_hour, {"pc_wh": 0.0, "monitors_wh": 0.0, "external_wh": 0.0, "total_wh": 0.0, "idle_waste_wh": 0.0, "cost_ntd": 0.0})
                    hb["pc_wh"] += p_wh
                    hb["monitors_wh"] += m_wh
                    hb["external_wh"] += e_wh
                    hb["total_wh"] += t_wh
                    hb["idle_waste_wh"] += w_wh
                    hb["cost_ntd"] += c_tot

                db = by_date.setdefault(
                    r_date,
                    {"pc_wh": 0.0, "monitors_wh": 0.0, "external_wh": 0.0, "total_wh": 0.0, "idle_waste_wh": 0.0, "cost_ntd": 0.0, "duration_sec": 0.0, "monitor_on_sec": 0.0},
                )
                db["pc_wh"] += p_wh
                db["monitors_wh"] += m_wh
                db["external_wh"] += e_wh
                db["total_wh"] += t_wh
                db["idle_waste_wh"] += w_wh
                db["cost_ntd"] += c_tot
                db["duration_sec"] += d_sec
                db["monitor_on_sec"] += mon_sec

            if range_key == "today":
                series = []
                for h in range(24):
                    hb = by_hour.get(h)
                    series.append(
                        {
                            "label": f"{h:02d}:00",
                            "pc_wh": round(hb["pc_wh"] if hb else 0.0, 2),
                            "monitors_wh": round(hb["monitors_wh"] if hb else 0.0, 2),
                            "external_wh": round(hb["external_wh"] if hb else 0.0, 2),
                            "total_wh": round(hb["total_wh"] if hb else 0.0, 2),
                            "idle_waste_wh": round(hb["idle_waste_wh"] if hb else 0.0, 2),
                            "cost_ntd": round(hb["cost_ntd"] if hb else 0.0, 3),
                        }
                    )
            else:
                series = [
                    {
                        "label": d_str[5:],
                        "date": d_str,
                        "pc_wh": round(db["pc_wh"], 2),
                        "monitors_wh": round(db["monitors_wh"], 2),
                        "external_wh": round(db["external_wh"], 2),
                        "total_wh": round(db["total_wh"], 2),
                        "idle_waste_wh": round(db["idle_waste_wh"], 2),
                        "cost_ntd": round(db["cost_ntd"], 3),
                    }
                    for d_str, db in sorted(by_date.items())
                ]

            dev_rows = self._conn.execute(
                """
                SELECT
                    device_id,
                    MAX(device_name) AS device_name,
                    MAX(category) AS category,
                    COALESCE(SUM(energy_wh), 0.0) AS energy_wh,
                    COALESCE(SUM(active_sec), 0.0) AS active_sec,
                    COALESCE(SUM(idle_waste_wh), 0.0) AS idle_waste_wh,
                    COALESCE(SUM(idle_waste_sec), 0.0) AS idle_waste_sec,
                    COALESCE(SUM(cost_ntd), 0.0) AS cost_ntd
                FROM device_daily
                WHERE date >= ? AND date <= ?
                GROUP BY device_id
                ORDER BY energy_wh DESC
                """,
                (start_date, end_date),
            ).fetchall()

        total_wh = float(row["total_wh"])
        monitors_wh = float(row["monitors_wh"])
        total_kwh = total_wh / 1000.0
        waste_wh = float(row["monitor_idle_waste_wh"])
        use_stored_dev_cost = (cfg is None) or (str(cfg.get("tariff_mode") or "tou_2tier").lower() == "tou_2tier")
        summary = {
            "range": range_key,
            "start_date": start_date,
            "end_date": end_date,
            "total_wh": round(total_wh, 2),
            "total_kwh": round(total_kwh, 4),
            "total_cost_ntd": round(total_cost_ntd, 2),
            "pc_ac_wh": round(float(row["pc_ac_wh"]), 2),
            "monitors_wh": round(monitors_wh, 2),
            "monitors_kwh": round(monitors_wh / 1000.0, 4),
            "monitors_cost_ntd": round(monitors_cost_ntd, 2),
            "monitor_pct": round((monitors_wh / total_wh * 100.0) if total_wh > 0 else 0.0, 1),
            "cpu_wh": round(float(row["cpu_wh"]), 2),
            "gpu_wh": round(float(row["gpu_wh"]), 2),
            "ram_wh": round(float(row["ram_wh"]), 2),
            "storage_wh": round(float(row["storage_wh"]), 2),
            "mb_fans_wh": round(float(row["mb_fans_wh"]), 2),
            "peripherals_wh": round(float(row["peripherals_wh"]), 2),
            "psu_loss_wh": round(float(row["psu_loss_wh"]), 2),
            "external_wh": round(float(row["external_wh"]), 2),
            "duration_sec": round(float(row["duration_sec"]), 1),
            "monitor_on_sec": round(float(row["monitor_on_sec"]), 1),
            "monitor_idle_waste_sec": round(float(row["monitor_idle_waste_sec"]), 1),
            "monitor_idle_waste_wh": round(waste_wh, 2),
            "monitor_idle_waste_kwh": round(waste_wh / 1000.0, 4),
            "monitor_idle_waste_cost_ntd": round(waste_cost_ntd, 2),
            "series": series,
            "devices": [
                {
                    "device_id": str(d["device_id"]),
                    "device_name": str(d["device_name"]),
                    "category": str(d["category"]),
                    "energy_wh": round(float(d["energy_wh"]), 2),
                    "energy_kwh": round(float(d["energy_wh"]) / 1000.0, 4),
                    "cost_ntd": round(
                        float(d["cost_ntd"])
                        if (use_stored_dev_cost and float(d["cost_ntd"]) > 0.0)
                        else (float(d["energy_wh"]) / 1000.0) * rate_ntd,
                        2,
                    ),
                    "active_sec": round(float(d["active_sec"]), 1),
                    "idle_waste_wh": round(float(d["idle_waste_wh"]), 2),
                    "idle_waste_sec": round(float(d["idle_waste_sec"]), 1),
                }
                for d in dev_rows
            ],
        }
        return summary, by_date

    def get_summary(
        self,
        range_key: str = "today",
        rate_ntd: float = 3.13,
        ref_date: datetime.date | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start_date, end_date = self._date_bounds(range_key, ref_date=ref_date)
        summary, _ = self._query_range_summary(start_date, end_date, range_key, rate_ntd=rate_ntd, cfg=cfg)
        return summary

    def get_month_summary(
        self,
        year: int,
        month: int,
        rate_ntd: float = 1.99,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start_date, end_date, num_days = self._month_bounds(year, month)
        summary, by_date = self._query_range_summary(start_date, end_date, "month", rate_ntd=rate_ntd, cfg=cfg)

        first_wd_sun0 = (datetime.date(year, month, 1).weekday() + 1) % 7
        calendar_days: list[dict[str, Any]] = []
        daily_series: list[dict[str, Any]] = []
        active_days = 0

        for day in range(1, num_days + 1):
            d_obj = datetime.date(year, month, day)
            d_str = d_obj.isoformat()
            db = by_date.get(d_str)
            has_data = db is not None and db["duration_sec"] > 0
            if has_data:
                active_days += 1
            tot_wh = float(db["total_wh"]) if db else 0.0
            pc_wh = float(db["pc_wh"]) if db else 0.0
            mon_wh = float(db["monitors_wh"]) if db else 0.0
            cost_ntd = float(db["cost_ntd"]) if db else 0.0
            dur_sec = float(db["duration_sec"]) if db else 0.0
            mon_sec = float(db["monitor_on_sec"]) if db else 0.0

            calendar_days.append(
                {
                    "day": day,
                    "date": d_str,
                    "weekday": d_obj.weekday(),
                    "total_wh": round(tot_wh, 2),
                    "total_kwh": round(tot_wh / 1000.0, 4),
                    "pc_wh": round(pc_wh, 2),
                    "monitors_wh": round(mon_wh, 2),
                    "cost_ntd": round(cost_ntd, 2),
                    "duration_sec": round(dur_sec, 1),
                    "monitor_on_sec": round(mon_sec, 1),
                    "has_data": has_data,
                }
            )
            daily_series.append(
                {
                    "label": f"{day}",
                    "date": d_str,
                    "pc_wh": round(pc_wh, 2),
                    "monitors_wh": round(mon_wh, 2),
                    "external_wh": round(float(db["external_wh"]) if db else 0.0, 2),
                    "total_wh": round(tot_wh, 2),
                    "cost_ntd": round(cost_ntd, 3),
                }
            )

        summary.update(
            {
                "year": year,
                "month": month,
                "month_key": f"{year:04d}-{month:02d}",
                "month_label": f"{year}年{month}月",
                "days_in_month": num_days,
                "first_weekday_sun0": first_wd_sun0,
                "active_days": active_days,
                "available_months": self.get_available_months(),
                "calendar_days": calendar_days,
                "series": daily_series,
            }
        )
        return summary

    def export_csv(
        self,
        range_key: str = "today",
        rate_ntd: float = 3.13,
        ref_date: datetime.date | None = None,
        cfg: dict[str, Any] | None = None,
        month_key: str | None = None,
    ) -> str:
        if range_key == "month":
            if month_key and "-" in month_key:
                y_s, m_s = month_key.split("-", 1)
                year, month = int(y_s), int(m_s)
            else:
                td = ref_date or datetime.date.today()
                year, month = td.year, td.month
            start_date, end_date, _ = self._month_bounds(year, month)
        else:
            start_date, end_date = self._date_bounds(range_key, ref_date=ref_date)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM minute_samples
                WHERE date >= ? AND date <= ?
                ORDER BY ts ASC
                """,
                (start_date, end_date),
            ).fetchall()

        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM for Microsoft Excel
        writer = csv.writer(buf)
        writer.writerow(
            [
                "時間(本地)",
                "日期",
                "小時",
                "計費時段",
                "當下費率(元/度)",
                "取樣秒數(s)",
                "總耗電_牆壁AC(W)",
                "主機總計_AC(W)",
                "螢幕總計_AC(W)",
                "CPU(W)",
                "GPU(W)",
                "記憶體RAM(W)",
                "硬碟儲存(W)",
                "主機板與風扇(W)",
                "USB週邊(W)",
                "電供轉換損耗(W)",
                "額外外接設備(W)",
                "顯示器開啟秒數(s)",
                "本分鐘總電量(Wh)",
                "預估電費(NT$)",
            ]
        )
        for r in rows:
            ts_str = datetime.datetime.fromtimestamp(int(r["ts"])).strftime("%Y-%m-%d %H:%M:%S")
            wh = float(r["total_ac_w"]) * float(r["duration_sec"]) / 3600.0
            row_rate, period_tag = get_rate_for_date_hour(str(r["date"]), int(r["hour"]), cfg) if cfg is not None else (rate_ntd, "固定費率")
            cost = (wh / 1000.0) * row_rate
            writer.writerow(
                [
                    ts_str,
                    r["date"],
                    r["hour"],
                    period_tag,
                    row_rate,
                    round(float(r["duration_sec"]), 1),
                    round(float(r["total_ac_w"]), 2),
                    round(float(r["pc_ac_w"]), 2),
                    round(float(r["monitors_w"]), 2),
                    round(float(r["cpu_w"]), 2),
                    round(float(r["gpu_w"]), 2),
                    round(float(r["ram_w"]), 2),
                    round(float(r["storage_w"]), 2),
                    round(float(r["mb_fans_w"]), 2),
                    round(float(r["peripherals_w"]), 2),
                    round(float(r["psu_loss_w"]), 2),
                    round(float(r["external_w"]), 2),
                    round(float(r["monitor_on_sec"]), 1),
                    round(wh, 3),
                    round(cost, 4),
                ]
            )
        return buf.getvalue()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
