# WattWatch 電量偵測管家

一套專為 Windows 設計的**免外接電錶、自動硬體偵測、即時電腦主機與顯示器耗電統計工具**。內建 **PawnIO / NVML 硬體感測支援**、**24 小時背景自動省電降頻機制**、**台電簡易型二段式時間電價（自動切換夏月／非夏月與尖峰／離峰，不含基本費）**，以及客觀的電費試算與模擬面板。

---

## 一鍵下載安裝（Windows 10 / 11 64-bit）

👉 **[點此直接下載 WattWatch_Setup_v1.0.exe](https://github.com/albertstayhome/WattWatch/releases/latest/download/WattWatch_Setup_v1.0.exe)**

下載後雙擊執行即可自動完成所有設定（內建執行環境與 `PawnIO` 硬體感測驅動，免另外安裝 Python），安裝完成可從桌面捷徑或系統匣直接開啟。

---

## 主要功能

- **自動偵測硬體與顯示器**：自動讀取 CPU、GPU、記憶體、硬碟、主機板、USB 週邊，以及解析 Windows 螢幕 **EDID**（品牌、尺寸、解析度、更新率）與 **DDC/CI**／電源狀態。
- **台電簡易型二段式時間電價（不含基本費）**：
  - **夏月 (6/1～9/30)**：週一至五 `09:00–24:00` 尖峰 `5.16 元/度`、`00:00–09:00` 離峰 `2.06 元/度`；週六、日全日離峰 `2.06 元/度`。
  - **非夏月 (10/1～5/31)**：週一至五 `06:00–11:00` 與 `14:00–24:00` 尖峰 `4.93 元/度`、其餘離峰 `1.99 元/度`；週六、日全日離峰 `1.99 元/度`。
  - 亦可在設定中切換為自訂固定費率。
- **24 小時低耗電背景運作**：開啟視窗時以 1 秒即時更新；關閉視窗縮入右下角系統匣後自動降頻為每 3～5 秒採樣，持續透過 SQLite 累積電量與電費紀錄，並支援匯出 CSV。

---

## 從原始碼開發執行

```powershell
py -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File vendor\lhm\build_bridge.ps1
```

開發模式啟動：雙擊 [`啟動_WattWatch.vbs`](啟動_WattWatch.vbs) 或於管理員終端機執行 `py app.py`。
