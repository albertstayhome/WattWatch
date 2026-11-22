from __future__ import annotations

import math
import re
from typing import Any

from core.config import PSU_TIERS

# ---------------------------------------------------------------------------
# 1. CPU Power Profiles (idle_w, tdp_w, max_w)
# ---------------------------------------------------------------------------
CPU_EXACT_MAP: dict[str, tuple[float, float, float]] = {
    # Intel Xeon E5 / HEDT (LGA2011 / 2011-3 / 2066)
    "E5-2666 V3": (18.0, 135.0, 135.0),
    "E5-2678 V3": (18.0, 120.0, 120.0),
    "E5-2680 V3": (18.0, 120.0, 120.0),
    "E5-2680 V4": (16.0, 120.0, 120.0),
    "E5-2696 V3": (22.0, 145.0, 145.0),
    "E5-2697 V3": (20.0, 145.0, 145.0),
    "E5-2690 V3": (18.0, 135.0, 135.0),
    "E5-2650 V3": (16.0, 105.0, 105.0),
    "E5-2640 V3": (15.0, 90.0, 90.0),
    "E5-2620 V3": (14.0, 85.0, 85.0),
    "E5-1650 V3": (17.0, 140.0, 140.0),
    "E5-2698 V4": (22.0, 135.0, 135.0),
    "E5-2699 V4": (24.0, 145.0, 145.0),
    "E3-1231 V3": (11.0, 80.0, 80.0),
    "E3-1230 V3": (11.0, 80.0, 80.0),
    # Intel Core 14th / 13th / 12th Gen Desktop
    "I9-14900K": (16.0, 125.0, 253.0),
    "I9-14900KF": (16.0, 125.0, 253.0),
    "I9-14900": (14.0, 65.0, 219.0),
    "I7-14700K": (14.0, 125.0, 253.0),
    "I7-14700KF": (14.0, 125.0, 253.0),
    "I7-14700": (12.0, 65.0, 219.0),
    "I5-14600K": (12.0, 125.0, 181.0),
    "I5-14600KF": (12.0, 125.0, 181.0),
    "I5-14400F": (9.0, 65.0, 148.0),
    "I5-14400": (9.0, 65.0, 148.0),
    "I3-14100": (7.0, 60.0, 110.0),
    "I9-13900K": (16.0, 125.0, 253.0),
    "I7-13700K": (14.0, 125.0, 253.0),
    "I5-13600K": (12.0, 125.0, 181.0),
    "I5-13400F": (9.0, 65.0, 148.0),
    "I5-13400": (9.0, 65.0, 148.0),
    "I9-12900K": (15.0, 125.0, 241.0),
    "I7-12700K": (13.0, 125.0, 190.0),
    "I7-12700": (11.0, 65.0, 180.0),
    "I5-12600K": (11.0, 125.0, 150.0),
    "I5-12400F": (8.0, 65.0, 117.0),
    "I5-12400": (8.0, 65.0, 117.0),
    "I3-12100F": (6.0, 58.0, 89.0),
    "I3-12100": (6.0, 60.0, 89.0),
    # Intel Core Ultra 200 (Arrow Lake)
    "ULTRA 9 285K": (12.0, 125.0, 250.0),
    "ULTRA 7 265K": (10.0, 125.0, 250.0),
    "ULTRA 5 245K": (9.0, 125.0, 159.0),
    # Intel Core 10th / 11th / 8th / 9th / 6th / 4th Gen
    "I9-11900K": (15.0, 125.0, 250.0),
    "I7-11700K": (13.0, 125.0, 225.0),
    "I5-11400": (9.0, 65.0, 154.0),
    "I9-10900K": (14.0, 125.0, 250.0),
    "I7-10700K": (12.0, 125.0, 229.0),
    "I5-10400": (8.0, 65.0, 134.0),
    "I5-10400F": (8.0, 65.0, 134.0),
    "I7-9700K": (11.0, 95.0, 170.0),
    "I5-9400F": (8.0, 65.0, 90.0),
    "I7-8700K": (11.0, 95.0, 155.0),
    "I7-7700K": (10.0, 91.0, 115.0),
    "I7-6700K": (10.0, 91.0, 105.0),
    "I7-4790K": (10.0, 88.0, 105.0),
    # Laptop Intel
    "I9-14900HX": (8.0, 55.0, 157.0),
    "I7-14700HX": (7.0, 55.0, 157.0),
    "I7-13700H": (6.0, 45.0, 115.0),
    "I5-13500H": (5.0, 45.0, 95.0),
    "I7-1360P": (4.0, 28.0, 64.0),
    "I5-1340P": (4.0, 28.0, 64.0),
    "I7-1355U": (3.0, 15.0, 55.0),
    "I5-1335U": (3.0, 15.0, 55.0),
    "I7-12700H": (6.0, 45.0, 115.0),
    "I5-12500H": (5.0, 45.0, 95.0),
    # AMD Ryzen 9000 / 7000 / 5000 / 3000 Desktop
    "RYZEN 9 9950X": (22.0, 170.0, 200.0),
    "RYZEN 9 9900X": (20.0, 120.0, 162.0),
    "RYZEN 7 9800X3D": (18.0, 120.0, 162.0),
    "RYZEN 7 9700X": (15.0, 65.0, 88.0),
    "RYZEN 5 9600X": (14.0, 65.0, 88.0),
    "RYZEN 9 7950X3D": (22.0, 120.0, 162.0),
    "RYZEN 9 7950X": (24.0, 170.0, 230.0),
    "RYZEN 9 7900X": (20.0, 170.0, 230.0),
    "RYZEN 7 7800X3D": (18.0, 120.0, 120.0),
    "RYZEN 7 7700X": (18.0, 105.0, 142.0),
    "RYZEN 7 7700": (15.0, 65.0, 88.0),
    "RYZEN 5 7600X": (16.0, 105.0, 142.0),
    "RYZEN 5 7600": (14.0, 65.0, 88.0),
    "RYZEN 5 7500F": (14.0, 65.0, 88.0),
    "RYZEN 9 5950X": (22.0, 105.0, 142.0),
    "RYZEN 9 5900X": (20.0, 105.0, 142.0),
    "RYZEN 7 5800X3D": (18.0, 105.0, 142.0),
    "RYZEN 7 5800X": (18.0, 105.0, 142.0),
    "RYZEN 7 5700X3D": (16.0, 105.0, 130.0),
    "RYZEN 7 5700X": (14.0, 65.0, 88.0),
    "RYZEN 5 5600X": (13.0, 65.0, 88.0),
    "RYZEN 5 5600": (12.0, 65.0, 76.0),
    "RYZEN 5 5500": (11.0, 65.0, 76.0),
    "RYZEN 7 3700X": (15.0, 65.0, 88.0),
    "RYZEN 5 3600": (13.0, 65.0, 88.0),
    "RYZEN 5 2600": (13.0, 65.0, 88.0),
    # AMD Mobile
    "RYZEN 9 7945HX": (12.0, 55.0, 140.0),
    "RYZEN 7 7840HS": (7.0, 35.0, 65.0),
    "RYZEN 7 8845HS": (7.0, 45.0, 65.0),
    "RYZEN 5 7535HS": (6.0, 35.0, 54.0),
    "RYZEN 7 5800H": (6.0, 45.0, 65.0),
}


