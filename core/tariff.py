from __future__ import annotations

import datetime
from typing import Any

# Taipower Residential / Small Commercial Simple 2-Tier Time-of-Use Tariff (Excl. NT$75 basic fee)
TOU_2TIER_RATES: dict[str, dict[str, float]] = {
    "summer": {"peak": 5.16, "offpeak": 2.06},
    "nonsummer": {"peak": 4.93, "offpeak": 1.99},
}


def is_summer(dt: datetime.datetime | datetime.date) -> bool:
    return 6 <= dt.month <= 9


def get_tou_2tier_info(dt: datetime.datetime | None = None) -> dict[str, Any]:
    now = dt or datetime.datetime.now()
    summer = is_summer(now)
    season = "summer" if summer else "nonsummer"
    season_label = "夏月 (6/1~9/30)" if summer else "非夏月 (10/1~5/31)"
    rates = TOU_2TIER_RATES[season]
    peak_rate = rates["peak"]
    offpeak_rate = rates["offpeak"]

    # weekday(): 0=Mon .. 4=Fri, 5=Sat, 6=Sun
    is_weekend = now.weekday() >= 5
    hour = now.hour

    if is_weekend:
        period = "offpeak"
        period_label = "週末全日離峰"
    elif summer:
        # Summer weekday peak: 09:00 ~ 24:00 (hours 9..23); off-peak: 00:00 ~ 09:00 (hours 0..8)
        if 9 <= hour <= 23:
            period = "peak"
            period_label = "尖峰時段 (09:00~24:00)"
        else:
            period = "offpeak"
            period_label = "離峰時段 (00:00~09:00)"
    else:
        # Non-summer weekday peak: 06:00~11:00 (6..10) & 14:00~24:00 (14..23)
        if (6 <= hour <= 10) or (14 <= hour <= 23):
            period = "peak"
            period_label = "尖峰時段 (06:00~11:00, 14:00~24:00)"
        else:
            period = "offpeak"
            period_label = "離峰時段 (00:00~06:00, 11:00~14:00)"

    rate_ntd = peak_rate if period == "peak" else offpeak_rate
    short_season = "夏月" if summer else "非夏月"
    short_period = "週末離峰" if is_weekend else ("尖峰" if period == "peak" else "離峰")

    # 75 peak hours (5 days * 15h) + 93 off-peak hours (5*9h + 48h weekend) = 168h
    summer_avg = (75.0 * TOU_2TIER_RATES["summer"]["peak"] + 93.0 * TOU_2TIER_RATES["summer"]["offpeak"]) / 168.0
    nonsummer_avg = (75.0 * TOU_2TIER_RATES["nonsummer"]["peak"] + 93.0 * TOU_2TIER_RATES["nonsummer"]["offpeak"]) / 168.0
    weekly_avg_rate = summer_avg if summer else nonsummer_avg
    # 122 summer days (Jun-Sep) + 243 non-summer days (Oct-May)
    annual_avg_rate = (122.0 * summer_avg + 243.0 * nonsummer_avg) / 365.0

    return {
        "mode": "tou_2tier",
        "mode_label": "台電簡易型二段式時間電價 (不含基本費)",
        "season": season,
        "season_label": season_label,
        "period": period,
        "period_label": period_label,
        "short_label": f"{short_season}・{short_period}",
        "is_weekend": is_weekend,
        "rate_ntd": rate_ntd,
        "peak_rate": peak_rate,
        "offpeak_rate": offpeak_rate,
        "weekly_avg_rate": round(weekly_avg_rate, 2),
        "annual_avg_rate": round(annual_avg_rate, 2),
    }


def get_effective_rate_info(cfg: dict[str, Any], dt: datetime.datetime | None = None) -> dict[str, Any]:
    mode = str(cfg.get("tariff_mode") or "tou_2tier").lower()
    if mode == "fixed":
        rate = float(cfg.get("electricity_rate_ntd", 3.13))
        return {
            "mode": "fixed",
            "mode_label": "自訂固定電價",
            "season": "custom",
            "season_label": "固定費率",
            "period": "fixed",
            "period_label": "單一費率",
            "short_label": "固定費率",
            "is_weekend": False,
            "rate_ntd": rate,
            "peak_rate": rate,
            "offpeak_rate": rate,
            "weekly_avg_rate": rate,
            "annual_avg_rate": rate,
        }
    return get_tou_2tier_info(dt)


def get_rate_for_date_hour(date_str: str, hour: int, cfg: dict[str, Any] | None = None) -> tuple[float, str]:
    if cfg and str(cfg.get("tariff_mode") or "tou_2tier").lower() == "fixed":
        return float(cfg.get("electricity_rate_ntd", 3.13)), "固定費率"
    try:
        d = datetime.date.fromisoformat(date_str)
        dt = datetime.datetime(d.year, d.month, d.day, max(0, min(23, int(hour))), 0, 0)
        info = get_tou_2tier_info(dt)
        return float(info["rate_ntd"]), str(info["short_label"])
    except Exception:
        return 1.99, "非夏月・離峰"
