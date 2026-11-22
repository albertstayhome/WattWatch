(() => {
  "use strict";

  const nowDate = new Date();
  const curMonthKey = `${nowDate.getFullYear()}-${String(nowDate.getMonth() + 1).padStart(2, "0")}`;

  const state = {
    live: null,
    hardware: null,
    configData: null,
    historyRange: "today",
    selectedMonth: curMonthKey,
    selectedCalDate: null,
    scenario: "monitor_only",
    simPeriod: "avg",
    wasteHours: 8,
    activeSeries: new Set(["total_ac_w", "pc_ac_w", "monitors_w", "gpu_w", "cpu_w"]),
    bannerDismissed: false,
    extDraft: [],
    hoverIndex: -1,
  };

  const SERIES_META = {
    total_ac_w: { label: "總耗電 AC", color: "#38bdf8" },
    pc_ac_w: { label: "主機 AC", color: "#a855f7" },
    monitors_w: { label: "螢幕", color: "#22d3ee" },
    gpu_w: { label: "GPU", color: "#f472b6" },
    cpu_w: { label: "CPU", color: "#22c55e" },
  };

  const $ = (id) => document.getElementById(id);

  function showToast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => t.classList.add("hidden"), 3200);
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDur(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h} 小時 ${m} 分`;
    if (m > 0) return `${m} 分鐘`;
    return `${s} 秒`;
  }

  // Tabs
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "history") loadHistory();
      if (btn.dataset.tab === "overview") drawLiveChart();
    });
  });

  // Series toggles
  document.querySelectorAll("#live-chart-toggles .toggle-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const k = chip.dataset.series;
      if (state.activeSeries.has(k)) {
        if (state.activeSeries.size > 1) {
          state.activeSeries.delete(k);
          chip.classList.remove("active");
        }
      } else {
        state.activeSeries.add(k);
        chip.classList.add("active");
      }
      drawLiveChart();
    });
  });

  // Live Chart Canvas + Hover
  const liveCanvas = $("live-chart");
  liveCanvas.addEventListener("mousemove", (ev) => {
    const pts = state.live?.chart_history || [];
    if (!pts.length) return;
    const rect = liveCanvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const padL = 48, padR = 16;
    const plotW = Math.max(10, rect.width - padL - padR);
    const idx = Math.round(((x - padL) / plotW) * (pts.length - 1));
    if (idx >= 0 && idx < pts.length) {
      state.hoverIndex = idx;
      const pt = pts[idx];
      const tip = $("live-chart-tooltip");
      tip.innerHTML =
        `<strong>${escapeHtml(pt.time)}</strong><br/>` +
        `總耗電: <b>${pt.total_ac_w.toFixed(1)} W</b><br/>` +
        `主機: ${pt.pc_ac_w.toFixed(1)} W ｜ 螢幕: ${pt.monitors_w.toFixed(1)} W<br/>` +
        `GPU: ${pt.gpu_w.toFixed(1)} W ｜ CPU: ${pt.cpu_w.toFixed(1)} W`;
      tip.style.left = Math.min(rect.width - 210, Math.max(8, x + 12)) + "px";
      tip.style.top = "12px";
      tip.classList.remove("hidden");
      drawLiveChart();
    }
  });
  liveCanvas.addEventListener("mouseleave", () => {
    state.hoverIndex = -1;
    $("live-chart-tooltip").classList.add("hidden");
    drawLiveChart();
  });

  function drawLiveChart() {
    const canvas = $("live-chart");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 700;
    const h = 250;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const pts = state.live?.chart_history || [];
    if (pts.length < 2) {
      ctx.fillStyle = "#94a3b8";
      ctx.font = "13px sans-serif";
      ctx.fillText("正在收集即時取樣點...", 24, 40);
      return;
    }

    const padL = 48, padR = 16, padT = 18, padB = 28;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    let maxVal = 50;
    for (const pt of pts) {
      for (const k of state.activeSeries) {
        if ((pt[k] || 0) > maxVal) maxVal = pt[k];
      }
    }
    maxVal = Math.ceil((maxVal * 1.15) / 25) * 25;

    ctx.strokeStyle = "rgba(148,163,184,0.16)";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px sans-serif";
    for (let i = 0; i <= 4; i++) {
      const y = padT + (plotH * i) / 4;
      const val = Math.round(maxVal * (1 - i / 4));
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText(`${val}W`, 6, y + 4);
    }

    const xFor = (i) => padL + (i / (pts.length - 1)) * plotW;
    const yFor = (v) => padT + plotH - (Math.max(0, v) / maxVal) * plotH;

    for (const k of state.activeSeries) {
      const meta = SERIES_META[k];
      ctx.strokeStyle = meta.color;
      ctx.lineWidth = k === "total_ac_w" ? 2.5 : 1.7;
      ctx.beginPath();
      pts.forEach((pt, idx) => {
        const x = xFor(idx);
        const y = yFor(pt[k] || 0);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    // X labels
    ctx.fillStyle = "#94a3b8";
    const step = Math.max(1, Math.floor(pts.length / 5));
    for (let i = 0; i < pts.length; i += step) {
      ctx.fillText(pts[i].time, Math.min(w - 60, xFor(i) - 18), h - 8);
    }

    if (state.hoverIndex >= 0 && state.hoverIndex < pts.length) {
      const hx = xFor(state.hoverIndex);
      ctx.strokeStyle = "rgba(226,232,240,0.4)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hx, padT);
      ctx.lineTo(hx, padT + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function renderLive(snap) {
    state.live = snap;
    if (!snap) return;

    const tariff = snap.tariff || {};
    const rate = snap.electricity_rate_ntd || 1.99;
    const rateTag = tariff.short_label ? `${tariff.short_label} ${rate.toFixed(2)}` : `${rate.toFixed(2)}`;
    const totalW = snap.total_ac_w || 0;
    const pcW = snap.pc_ac_w || 0;
    const monW = snap.monitors_w || 0;
    const today = snap.today || {};

    // KPIs
    $("kpi-total-w").textContent = totalW.toFixed(1);
    $("kpi-total-sub").textContent = `主機 ${pcW.toFixed(1)} W + 螢幕 ${monW.toFixed(1)} W` + (snap.external_w > 0 ? ` + 外接 ${snap.external_w.toFixed(1)} W` : "");
    $("kpi-today-kwh").textContent = (today.total_kwh || 0).toFixed(3);
    $("kpi-today-cost").textContent = `約 NT$ ${(today.total_cost_ntd || 0).toFixed(2)}（${rateTag} 元/度）`;
    $("kpi-mon-w").textContent = monW.toFixed(1);
    $("kpi-mon-sub").textContent = `今日顯示器 ${(today.monitors_kwh || 0).toFixed(3)} 度 (NT$ ${(today.monitors_cost_ntd || 0).toFixed(2)}) ｜ 開啟 ${fmtDur(today.monitor_on_sec)}`;

    const tm = snap.this_month || {};
    $("kpi-month-label").textContent = tm.month_label ? `${tm.month_label} 日曆累計` : "本月日曆累計";
    $("kpi-month-kwh").textContent = (tm.total_kwh || 0).toFixed(3);
    $("kpi-month-sub").textContent = `約 NT$ ${(tm.total_cost_ntd || 0).toFixed(2)} ｜ 已記錄 ${tm.active_days || 0} 天 (${fmtDur(tm.duration_sec)})`;

    // Status pill (objective telemetry status)
    const pill = $("live-status-pill");
    pill.classList.remove("waste");
    $("live-status-text").textContent = "🟢 即時監控中";

    // Sensor badges
    const sens = snap.sensors || {};
    const modeLabelMap = {
      realtime_1s: "視窗即時 1s",
      eco_3s: "背景省電 3s",
      deep_eco_5s: "深度省電 5s",
    };
    const modeText = modeLabelMap[snap.power_mode] || "省電監控";
    $("sensor-badges").innerHTML = [
      `<span class="sensor-chip ${sens.nvml_active ? "ok" : ""}">GPU: ${sens.nvml_active ? "NVML實測" : "模型/LHM"}</span>`,
      `<span class="sensor-chip ${sens.cpu_rapl_active ? "ok" : ""}">CPU: ${sens.cpu_rapl_active ? "RAPL實測" : "模型估算"}</span>`,
      `<span class="sensor-chip ${sens.ddcci_any ? "ok" : ""}">螢幕: ${sens.ddcci_any ? "DDC/CI偵測" : "EDID+電源事件"}</span>`,
      `<span class="sensor-chip ok">${modeText}</span>`,
    ].join("");

    // PawnIO notice banner
    const banner = $("pawnio-banner");
    if (!sens.cpu_rapl_active && !state.bannerDismissed) {
      banner.classList.remove("hidden");
      if (sens.pawnio_installed && !sens.is_admin) {
        $("pawnio-banner-msg").textContent = "偵測到系統已安裝 PawnIO，請使用「啟動_WattWatch.vbs」重新啟動以取得系統管理員權限並啟用 CPU 實測功耗。";
      }
    } else {
      banner.classList.add("hidden");
    }

    // PawnIO status in settings
    if ($("pawnio-status-text")) {
      if (sens.cpu_rapl_active) {
        $("pawnio-status-text").textContent = "🟢 已啟用統一硬體實測模式 (PawnIO + 管理員權限)，關閉視窗時自動切換背景省電降頻採樣！";
      } else if (sens.pawnio_installed) {
        $("pawnio-status-text").textContent = "🟡 已安裝 PawnIO，請以系統管理員權限啟動（使用 啟動_WattWatch.vbs）即可啟用 CPU 實測。";
      } else {
        $("pawnio-status-text").textContent = "尚未安裝 PawnIO 驅動（目前使用 CPU TDP + 即時負載模型估算）。";
      }
    }

    // Components grid
    const grid = $("components-grid");
    grid.innerHTML = (snap.components || [])
      .map((c) => {
        const badgeCls = c.confidence === "measured" ? "badge-measured" : (c.confidence === "calibrated" ? "badge-calibrated" : "badge-estimated");
        const icon = c.confidence === "measured" ? "🟢" : (c.confidence === "calibrated" ? "🔵" : "🟡");
        return `
          <div class="comp-card">
            <div class="comp-top">
              <span class="comp-name">${escapeHtml(c.name)}</span>
              <span class="comp-watts" style="color:${c.color}">${c.watts.toFixed(1)} W</span>
            </div>
            <div class="comp-detail">${escapeHtml(c.detail)}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, c.share_pct)}%; background:${c.color}"></div></div>
            <div class="comp-bottom">
              <span class="badge ${badgeCls}">${icon} ${escapeHtml(c.source_label)}</span>
              <span>佔比 ${c.share_pct.toFixed(1)}% ｜ 今日 ${c.today_wh.toFixed(1)} Wh (NT$${c.today_cost_ntd.toFixed(2)})</span>
            </div>
          </div>
        `;
      })
      .join("");

    drawLiveChart();
    renderDebate();
  }

  function renderHardware(hw) {
    state.hardware = hw;
    if (!hw) return;
    const sys = hw.system || {};
    const cpu = hw.cpus?.[0];
    const gpu = hw.gpus?.[0];
    const mon = hw.monitors?.[0];
    $("system-summary").textContent =
      `${sys.form_factor || "電腦"} ｜ ${cpu ? cpu.name : ""} ｜ ${gpu ? gpu.name : ""} ｜ ${mon ? mon.name + " " + mon.diagonal_in + "\"" : ""}`;

    const items = [
      { title: "平台與主機板", body: `${sys.form_factor || "桌上型"}｜${sys.motherboard || "未知主機板"}` },
      { title: "處理器 CPU", body: (hw.cpus || []).map((c) => `${c.name} (${c.cores}C/${c.threads}T)`).join("<br>") || "未偵測" },
      { title: "顯示卡 GPU", body: (hw.gpus || []).map((g) => g.name).join("<br>") || "無獨立顯卡" },
      { title: "記憶體 RAM", body: hw.ram ? `${hw.ram.label} (${hw.ram.manufacturer || "系統偵測"})` : "未知" },
      { title: "儲存裝置", body: (hw.disks || []).map((d) => `${d.name} (${d.kind}, ${d.size_gb}GB)`).join("<br>") || "無" },
      { title: "USB 週邊設備（已去重）", body: (hw.peripherals || []).map((p) => `${p.name} (~${p.watts}W)`).join("<br>") || "無外接週邊" },
    ];
    $("hw-summary-grid").innerHTML = items.map((i) => `<div class="hw-item"><strong>${escapeHtml(i.title)}</strong><div>${i.body}</div></div>`).join("");
  }

  function getDebateNumbers() {
    const snap = state.live;
    if (!snap) return null;
    const tariff = snap.tariff || {};
    const curRate = snap.electricity_rate_ntd || 1.99;
    let simRate = curRate;
    let yearAvgRate = curRate;
    let periodNote = tariff.short_label || "固定費率";

    if (tariff.mode === "tou_2tier") {
      if (state.simPeriod === "peak") {
        simRate = tariff.peak_rate || curRate;
        // 4mo summer peak 5.16, 8mo non-summer peak 4.93
        yearAvgRate = (122 * 5.16 + 243 * 4.93) / 365;
        periodNote = `${tariff.season_label || ""}純尖峰 (${simRate.toFixed(2)} 元/度)`;
      } else if (state.simPeriod === "offpeak") {
        simRate = tariff.offpeak_rate || curRate;
        yearAvgRate = (122 * 2.06 + 243 * 1.99) / 365;
        periodNote = `${tariff.season_label || ""}純離峰 (${simRate.toFixed(2)} 元/度)`;
      } else {
        simRate = tariff.weekly_avg_rate || curRate;
        yearAvgRate = tariff.annual_avg_rate || simRate;
        periodNote = `${tariff.season_label || ""}週均費率 (${simRate.toFixed(2)} 元/度)`;
      }
    }

    const mons = snap.monitors || [];
    const monActiveW = mons.reduce((acc, m) => acc + (m.state === "off" ? 25.0 : Math.max(0, m.watts - 0.5)), 0) || 30.0;
    const pcIdleApproxW = Math.min(snap.pc_ac_w || 85, 95);

    let extraW = monActiveW;
    let label = "僅計算顯示器開啟與待機差值（開啟功耗 − 待機 0.5W）";
    if (state.scenario === "pc_idle_monitor") {
      extraW = monActiveW + Math.max(25, pcIdleApproxW - 3.0);
      label = "主機待機＋顯示器開啟合計功耗（相較睡眠狀態）";
    } else if (state.scenario === "current_total") {
      extraW = Math.max(10, (snap.total_ac_w || 120) - 3.0);
      label = "目前整組電腦總功耗（主機＋顯示器＋外接設備）";
    }

    const hrs = state.wasteHours;
    const dayKwh = (extraW * hrs) / 1000;
    const monthKwh = dayKwh * 30;
    const yearKwh = dayKwh * 365;
    return {
      rate: simRate,
      yearRate: yearAvgRate,
      periodNote,
      isTou: tariff.mode === "tou_2tier",
      extraW,
      monActiveW,
      label,
      hrs,
      dayKwh,
      dayNtd: dayKwh * simRate,
      monthKwh,
      monthNtd: monthKwh * simRate,
      yearKwh,
      yearNtd: yearKwh * yearAvgRate,
    };
  }

  function renderDebate() {
    const snap = state.live;
    if (!snap) return;
    const mons = snap.monitors || [];
    const stateTextMap = { on: "🟢 開啟中", dimmed: "🟡 變暗中", off: "⚫ 已待機/關閉", disconnected: "⚪ 未連接" };

    $("debate-monitors-list").innerHTML = mons.length
      ? mons
          .map(
            (m) => `
        <div class="mon-card">
          <div class="mon-head">
            <span>${escapeHtml(m.name)} (${m.diagonal_in}" ${escapeHtml(m.panel_type)})</span>
            <span style="color:#38bdf8">${m.watts.toFixed(1)} W</span>
          </div>
          <div class="mon-meta">
            ${escapeHtml(m.resolution)} @ ${m.refresh_hz}Hz ｜ 狀態：${stateTextMap[m.state] || m.state} ｜ 亮度：${m.brightness_pct}% ${m.ddcci_brightness_reliable ? "(DDC/CI即時)" : "(預設/手動值)"}
          </div>
        </div>`
          )
          .join("")
      : `<div class="mon-card">未偵測到外接螢幕</div>`;

    const calc = getDebateNumbers();
    if (!calc) return;
    $("sim-period-tabs").classList.toggle("hidden", !calc.isTou);
    $("debate-calc-explain").textContent =
      `套用情境：${calc.label}，計算基準約 ${calc.extraW.toFixed(1)} W，計費基準：${calc.periodNote}` +
      (calc.isTou ? `（年電費已按夏月4個月＋非夏月8個月加權推算）` : "");
    $("waste-day-ntd").textContent = `NT$ ${calc.dayNtd.toFixed(1)}`;
    $("waste-day-kwh").textContent = `${calc.dayKwh.toFixed(3)} 度`;
    $("waste-month-ntd").textContent = `NT$ ${calc.monthNtd.toFixed(0)}`;
    $("waste-month-kwh").textContent = `${calc.monthKwh.toFixed(1)} 度`;
    $("waste-year-ntd").textContent = `NT$ ${calc.yearNtd.toFixed(0)}`;
    $("waste-year-kwh").textContent = `${calc.yearKwh.toFixed(1)} 度`;

    const hourKwh = calc.extraW / 1000;
    const hourNtd = hourKwh * calc.rate;
    $("debate-fun-compare").innerHTML =
      `📊 <strong>每小時單位換算：</strong>約消耗 <strong>${hourKwh.toFixed(3)} 度 (kWh)</strong>，折合電費約 <strong>NT$ ${hourNtd.toFixed(2)} / 小時</strong>（依 ${calc.periodNote}，不含基本電費）。`;

    const today = snap.today || {};
    $("debate-evidence-grid").innerHTML = [
      { label: "今日總監測時長", val: fmtDur(today.duration_sec) },
      { label: "今日顯示器開啟時長", val: fmtDur(today.monitor_on_sec) },
      { label: "今日顯示器用電佔比", val: `${(today.monitor_pct || 0).toFixed(1)}%` },
      { label: "今日全機累計電量與電費", val: `${(today.total_kwh || 0).toFixed(3)} 度 (NT$ ${(today.total_cost_ntd || 0).toFixed(2)})` },
    ]
      .map((x) => `<div class="ev-item"><span>${x.label}</span><strong>${x.val}</strong></div>`)
      .join("");
  }

  // Simulator interactive events
  document.querySelectorAll("#scenario-tabs .scenario-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#scenario-tabs .scenario-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.scenario = btn.dataset.scenario;
      renderDebate();
    });
  });

  document.querySelectorAll("#sim-period-tabs .scenario-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#sim-period-tabs .scenario-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.simPeriod = btn.dataset.period;
      renderDebate();
    });
  });

  $("debate-hours-slider").addEventListener("input", (e) => {
    state.wasteHours = parseFloat(e.target.value);
    $("debate-hours-label").textContent = `${state.wasteHours} 小時 / 天`;
    renderDebate();
  });

  $("btn-copy-battle-report").addEventListener("click", async () => {
    const snap = state.live;
    const calc = getDebateNumbers();
    if (!snap || !calc) return;
    const mon = (snap.monitors || [])[0];
    const monDesc = mon ? `${mon.name} (${mon.diagonal_in}吋 ${mon.resolution}@${mon.refresh_hz}Hz)` : "電腦顯示器";
    const today = snap.today || {};
    const tariffNote = calc.isTou ? "台電簡易型二段式時間電價（不含基本費）" : `固定費率 NT$ ${calc.rate.toFixed(2)}/度`;
    const report =
      `【WattWatch 電腦耗電統計摘要】\n` +
      `• 計費標準：${tariffNote}｜目前基準：${calc.periodNote}\n` +
      `• 顯示器：${monDesc}，開啟時約 ${calc.monActiveW.toFixed(1)} W（待機約 0.5 W）\n` +
      `• 主機＋顯示器即時總功耗：${snap.total_ac_w.toFixed(1)} W（主機 ${snap.pc_ac_w.toFixed(1)} W ＋ 顯示器 ${snap.monitors_w.toFixed(1)} W）\n` +
      `• 情境試算（${calc.label}，每日 ${calc.hrs} 小時）：\n` +
      `   - 每日預估：${calc.dayKwh.toFixed(3)} 度 ≈ NT$ ${calc.dayNtd.toFixed(1)}\n` +
      `   - 每月 (30天) 預估：${calc.monthKwh.toFixed(1)} 度 ≈ NT$ ${calc.monthNtd.toFixed(0)}\n` +
      `   - 每年 (365天) 預估：${calc.yearKwh.toFixed(1)} 度 ≈ NT$ ${calc.yearNtd.toFixed(0)}\n` +
      `• 今日累計用電：${(today.total_kwh || 0).toFixed(3)} 度（約 NT$ ${(today.total_cost_ntd || 0).toFixed(2)}）`;
    try {
      await navigator.clipboard.writeText(report);
      showToast("已複製用電統計摘要到剪貼簿！");
    } catch {
      showToast("複製失敗，請確認剪貼簿權限");
    }
  });

  // History
  document.querySelectorAll("#history-range-pills .range-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#history-range-pills .range-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.historyRange = btn.dataset.range;
      loadHistory();
    });
  });

  function shiftMonth(monthKey, delta) {
    const [y, m] = monthKey.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  $("btn-prev-month").addEventListener("click", () => {
    state.selectedMonth = shiftMonth(state.selectedMonth, -1);
    state.selectedCalDate = null;
    loadHistory();
  });

  $("btn-next-month").addEventListener("click", () => {
    state.selectedMonth = shiftMonth(state.selectedMonth, 1);
    state.selectedCalDate = null;
    loadHistory();
  });

  async function loadHistory() {
    try {
      let url = `/api/history?range=${encodeURIComponent(state.historyRange)}`;
      if (state.historyRange === "month") {
        url += `&month=${encodeURIComponent(state.selectedMonth)}`;
      }
      const r = await fetch(url);
      const data = await r.json();
      renderHistory(data);
    } catch (err) {
      console.error(err);
    }
  }

  function renderCalendar(data) {
    const panel = $("month-calendar-panel");
    if (state.historyRange !== "month" || !Array.isArray(data.calendar_days)) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    $("cal-month-title").textContent = data.month_label || state.selectedMonth;

    const days = data.calendar_days;
    const maxWh = Math.max(10, ...days.map((d) => d.total_wh || 0));
    const todayIso = `${nowDate.getFullYear()}-${String(nowDate.getMonth() + 1).padStart(2, "0")}-${String(nowDate.getDate()).padStart(2, "0")}`;

    const cells = [];
    for (let i = 0; i < (data.first_weekday_sun0 || 0); i++) {
      cells.push(`<div class="cal-cell empty"></div>`);
    }
    for (const d of days) {
      const heat = d.has_data ? (0.08 + 0.34 * Math.min(1, d.total_wh / maxWh)).toFixed(2) : "0";
      const cls = [
        "cal-cell",
        d.has_data ? "has-data" : "",
        d.date === todayIso ? "today" : "",
        d.date === state.selectedCalDate ? "selected" : "",
      ]
        .filter(Boolean)
        .join(" ");
      cells.push(`
        <div class="${cls}" data-date="${d.date}" style="--heat:${heat}">
          <div class="cal-day-num">${d.day}</div>
          ${
            d.has_data
              ? `<div><div class="cal-kwh">${d.total_kwh.toFixed(2)} 度</div><div class="cal-cost">NT$ ${d.cost_ntd.toFixed(1)}</div></div>`
              : `<div class="cal-cost" style="color:#475569">—</div>`
          }
        </div>
      `);
    }
    const grid = $("calendar-grid");
    grid.innerHTML = cells.join("");

    const detailBox = $("calendar-day-detail");
    const renderDayDetail = (iso) => {
      const item = days.find((x) => x.date === iso);
      if (!item) {
        detailBox.classList.add("hidden");
        return;
      }
      detailBox.classList.remove("hidden");
      if (!item.has_data) {
        detailBox.innerHTML = `<strong>${item.date}</strong>：當日尚無紀錄資料`;
        return;
      }
      detailBox.innerHTML =
        `<strong>${item.date}</strong> ｜ 總電量：<b>${item.total_kwh.toFixed(3)} 度</b> (${item.total_wh.toFixed(1)} Wh) ｜ 預估電費：<b>NT$ ${item.cost_ntd.toFixed(2)}</b><br/>` +
        `主機：${item.pc_wh.toFixed(1)} Wh ｜ 顯示器：${item.monitors_wh.toFixed(1)} Wh ｜ 記錄時長：${fmtDur(item.duration_sec)}（顯示器開啟 ${fmtDur(item.monitor_on_sec)}）`;
    };

    if (state.selectedCalDate) renderDayDetail(state.selectedCalDate);
    else if (days.some((d) => d.date === todayIso && d.has_data)) renderDayDetail(todayIso);
    else detailBox.classList.add("hidden");

    grid.querySelectorAll(".cal-cell:not(.empty)").forEach((cell) => {
      cell.addEventListener("click", () => {
        state.selectedCalDate = cell.dataset.date;
        grid.querySelectorAll(".cal-cell").forEach((c) => c.classList.remove("selected"));
        cell.classList.add("selected");
        renderDayDetail(state.selectedCalDate);
      });
    });
  }

  function renderHistory(data) {
    const avgW = data.duration_sec > 0 ? data.total_wh / (data.duration_sec / 3600) : 0;
    const subDur =
      state.historyRange === "month" && data.active_days !== undefined
        ? `記錄 ${data.active_days} / ${data.days_in_month} 天 (${fmtDur(data.duration_sec)})`
        : `記錄時長 ${fmtDur(data.duration_sec)}`;
    $("history-kpis").innerHTML = [
      { label: "期間總耗電", val: `${(data.total_kwh || 0).toFixed(4)} 度`, sub: `${(data.total_wh || 0).toFixed(1)} Wh` },
      { label: "期間預估電費", val: `NT$ ${(data.total_cost_ntd || 0).toFixed(2)}`, sub: subDur },
      { label: "顯示器用電佔比", val: `${(data.monitor_pct || 0).toFixed(1)}%`, sub: `${(data.monitors_wh || 0).toFixed(1)} Wh (NT$ ${(data.monitors_cost_ntd || 0).toFixed(2)})` },
      { label: "期間平均總功耗", val: `${avgW.toFixed(1)} W`, sub: `顯示器開啟 ${fmtDur(data.monitor_on_sec)}` },
    ]
      .map((k) => `<div class="subpanel"><div class="kpi-label">${k.label}</div><div style="font-size:20px;font-weight:700">${k.val}</div><div class="small-muted">${k.sub}</div></div>`)
      .join("");

    renderCalendar(data);
    drawHistoryBarChart(data.series || []);
    drawDonut(data);

    const totWh = data.total_wh || 0;
    const rows = (data.devices || []).map((d) => {
      const share = totWh > 0 ? (d.energy_wh / totWh) * 100 : 0;
      const durCell = d.active_sec > 0 ? fmtDur(d.active_sec) : "—";
      return `
        <tr>
          <td>${escapeHtml(d.device_name)}</td>
          <td>${escapeHtml(d.category)}</td>
          <td>${d.energy_wh.toFixed(2)}</td>
          <td>${d.energy_kwh.toFixed(4)}</td>
          <td>NT$ ${d.cost_ntd.toFixed(2)}</td>
          <td>${share.toFixed(1)}%</td>
          <td>${durCell}</td>
        </tr>
      `;
    });
    $("history-devices-tbody").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="7" style="text-align:center;color:#94a3b8">尚無紀錄資料</td></tr>`;
  }

  function drawHistoryBarChart(series) {
    const canvas = $("history-bar-chart");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 500;
    const h = 240;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    if (!series.length) {
      ctx.fillStyle = "#94a3b8";
      ctx.fillText("此區間尚無數據", 20, 40);
      return;
    }

    const padL = 44, padR = 12, padT = 16, padB = 28;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const maxV = Math.max(10, ...series.map((s) => s.total_wh || 0)) * 1.15;

    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px sans-serif";
    ctx.strokeStyle = "rgba(148,163,184,0.16)";
    for (let i = 0; i <= 3; i++) {
      const y = padT + (plotH * i) / 3;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText(`${Math.round(maxV * (1 - i / 3))}Wh`, 4, y + 4);
    }

    const slotW = plotW / series.length;
    const barW = Math.max(4, Math.min(26, slotW * 0.65));
    series.forEach((item, idx) => {
      const x = padL + idx * slotW + (slotW - barW) / 2;
      const pcH = ((item.pc_wh || 0) / maxV) * plotH;
      const monH = ((item.monitors_wh || 0) / maxV) * plotH;
      const yPc = padT + plotH - pcH;
      const yMon = yPc - monH;
      ctx.fillStyle = "#a855f7";
      ctx.fillRect(x, yPc, barW, pcH);
      ctx.fillStyle = "#38bdf8";
      ctx.fillRect(x, yMon, barW, monH);

      if (series.length <= 12 || idx % Math.ceil(series.length / 8) === 0) {
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(item.label, Math.max(0, x - 4), h - 8);
      }
    });
  }

  function drawDonut(data) {
    const svg = $("history-donut");
    const leg = $("history-donut-legend");
    const slices = [
      { label: "顯示卡 GPU", val: data.gpu_wh || 0, color: "#a855f7" },
      { label: "外接螢幕", val: data.monitors_wh || 0, color: "#38bdf8" },
      { label: "處理器 CPU", val: data.cpu_wh || 0, color: "#22c55e" },
      { label: "主機板/RAM/硬碟/週邊", val: (data.mb_fans_wh || 0) + (data.ram_wh || 0) + (data.storage_wh || 0) + (data.peripherals_wh || 0), color: "#f59e0b" },
      { label: "電源轉換損耗", val: data.psu_loss_wh || 0, color: "#64748b" },
    ].filter((s) => s.val > 0.001);

    const total = slices.reduce((a, b) => a + b.val, 0);
    if (total <= 0) {
      svg.innerHTML = `<circle cx="100" cy="100" r="68" fill="none" stroke="#233252" stroke-width="26"/>`;
      leg.innerHTML = `<span class="small-muted">尚無耗電資料</span>`;
      return;
    }

    const r = 68;
    const circ = 2 * Math.PI * r;
    let offset = 0;
    svg.innerHTML = slices
      .map((s) => {
        const len = (s.val / total) * circ;
        const el = `<circle cx="100" cy="100" r="${r}" fill="none" stroke="${s.color}" stroke-width="26" stroke-dasharray="${len.toFixed(2)} ${circ.toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 100 100)"/>`;
        offset += len;
        return el;
      })
      .join("");

    leg.innerHTML = slices
      .map((s) => `<div class="legend-row"><span class="legend-swatch" style="background:${s.color}"></span><span>${s.label}: <b>${((s.val / total) * 100).toFixed(1)}%</b> (${s.val.toFixed(1)} Wh)</span></div>`)
      .join("");
  }

  // Settings
  async function loadConfig() {
    const r = await fetch("/api/config");
    const data = await r.json();
    state.configData = data;
    renderSettings(data);
  }

  function updateTariffModeVisibility() {
    const isFixed = $("cfg-tariff-mode").value === "fixed";
    $("tou-info-box").classList.toggle("hidden", isFixed);
    $("fixed-rate-fields").classList.toggle("hidden", !isFixed);
  }

  function renderSettings(data) {
    const cfg = data.config;
    $("cfg-tariff-mode").value = cfg.tariff_mode || "tou_2tier";
    const presetSel = $("cfg-tariff-preset");
    presetSel.innerHTML =
      Object.entries(data.presets)
        .map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)}</option>`)
        .join("") + `<option value="custom">自訂每度電價</option>`;
    presetSel.value = cfg.tariff_preset || "custom";

    $("cfg-rate-ntd").value = cfg.electricity_rate_ntd;
    updateTariffModeVisibility();

    const psuSel = $("cfg-psu-tier");
    psuSel.innerHTML = Object.entries(data.psu_tiers)
      .map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)}</option>`)
      .join("");
    psuSel.value = cfg.psu_efficiency_tier || "bronze";
    $("cfg-psu-watts").value = cfg.psu_rated_watts || 650;
    $("cfg-autostart").checked = Boolean(cfg.autostart);

    state.extDraft = (cfg.external_devices || []).map((d) => ({ ...d }));
    renderExtDraft();
    renderMonitorOverrides(cfg);
  }

  function renderMonitorOverrides(cfg) {
    const mons = state.hardware?.monitors || [];
    const liveMons = state.live?.monitors || [];
    const ovs = cfg.monitor_overrides || {};
    $("settings-monitors-list").innerHTML = mons.length
      ? mons
          .map((m) => {
            const ov = ovs[m.id] || {};
            const liveM = liveMons.find((x) => x.id === m.id);
            const panel = ov.panel_type || m.default_panel || "IPS";
            const bright = ov.manual_brightness ?? (liveM?.ddcci_brightness_reliable ? liveM.brightness_pct : 70);
            const cw = ov.custom_watts || "";
            const ddcciNote = liveM && !liveM.ddcci_brightness_reliable ? "（此螢幕 DDC/CI 未回報真實亮度，請手動微調亮度滑桿）" : "";
            return `
            <div class="subpanel monitor-ov-card" data-mon-id="${escapeHtml(m.id)}" style="margin-bottom:10px">
              <strong>${escapeHtml(m.model_name)} (${m.diagonal_in}" ${m.width_px}×${m.height_px}@${m.refresh_hz}Hz)</strong>
              <div class="small-muted">${escapeHtml(ddcciNote)}</div>
              <div class="form-row" style="margin-top:8px">
                <div class="form-group">
                  <label>面板類型</label>
                  <select class="ov-panel">
                    ${["IPS", "VA", "TN", "OLED", "Mini-LED"].map((p) => `<option value="${p}" ${p === panel ? "selected" : ""}>${p}</option>`).join("")}
                  </select>
                </div>
                <div class="form-group">
                  <label>實測校準瓦數 (W，留空用物理模型)</label>
                  <input type="number" class="ov-custom-w" step="0.5" min="0" max="500" placeholder="自動估算" value="${cw}" />
                </div>
              </div>
              <div class="form-group" style="margin-bottom:0">
                <label>螢幕常用亮度：<span class="ov-bright-val">${bright}%</span></label>
                <input type="range" class="ov-bright" min="0" max="100" value="${bright}" oninput="this.previousElementSibling.querySelector('.ov-bright-val').textContent=this.value+'%'" />
              </div>
            </div>`;
          })
          .join("")
      : `<div class="small-muted">未偵測到螢幕</div>`;
  }

  function renderExtDraft() {
    const list = $("ext-devices-list");
    list.innerHTML = state.extDraft.length
      ? state.extDraft
          .map(
            (d, i) => `
        <div class="ext-item">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" ${d.enabled ? "checked" : ""} data-idx="${i}" class="ext-chk" />
            <span>${escapeHtml(d.name)}</span>
          </label>
          <div style="display:flex;align-items:center;gap:10px">
            <strong>${Number(d.watts).toFixed(1)} W</strong>
            <button class="btn btn-sm btn-ghost ext-del" data-idx="${i}">刪除</button>
          </div>
        </div>`
          )
          .join("")
      : `<div class="small-muted">尚未新增任何額外電器</div>`;

    list.querySelectorAll(".ext-chk").forEach((c) => {
      c.addEventListener("change", () => {
        state.extDraft[Number(c.dataset.idx)].enabled = c.checked;
      });
    });
    list.querySelectorAll(".ext-del").forEach((b) => {
      b.addEventListener("click", () => {
        state.extDraft.splice(Number(b.dataset.idx), 1);
        renderExtDraft();
      });
    });
  }

  $("cfg-tariff-mode").addEventListener("change", updateTariffModeVisibility);

  $("cfg-tariff-preset").addEventListener("change", (e) => {
    const val = e.target.value;
    const preset = state.configData?.presets?.[val];
    if (preset) $("cfg-rate-ntd").value = preset.rate;
  });

  $("cfg-rate-ntd").addEventListener("input", () => {
    $("cfg-tariff-preset").value = "custom";
  });

  $("btn-add-ext").addEventListener("click", () => {
    const name = $("ext-name").value.trim();
    const watts = parseFloat($("ext-watts").value);
    if (!name || isNaN(watts) || watts <= 0) {
      showToast("請輸入外接設備名稱與有效瓦數");
      return;
    }
    state.extDraft.push({ name, watts, enabled: true });
    $("ext-name").value = "";
    $("ext-watts").value = "";
    renderExtDraft();
  });

  $("btn-save-settings").addEventListener("click", async () => {
    const overrides = {};
    document.querySelectorAll(".monitor-ov-card").forEach((card) => {
      const mid = card.dataset.monId;
      const cwRaw = card.querySelector(".ov-custom-w").value.trim();
      overrides[mid] = {
        panel_type: card.querySelector(".ov-panel").value,
        manual_brightness: parseInt(card.querySelector(".ov-bright").value, 10),
        custom_watts: cwRaw ? parseFloat(cwRaw) : null,
      };
    });

    const payload = {
      tariff_mode: $("cfg-tariff-mode").value,
      tariff_preset: $("cfg-tariff-preset").value,
      electricity_rate_ntd: parseFloat($("cfg-rate-ntd").value),
      psu_efficiency_tier: $("cfg-psu-tier").value,
      psu_rated_watts: parseInt($("cfg-psu-watts").value, 10) || 650,
      autostart: $("cfg-autostart").checked,
      monitor_overrides: overrides,
      external_devices: state.extDraft,
    };

    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const res = await r.json();
    if (res.ok) {
      $("settings-save-msg").textContent = "已儲存並即時套用！";
      showToast("設定已儲存！");
      setTimeout(() => ($("settings-save-msg").textContent = ""), 3000);
      loadConfig();
    }
  });

  // Top & Action Buttons
  async function rescanHardware() {
    showToast("正在重新掃描硬體與螢幕...");
    const r = await fetch("/api/hardware/rescan", { method: "POST" });
    const data = await r.json();
    if (data.hardware) {
      renderHardware(data.hardware);
      if (state.configData) renderMonitorOverrides(state.configData.config);
      showToast("硬體掃描完成！");
    }
  }
  $("btn-rescan-top").addEventListener("click", rescanHardware);

  $("btn-export-csv").addEventListener("click", () => {
    const rng = state.historyRange === "session" ? "today" : state.historyRange;
    let url = `/api/export.csv?range=${encodeURIComponent(rng)}`;
    if (rng === "month") url += `&month=${encodeURIComponent(state.selectedMonth)}`;
    window.location.href = url;
  });

  $("btn-reset-session").addEventListener("click", async () => {
    await fetch("/api/session/reset", { method: "POST" });
    showToast("本次啟動累積統計已歸零");
    if (state.historyRange === "session") loadHistory();
  });

  $("btn-dismiss-banner").addEventListener("click", () => {
    state.bannerDismissed = true;
    $("pawnio-banner").classList.add("hidden");
  });

  $("btn-go-pawnio").addEventListener("click", () => {
    document.querySelector('.tab-btn[data-tab="settings"]').click();
  });

  $("btn-install-pawnio").addEventListener("click", async () => {
    const btn = $("btn-install-pawnio");
    btn.disabled = true;
    btn.textContent = "處理中...";
    try {
      const r = await fetch("/api/pawnio/install", { method: "POST" });
      const res = await r.json();
      showToast(res.message || "操作完成");
    } catch {
      showToast("執行安裝請求失敗");
    } finally {
      btn.disabled = false;
      btn.textContent = "安裝 / 檢查 PawnIO";
    }
  });

  // WebSocket + Polling Fallback
  let pollTimer = null;
  function startPollFallback() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch("/api/live");
        if (r.ok) renderLive(await r.json());
      } catch {}
    }, 1200);
  }
  function stopPollFallback() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => stopPollFallback();
    ws.onmessage = (ev) => {
      try {
        renderLive(JSON.parse(ev.data));
      } catch {}
    };
    ws.onerror = () => startPollFallback();
    ws.onclose = () => {
      startPollFallback();
      setTimeout(connectWS, 2500);
    };
  }

  async function init() {
    try {
      const [hwRes, liveRes] = await Promise.all([fetch("/api/hardware"), fetch("/api/live")]);
      renderHardware(await hwRes.json());
      renderLive(await liveRes.json());
      await loadConfig();
    } catch (err) {
      console.error(err);
    }
    connectWS();
  }

  init();
})();