def lookup_cpu_profile(name: str, cores: int = 4, is_laptop: bool = False) -> dict[str, Any]:
    up = (name or "").upper()
    # Clean common strings
    cleaned = re.sub(r"\(R\)|\(TM\)|CPU|@.*|PROCESSOR|\d+-CORE", " ", up)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    for key, (idle_w, tdp_w, max_w) in CPU_EXACT_MAP.items():
        if key in cleaned:
            return {"model_key": key, "idle_w": idle_w, "tdp_w": tdp_w, "max_w": max_w, "matched": True}

    # Heuristic fallback by series / suffix / core count
    if "XEON" in up:
        if cores >= 14:
            return {"model_key": "Xeon HEDT (>=14C)", "idle_w": 22.0, "tdp_w": 145.0, "max_w": 145.0, "matched": False}
        if cores >= 8:
            return {"model_key": "Xeon HEDT (8-12C)", "idle_w": 17.0, "tdp_w": 120.0, "max_w": 125.0, "matched": False}
        return {"model_key": "Xeon Workstation", "idle_w": 12.0, "tdp_w": 85.0, "max_w": 90.0, "matched": False}

    if "THREADRIPPER" in up:
        return {"model_key": "Threadripper", "idle_w": 35.0, "tdp_w": 280.0, "max_w": 350.0, "matched": False}

    if is_laptop or re.search(r"\b\d{4,5}[HU]\b|\b\d{4,5}HX\b|\b\d{4,5}HS\b|\b\d{4}P\b", up):
        if "HX" in up:
            return {"model_key": "Laptop HX", "idle_w": 7.0, "tdp_w": 55.0, "max_w": 130.0, "matched": False}
        if "HS" in up or re.search(r"\d{4,5}H\b", up):
            return {"model_key": "Laptop H/HS", "idle_w": 5.5, "tdp_w": 45.0, "max_w": 85.0, "matched": False}
        if "U" in up:
            return {"model_key": "Laptop U", "idle_w": 3.0, "tdp_w": 15.0, "max_w": 45.0, "matched": False}
        return {"model_key": "Laptop Mobile", "idle_w": 4.5, "tdp_w": 28.0, "max_w": 60.0, "matched": False}

    if "I9-" in up or "RYZEN 9" in up or "ULTRA 9" in up:
        return {"model_key": "Desktop Flagship", "idle_w": 16.0, "tdp_w": 125.0, "max_w": 210.0, "matched": False}
    if "I7-" in up or "RYZEN 7" in up or "ULTRA 7" in up:
        return {"model_key": "Desktop High-End", "idle_w": 13.0, "tdp_w": 95.0, "max_w": 160.0, "matched": False}
    if "I5-" in up or "RYZEN 5" in up or "ULTRA 5" in up:
        return {"model_key": "Desktop Mainstream", "idle_w": 10.0, "tdp_w": 65.0, "max_w": 115.0, "matched": False}
    if "I3-" in up or "RYZEN 3" in up or "PENTIUM" in up or "CELERON" in up or "ATHLON" in up:
        return {"model_key": "Desktop Entry", "idle_w": 6.0, "tdp_w": 55.0, "max_w": 75.0, "matched": False}

    base_max = max(45.0, min(220.0, 25.0 + cores * 11.0))
    idle_w = round(max(6.0, base_max * 0.13), 1)
    return {"model_key": f"Generic {cores}C CPU", "idle_w": idle_w, "tdp_w": round(base_max * 0.85, 1), "max_w": round(base_max, 1), "matched": False}


def estimate_cpu_watts(profile: dict[str, Any], util_pct: float, freq_ratio: float = 1.0) -> float:
    u = max(0.0, min(1.0, float(util_pct) / 100.0))
    fr = max(0.35, min(1.25, float(freq_ratio)))
    idle_w = float(profile.get("idle_w", 12.0))
    max_w = float(profile.get("max_w", 95.0))
    # Dynamic scaling: mild sub-linear turbo bump at partial thread load + frequency scaling
    load_factor = 0.25 * u + 0.75 * (u ** 0.8)
    freq_scale = 0.7 + 0.3 * fr
    watts = idle_w + (max_w - idle_w) * load_factor * freq_scale
    return round(max(idle_w * 0.8, min(max_w * 1.08, watts)), 2)


# ---------------------------------------------------------------------------
# 2. GPU Power Profiles (idle_w, tdp_w)
# ---------------------------------------------------------------------------
VIRTUAL_GPU_KEYWORDS = (
    "MICROSOFT BASIC",
    "TODESK",
    "PARSEC",
    "TEAMVIEWER",
    "ANYDESK",
    "VIRTUAL",
    "CITRIX",
    "IDDSAMPLE",
    "SPACEDESK",
    "MIRAGE",
    "LUMINON",
    "ORAY",
    "SPLASHTOP",
)

GPU_EXACT_MAP: dict[str, tuple[float, float]] = {
    # NVIDIA RTX 50 / 40 / 30 / 20 / GTX
    "RTX 5090": (25.0, 575.0),
    "RTX 5080": (20.0, 360.0),
    "RTX 5070 TI": (16.0, 300.0),
    "RTX 5070": (14.0, 250.0),
    "RTX 4090": (22.0, 450.0),
    "RTX 4080 SUPER": (18.0, 320.0),
    "RTX 4080": (18.0, 320.0),
    "RTX 4070 TI SUPER": (15.0, 285.0),
    "RTX 4070 TI": (15.0, 285.0),
    "RTX 4070 SUPER": (14.0, 220.0),
    "RTX 4070": (12.0, 200.0),
    "RTX 4060 TI": (11.0, 165.0),
    "RTX 4060": (9.0, 115.0),
    "RTX 3090 TI": (25.0, 450.0),
    "RTX 3090": (22.0, 350.0),
    "RTX 3080 TI": (20.0, 350.0),
    "RTX 3080": (19.0, 320.0),
    "RTX 3070 TI": (16.0, 290.0),
    "RTX 3070": (14.0, 220.0),
    "RTX 3060 TI": (13.0, 200.0),
    "RTX 3060": (12.0, 170.0),
    "RTX 3050": (9.0, 130.0),
    "RTX 2080 TI": (18.0, 250.0),
    "RTX 2080 SUPER": (16.0, 250.0),
    "RTX 2080": (16.0, 215.0),
    "RTX 2070 SUPER": (14.0, 215.0),
    "RTX 2070": (13.0, 175.0),
    "RTX 2060 SUPER": (12.0, 175.0),
    "RTX 2060": (11.0, 160.0),
    "GTX 1660 TI": (10.0, 120.0),
    "GTX 1660 SUPER": (10.0, 125.0),
    "GTX 1660": (10.0, 120.0),
    "GTX 1650 SUPER": (8.0, 100.0),
    "GTX 1650": (7.0, 75.0),
    "GTX 1080 TI": (16.0, 250.0),
    "GTX 1080": (13.0, 180.0),
    "GTX 1070 TI": (12.0, 180.0),
    "GTX 1070": (11.0, 150.0),
    "GTX 1060": (9.0, 120.0),
    "GTX 1050 TI": (6.0, 75.0),
    "GTX 1050": (6.0, 75.0),
    "GTX 970": (12.0, 145.0),
    "GTX 960": (10.0, 120.0),
    "GTX 750 TI": (6.0, 60.0),
    "GT 1030": (4.0, 30.0),
    "GT 730": (4.0, 25.0),
    "GT 710": (3.0, 19.0),
    # AMD Radeon RX
    "RX 9070 XT": (18.0, 304.0),
    "RX 9070": (15.0, 220.0),
    "RX 7900 XTX": (24.0, 355.0),
    "RX 7900 XT": (20.0, 315.0),
    "RX 7800 XT": (16.0, 263.0),
    "RX 7700 XT": (14.0, 245.0),
    "RX 7600": (10.0, 165.0),
    "RX 6950 XT": (20.0, 335.0),
    "RX 6900 XT": (18.0, 300.0),
    "RX 6800 XT": (16.0, 300.0),
    "RX 6800": (15.0, 250.0),
    "RX 6750 XT": (14.0, 250.0),
    "RX 6700 XT": (13.0, 230.0),
    "RX 6650 XT": (10.0, 180.0),
    "RX 6600 XT": (9.0, 160.0),
    "RX 6600": (8.0, 132.0),
    "RX 6500 XT": (7.0, 107.0),
    "RX 5700 XT": (13.0, 225.0),
    "RX 5700": (12.0, 180.0),
    "RX 580": (13.0, 185.0),
    "RX 570": (11.0, 150.0),
    "RX 560": (7.0, 75.0),
    # Intel Arc
    "ARC B580": (14.0, 190.0),
    "ARC A770": (18.0, 225.0),
    "ARC A750": (16.0, 225.0),
    "ARC A580": (14.0, 185.0),
    "ARC A380": (10.0, 75.0),
}


def is_virtual_gpu(name: str) -> bool:
    up = (name or "").upper()
    if not up.strip():
        return True
    return any(kw in up for kw in VIRTUAL_GPU_KEYWORDS)


def lookup_gpu_profile(name: str, is_laptop: bool = False) -> dict[str, Any]:
    up = (name or "").upper()
    if any(k in up for k in ("UHD GRAPHICS", "HD GRAPHICS", "IRIS XE", "IRIS PLUS", "RADEON(TM) GRAPHICS", "RADEON GRAPHICS", "VEGA 8", "VEGA 7")):
        # Integrated GPU shares CPU package power (avoid double counting if CPU RAPL active, small slice otherwise)
        return {"model_key": "Integrated GPU", "idle_w": 1.0, "tdp_w": 12.0, "is_igpu": True, "matched": True}

    # Check longest keys first so "RTX 4070 TI SUPER" matches before "RTX 4070"
    for key in sorted(GPU_EXACT_MAP.keys(), key=len, reverse=True):
        if key in up:
            idle_w, tdp_w = GPU_EXACT_MAP[key]
            if is_laptop or "LAPTOP" in up or "MAX-Q" in up:
                idle_w = round(idle_w * 0.55, 1)
                tdp_w = round(tdp_w * 0.55, 1)
            return {"model_key": key, "idle_w": idle_w, "tdp_w": tdp_w, "is_igpu": False, "matched": True}

    return {"model_key": "Discrete GPU (Generic)", "idle_w": 10.0, "tdp_w": 140.0, "is_igpu": False, "matched": False}


def estimate_gpu_watts(profile: dict[str, Any], util_pct: float) -> float:
    u = max(0.0, min(1.0, float(util_pct) / 100.0))
    idle_w = float(profile.get("idle_w", 10.0))
    tdp_w = float(profile.get("tdp_w", 140.0))
    return round(idle_w + (tdp_w - idle_w) * (u ** 0.85), 2)


# ---------------------------------------------------------------------------
# 3. Monitor PNP Brand Map, Known Models & Physical Wattage Model
# ---------------------------------------------------------------------------
PNP_BRAND_MAP: dict[str, str] = {
    "ACI": "ASUS", "ACR": "Acer", "ACT": "Targa", "ADI": "ADI", "AMW": "AMW",
    "AOC": "AOC", "API": "Acer", "APP": "Apple", "ART": "ArtMedia", "AST": "AST",
    "AUS": "ASUS", "AUO": "AU Optronics", "BEN": "BenQ", "BNQ": "BenQ", "BOE": "BOE",
    "BTC": "BTC", "CMN": "Innolux (群創)", "CMO": "Chi Mei (奇美)", "COM": "Comrade",
    "CPL": "Compal", "CPQ": "Compaq", "CTX": "CTX", "DEC": "DEC", "DEL": "Dell",
    "DPC": "Delta", "DWE": "Daewoo", "ECS": "ECS", "EIZ": "EIZO", "ELS": "ELSA",
    "ENC": "EIZO", "EPI": "Envision", "FCM": "Funai", "FUS": "Fujitsu", "GBT": "Gigabyte (技嘉)",
    "GIG": "Gigabyte (技嘉)", "GSM": "LG", "GWY": "Gateway", "HAN": "HannStar", "HEI": "Hyundai",
    "HIK": "Hikvision", "HKC": "HKC (惠科)", "HPN": "HP", "HPQ": "HP", "HSD": "HannStar",
    "HTI": "Hitachi", "HWP": "HP", "HYO": "HYC", "IBM": "IBM", "ICL": "Fujitsu",
    "INL": "Innolux", "IVM": "iiyama", "KDS": "KDS", "KTC": "KTC (康冠)", "LEN": "Lenovo",
    "LGD": "LG Display", "LGE": "LG", "LKM": "ADLAS", "LPL": "LG Philips", "LTN": "Lite-On",
    "MAX": "Maxdata", "MED": "Medion", "MEI": "Panasonic", "MEL": "Mitsubishi", "MIR": "miro",
    "MON": "Generic", "MSI": "MSI (微星)", "MTC": "MITAC", "NAN": "NANAO (EIZO)", "NEC": "NEC",
    "NOK": "Nokia", "NVD": "NVIDIA", "OQI": "Optiquest", "PBN": "Packard Bell", "PGS": "Princeton",
    "PHL": "Philips", "PIX": "Pixel", "PLX": "Parallax", "PNR": "Planar", "PRO": "Proview",
    "PTS": "Proview", "QDS": "Quanta", "RAT": "Rentek", "REL": "Relisys", "ROG": "ASUS ROG",
    "SAM": "Samsung", "SAN": "Sanyo", "SCD": "Samsung", "SDC": "Samsung Display", "SEC": "Seiko Epson",
    "SGI": "SGI", "SGT": "SGT (瑞軒/冠捷代工)", "SHP": "Sharp", "SII": "Seiko", "SKT": "Skyworth",
    "SNY": "Sony", "SPT": "Sceptre", "SRC": "Shamrock", "STN": "Samtron", "SUN": "Sun",
    "TAT": "Tatung (大同)", "TCL": "TCL", "TOS": "Toshiba", "TSB": "Toshiba", "UNM": "Unisys",
    "VIZ": "Vizio", "VSC": "ViewSonic (優派)", "WTC": "Wen", "XMI": "Xiaomi (小米)", "ZCM": "Zenith",
}

# Known popular monitor specs: model_substring -> (min_w, max_w, standby_w, default_panel)
KNOWN_MONITOR_DB: dict[str, tuple[float, float, float, str]] = {
    "U2723QE": (19.0, 52.0, 0.5, "IPS"),
    "U2720Q": (20.0, 50.0, 0.5, "IPS"),
    "U2422H": (12.0, 26.0, 0.4, "IPS"),
    "S2721DGF": (22.0, 55.0, 0.5, "IPS"),
    "AW3423DW": (32.0, 95.0, 0.6, "OLED"),
    "27GP850": (22.0, 52.0, 0.5, "IPS"),
    "27GL850": (22.0, 50.0, 0.5, "IPS"),
    "27GR95QE": (25.0, 75.0, 0.5, "OLED"),
    "VG27AQ": (20.0, 48.0, 0.5, "IPS"),
    "PG27AQDM": (26.0, 78.0, 0.5, "OLED"),
    "XV272U": (20.0, 48.0, 0.5, "IPS"),
    "M27Q": (20.0, 50.0, 0.5, "IPS"),
    "M32U": (28.0, 72.0, 0.5, "IPS"),
    "EX2780Q": (21.0, 52.0, 0.5, "IPS"),
    "EX2710Q": (21.0, 50.0, 0.5, "IPS"),
    "G27QC": (19.0, 45.0, 0.5, "VA"),
    "MAG274QRF": (21.0, 50.0, 0.5, "IPS"),
    "ODYSSEY G7": (26.0, 68.0, 0.5, "VA"),
    "ODYSSEY G5": (20.0, 48.0, 0.5, "VA"),
}


def lookup_monitor_brand(pnp_mfg: str) -> str:
    code = (pnp_mfg or "").strip().upper()
    return PNP_BRAND_MAP.get(code, code or "Unknown")


def lookup_known_monitor(model_name: str) -> tuple[float, float, float, str] | None:
    up = (model_name or "").upper()
    for key, spec in KNOWN_MONITOR_DB.items():
        if key in up:
            return spec
    return None


def estimate_monitor_watts(
    width_cm: float,
    height_cm: float,
    diagonal_in: float,
    width_px: int,
    height_px: int,
    refresh_hz: int,
    brightness_pct: float = 70.0,
    panel_type: str = "IPS",
    state: str = "on",
    custom_watts: float | None = None,
    model_spec: tuple[float, float, float, str] | None = None,
) -> float:
    st = (state or "on").lower()
    if st == "disconnected":
        return 0.0
    if st == "off":
        return float(model_spec[2]) if model_spec else 0.5

    if custom_watts is not None and float(custom_watts) > 0:
        base = float(custom_watts)
        return round(base * 0.55 if st == "dimmed" else base, 2)

    b = max(0.0, min(100.0, float(brightness_pct)))
    eff_b = b * (0.35 if st == "dimmed" else 1.0)

    if model_spec is not None:
        min_w, max_w, _, _ = model_spec
        watts = min_w + (max_w - min_w) * (eff_b / 100.0)
        return round(watts, 2)

    # Physical panel area in cm^2
    if width_cm > 5 and height_cm > 5:
        area_cm2 = float(width_cm) * float(height_cm)
    elif diagonal_in > 5:
        # Assume 16:9 ratio when cm missing: diag_cm = diag_in * 2.54
        diag_cm = float(diagonal_in) * 2.54
        w_est = diag_cm * (16.0 / math.sqrt(16 * 16 + 9 * 9))
        h_est = diag_cm * (9.0 / math.sqrt(16 * 16 + 9 * 9))
        area_cm2 = w_est * h_est
    else:
        area_cm2 = 1980.0  # 27-inch default

    # Normalized to ~27" 16:9 (~1980 cm^2)
    area_ratio = max(0.2, min(4.5, area_cm2 / 2000.0))

    res_pixels = int(width_px) * int(height_px)
    if res_pixels <= 2_200_000:
        res_penalty = 0.0
    elif res_pixels <= 4_200_000:
        res_penalty = 3.5
    elif res_pixels <= 6_000_000:
        res_penalty = 5.5
    else:
        res_penalty = 9.5

    hz = int(refresh_hz) if refresh_hz else 60
    if hz <= 75:
        hz_penalty = 0.0
    elif hz <= 165:
        hz_penalty = 3.5
    elif hz <= 240:
        hz_penalty = 6.0
    else:
        hz_penalty = 8.5

    electronics_w = 4.5 + res_penalty + hz_penalty

    panel_mult_map = {
        "IPS": 1.0,
        "VA": 0.93,
        "TN": 0.88,
        "OLED": 0.90,
        "MINI-LED": 1.28,
    }
    panel_mult = panel_mult_map.get((panel_type or "IPS").upper(), 1.0)
    backlight_w = (5.0 + 24.0 * (eff_b / 100.0)) * area_ratio * panel_mult
    return round(electronics_w + backlight_w, 2)


# ---------------------------------------------------------------------------
# 4. PSU Load-Dependent Efficiency Curve
# ---------------------------------------------------------------------------
def get_psu_efficiency(dc_watts: float, rated_watts: int = 650, tier: str = "bronze") -> float:
    tier_info = PSU_TIERS.get((tier or "bronze").lower(), PSU_TIERS["bronze"])
    nominal = float(tier_info["nominal"])
    rated = max(150.0, float(rated_watts or 650))
    load_pct = max(0.0, float(dc_watts) / rated)

    if load_pct < 0.05:
        adj = -0.08
    elif load_pct < 0.20:
        # Linear ramp from -0.08 at 5% to 0.0 at 20%
        adj = -0.08 + ((load_pct - 0.05) / 0.15) * 0.08
    elif load_pct <= 0.65:
        # Peak curve around 40-60% load
        adj = 0.01 * math.sin(((load_pct - 0.20) / 0.45) * math.pi)
    elif load_pct <= 0.85:
        adj = 0.0
    else:
        adj = -0.02 * min(1.0, (load_pct - 0.85) / 0.15)

    eff = nominal + adj
    return round(max(0.65, min(0.96, eff)), 4)


# ---------------------------------------------------------------------------
# 5. USB Vendor Map & Peripheral Power Classification
# ---------------------------------------------------------------------------
USB_VENDOR_MAP: dict[str, str] = {
    "1532": "Razer",
    "046D": "Logitech",
    "1B1C": "Corsair",
    "1038": "SteelSeries",
    "320F": "Sonix",
    "0BDA": "Realtek",
    "045E": "Microsoft",
    "0951": "HyperX / Kingston",
    "0B05": "ASUS",
    "1462": "MSI",
    "258A": "Sino Wealth",
    "046A": "Cherry",
    "04D9": "Holtek",
    "1A2C": "USB 鍵盤",
    "0C45": "Microdia",
    "05AC": "Apple",
    "04E8": "Samsung",
    "0781": "SanDisk",
    "1058": "Western Digital",
    "0BC2": "Seagate",
    "13FD": "Initio",
    "174C": "ASMedia",
    "2109": "VIA Labs",
    "05E3": "Genesys Logic",
    "8087": "Intel",
    "04F2": "Chicony",
    "3434": "Keychron",
    "2516": "Cooler Master",
    "04D8": "Microchip",
    "046E": "Behavior Tech",
    "093A": "Pixart",
    "1EA7": "SHARKOON",
    "248A": "Maxxter",
    "0D8C": "C-Media Audio",
    "1235": "Focusrite Audio",
}


def classify_usb_peripheral(friendly_name: str, pnp_class: str = "", vid: str = "", pid: str = "") -> dict[str, Any]:
    up = (friendly_name or "").upper()
    v = (vid or "").upper()
    brand = USB_VENDOR_MAP.get(v, "")

    if any(k in up for k in ("WEBCAM", "CAMERA", "攝影機", "C920", "C922", "BRIO", "STREAMCAM")):
        return {"category": "Webcam", "label": "網路攝影機", "watts": 2.0, "brand": brand}
    if any(k in up for k in ("BLUETOOTH", "藍牙", "BT ", "WIRELESS ADAPTER", "WI-FI", "WIFI")):
        return {"category": "Radio", "label": "藍牙 / 無線接收器", "watts": 0.4, "brand": brand}
    if any(k in up for k in ("DEATHADDER", "VIPER", "BASILISK", "MOUSE", "滑鼠", "G502", "G PRO", "SUPERLIGHT")):
        is_rgb = any(k in up for k in ("RAZER", "DEATHADDER", "VIPER", "CORSAIR", "LOGITECH", "RGB", "GAMING"))
        return {"category": "Mouse", "label": "電競/光學滑鼠", "watts": 1.2 if is_rgb else 0.7, "brand": brand}
    if any(k in up for k in ("KEYBOARD", "鍵盤", "KEYCHRON", "320F", "BLACKWIDOW", "HUNTSMAN", "K70", "APEX")):
        return {"category": "Keyboard", "label": "RGB / 機械鍵盤", "watts": 2.2, "brand": brand}
    if v in ("320F", "046A", "3434"):
        return {"category": "Keyboard", "label": "RGB / USB 鍵盤", "watts": 2.0, "brand": brand}
    if any(k in up for k in ("AUDIO", "DAC", "HEADSET", "HEADPHONE", "SPEAKER", "MIC", "耳機", "麥克風", "音效")):
        return {"category": "Audio", "label": "USB 音效 / 耳麥", "watts": 1.5, "brand": brand}
    if any(k in up for k in ("STORAGE", "DISK", "MASS STORAGE", "FLASH", "SSD", "隨身碟", "外接")):
        return {"category": "USBStorage", "label": "USB 外接儲存", "watts": 3.5, "brand": brand}
    if pnp_class.upper() == "CAMERA":
        return {"category": "Webcam", "label": "攝影機裝置", "watts": 2.0, "brand": brand}

    return {"category": "USB", "label": "USB 週邊裝置", "watts": 0.9, "brand": brand}
