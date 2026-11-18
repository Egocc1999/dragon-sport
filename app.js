(() => {
  "use strict";

  const PANELS = ["today", "fixtures", "record", "stats"];
  const LEAGUES = ["全部", "英超", "西甲", "德甲", "法甲"];
  const DISCLAIMER = "V1 紙上往績，提示會在開波前鎖定，賽後不改歷史；這不保證獲勝。";

  const state = {
    demo: false,
    tips: [],
    generatedAt: null,
    recordFilter: "全部",
    loadError: null,
    fixtures: [],
    fixturesGeneratedAt: null,
    fixturesError: null,
    fixtureLeague: "全部",
    fixtureStatus: "upcoming",
  };

  const $ = (id) => document.getElementById(id);

  function isDemoMode() {
    const q = new URLSearchParams(location.search);
    return q.get("demo") === "1";
  }

  function currentPanel() {
    const hash = (location.hash || "#today").replace("#", "");
    return PANELS.includes(hash) ? hash : "today";
  }

  function formatPl(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return { text: "不適用", cls: "" };
    }
    const n = Number(value);
    const abs = Math.abs(n).toFixed(2).replace(/\.?0+$/, (m) => (m === ".00" ? ".00" : m.replace(/0+$/, "").replace(/\.$/, "")));
    const shown = Math.abs(n).toFixed(2);
    if (n > 0) return { text: `+${shown}`, cls: "pl-pos" };
    if (n < 0) return { text: `−${shown}`, cls: "pl-neg" };
    return { text: "0.00", cls: "pl-zero" };
  }

  function statusMeta(tip) {
    const g = tip.grade_status || tip.result;
    if (tip.status === "void" || g === "void") return { text: "無效", cls: "status-void" };
    if (g === "won") return { text: "中", cls: "status-won" };
    if (g === "lost") return { text: "不中", cls: "status-lost" };
    if (g === "push") return { text: "走水", cls: "status-push" };
    if (tip.status === "settled") return { text: "已結算", cls: "status-locked" };
    return { text: "已鎖定", cls: "status-locked" };
  }

  function isSettled(tip) {
    return tip.status === "settled" || tip.status === "void";
  }

  function isOpen(tip) {
    return tip.status === "locked" || tip.status === "proposed";
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function metaRow(label, value, extraClass) {
    if (value === null || value === undefined || value === "") return "";
    const cls = extraClass ? ` class="${extraClass}"` : "";
    return `<li><span>${escapeHtml(label)}</span><span${cls}>${value}</span></li>`;
  }

  function renderCard(tip) {
    const st = statusMeta(tip);
    const odds = tip.reference_odds;
    const oddsText = typeof odds === "number" ? odds.toFixed(2) : "";
    const stake = tip.stake_units;
    const stakeText = typeof stake === "number" ? `${stake} 單位` : "";
    const pl = isSettled(tip) ? formatPl(tip.unit_pl) : null;
    const plText = pl
      ? (tip.unit_pl === null && tip.grade_status === "won"
        ? "無參考賠率，不計單位盈虧"
        : escapeHtml(pl.text))
      : "";
    const resultText = tip.result_label || (isSettled(tip) ? st.text : "");
    return `
      <article class="card">
        <div class="card-head">
          <span class="league-pill">${escapeHtml(tip.league || "")}</span>
          <span class="status-pill ${st.cls}">${escapeHtml(st.text)}</span>
        </div>
        <h2 class="match">${escapeHtml(tip.match || "")}</h2>
        <p class="kickoff">${escapeHtml(tip.kickoff_label || tip.kickoff || "")}</p>
        <div class="pick-row">
          <p class="pick-label">${escapeHtml(tip.market || "提示")}</p>
          <p class="pick-value">${escapeHtml(tip.pick || "")}</p>
        </div>
        <ul class="meta">
          ${metaRow("參考賠率", oddsText ? escapeHtml(oddsText) : "沒有提供")}
          ${metaRow("注碼", escapeHtml(stakeText))}
          ${metaRow("鎖定時間", escapeHtml(tip.lock_time_label || tip.lock_time || ""))}
          ${isSettled(tip) ? metaRow("賽果", escapeHtml(tip.score_label || "")) : ""}
          ${isSettled(tip) ? metaRow("結果", escapeHtml(resultText)) : ""}
          ${isSettled(tip) ? metaRow("單位盈虧", plText, pl && tip.unit_pl !== null ? pl.cls : "") : ""}
        </ul>
        <p class="rationale">${escapeHtml(tip.rationale || "")}</p>
      </article>
    `;
  }

  function emptyBox(title, body) {
    return `<div class="empty"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(body)}</p></div>`;
  }

  function sortNewest(tips) {
    return [...tips].sort((a, b) => String(b.kickoff || "").localeCompare(String(a.kickoff || "")));
  }

  function computeStats(tips) {
    const settled = tips.filter((t) => t.status === "settled");
    if (!settled.length) {
      return { has_settled_record: false, overall: null, by_league: {} };
    }
    const bucket = () => ({
      tips: 0, wins: 0, losses: 0, pushes: 0, voids: 0,
      unit_pl: 0, unit_pl_counted: 0, win_rate: null,
    });
    const add = (b, tip) => {
      const g = tip.grade_status || tip.result;
      if (g === "void" || tip.status === "void") {
        b.voids += 1;
        return;
      }
      b.tips += 1;
      if (g === "won") b.wins += 1;
      else if (g === "lost") b.losses += 1;
      else if (g === "push") b.pushes += 1;
      if (typeof tip.unit_pl === "number") {
        b.unit_pl += tip.unit_pl;
        b.unit_pl_counted += 1;
      }
    };
    const finalize = (b) => {
      const decided = b.wins + b.losses;
      b.win_rate = decided ? b.wins / decided : null;
      b.unit_pl = Math.round(b.unit_pl * 10000) / 10000;
      return b;
    };
    const overall = bucket();
    const by_league = {};
    for (const tip of tips) {
      if (tip.status !== "settled" && tip.status !== "void") continue;
      add(overall, tip);
      const lg = tip.league || "未分類";
      if (!by_league[lg]) by_league[lg] = bucket();
      add(by_league[lg], tip);
    }
    if (overall.tips === 0) {
      return { has_settled_record: false, overall: null, by_league: {} };
    }
    const leagues = {};
    for (const [k, v] of Object.entries(by_league)) {
      if (v.tips > 0) leagues[k] = finalize(v);
    }
    return { has_settled_record: true, overall: finalize(overall), by_league: leagues };
  }

  function pct(rate) {
    if (rate === null || rate === undefined) return "尚無已決出場次";
    return `${(rate * 100).toFixed(1)}%`;
  }


  const FIXTURE_STATUS_FILTERS = [
    { id: "upcoming", label: "未開" },
    { id: "finished", label: "已完" },
    { id: "all", label: "全部" },
  ];

  const FIXTURE_LIVE = new Set(["1H", "HT", "2H", "LIVE"]);
  const FIXTURE_FINISHED = new Set(["FT", "AET", "PEN"]);

  function kickoffTime(iso) {
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return "";
    return new Intl.DateTimeFormat("zh-HK", {
      timeZone: "Asia/Hong_Kong",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(dt);
  }

  function todayHkDate() {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Hong_Kong",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
  }

  function isUpcomingFixture(fx) {
    const st = fx.status || "";
    return st === "NS" || st === "PST" || FIXTURE_LIVE.has(st);
  }

  function isFinishedFixture(fx) {
    return FIXTURE_FINISHED.has(fx.status || "");
  }

  function sortFixtures(list, statusFilter) {
    const copy = [...list];
    const dir = statusFilter === "finished" ? -1 : 1;
    copy.sort((a, b) => {
      const cmp = String(a.kickoff || "").localeCompare(String(b.kickoff || ""));
      if (cmp !== 0) return dir * cmp;
      return dir * (Number(a.id || 0) - Number(b.id || 0));
    });
    return copy;
  }

  function fixtureStatusClass(fx) {
    const st = fx.status || "";
    if (FIXTURE_FINISHED.has(st)) return "status-locked";
    if (FIXTURE_LIVE.has(st)) return "status-won";
    if (st === "PST" || st === "CANC" || st === "ABD") return "status-void";
    return "status-locked";
  }

  function renderFixtureCard(fx) {
    const score = fx.score_label;
    const scoreHtml = score
      ? `<p class="fx-score">${escapeHtml(score)}</p>`
      : `<p class="fx-score is-pending">${escapeHtml(fx.status_label || "未開")}</p>`;
    return `
      <article class="fx-card">
        <p class="fx-time">${escapeHtml(kickoffTime(fx.kickoff))}</p>
        <div class="fx-head">
          <span class="league-pill">${escapeHtml(fx.league || "")}</span>
          <span class="status-pill ${fixtureStatusClass(fx)}">${escapeHtml(fx.status_label || "")}</span>
        </div>
        <h2 class="fx-match">${escapeHtml(fx.match || `${fx.home || ""} 對 ${fx.away || ""}`)}</h2>
        ${scoreHtml}
      </article>
    `;
  }

  function renderChoiceFilters(containerId, items, current, onPick, ariaLabel) {
    const box = $(containerId);
    if (!box) return;
    box.setAttribute("aria-label", ariaLabel);
    box.innerHTML = items.map((item) => {
      const id = typeof item === "string" ? item : item.id;
      const label = typeof item === "string" ? item : item.label;
      const pressed = current === id ? "true" : "false";
      return `<button type="button" class="filter-btn" data-id="${escapeHtml(id)}" aria-pressed="${pressed}">${escapeHtml(label)}</button>`;
    }).join("");
    box.querySelectorAll(".filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => onPick(btn.getAttribute("data-id")));
    });
  }

  function renderFixtures() {
    const list = $("fixtures-list");
    if (!list) return;
    renderChoiceFilters(
      "fixture-status-filters",
      FIXTURE_STATUS_FILTERS,
      state.fixtureStatus,
      (id) => {
        state.fixtureStatus = id || "upcoming";
        renderFixtures();
      },
      "按開波狀態篩選"
    );
    renderChoiceFilters(
      "fixture-league-filters",
      LEAGUES,
      state.fixtureLeague,
      (id) => {
        state.fixtureLeague = id || "全部";
        renderFixtures();
      },
      "按聯賽篩選"
    );
    if (state.fixturesError) {
      list.innerHTML = `<div class="error-box">無法讀取賽程資料。請用本機伺服器開啟網站。</div>`;
      return;
    }
    let rows = state.fixtures.slice();
    if (state.fixtureStatus === "upcoming") rows = rows.filter(isUpcomingFixture);
    else if (state.fixtureStatus === "finished") rows = rows.filter(isFinishedFixture);
    if (state.fixtureLeague !== "全部") {
      rows = rows.filter((fx) => fx.league === state.fixtureLeague);
    }
    if (!rows.length) {
      if (!state.fixtures.length) {
        list.innerHTML = emptyBox(
          "目前沒有賽程資料",
          "賽程會在擷取四大聯賽場次後在這裏按開波時間公開。現階段尚未寫入公開賽程。"
        );
      } else if (state.fixtureStatus === "upcoming") {
        list.innerHTML = emptyBox(
          "沒有即將開波的場次",
          "可改看「已完」或「全部」，或選擇其他聯賽。"
        );
      } else {
        list.innerHTML = emptyBox(
          "沒有符合條件的場次",
          "可改選其他狀態或聯賽。"
        );
      }
      return;
    }
    rows = sortFixtures(rows, state.fixtureStatus);
    const today = todayHkDate();
    const groups = [];
    for (const fx of rows) {
      const key = fx.kickoff_date || "";
      if (!groups.length || groups[groups.length - 1].date !== key) {
        groups.push({
          date: key,
          label: fx.kickoff_date_label || fx.kickoff_label || key,
          items: [],
        });
      }
      groups[groups.length - 1].items.push(fx);
    }
    list.innerHTML = groups.map((g) => {
      const isToday = g.date === today;
      const title = isToday ? `今天 · ${g.label}` : g.label;
      return `<section class="date-group${isToday ? " is-today" : ""}">
        <h2>${escapeHtml(title)}</h2>
        <div class="date-group-list">${g.items.map(renderFixtureCard).join("")}</div>
      </section>`;
    }).join("");
  }

  function renderToday() {
    const open = sortNewest(state.tips.filter(isOpen));
    const el = $("today-list");
    if (state.loadError) {
      el.innerHTML = `<div class="error-box">無法讀取提示資料。請用本機伺服器開啟網站，例如 python3 -m http.server。</div>`;
      return;
    }
    if (!open.length) {
      el.innerHTML = emptyBox(
        "目前沒有已鎖定的提示",
        "尚未有已鎖定、但仍未完場的提示。提示只會在開波前鎖定並公開；若模型沒有選項通過門檻，當日可以沒有提示。"
      );
      return;
    }
    el.innerHTML = open.map(renderCard).join("");
  }

  function renderFilters() {
    const box = $("league-filters");
    box.innerHTML = LEAGUES.map((name) => {
      const pressed = state.recordFilter === name ? "true" : "false";
      return `<button type="button" class="filter-btn" data-league="${escapeHtml(name)}" aria-pressed="${pressed}">${escapeHtml(name)}</button>`;
    }).join("");
    box.querySelectorAll(".filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.recordFilter = btn.getAttribute("data-league") || "全部";
        renderRecord();
      });
    });
  }

  function renderRecord() {
    renderFilters();
    const list = $("record-list");
    if (state.loadError) {
      list.innerHTML = `<div class="error-box">無法讀取往績資料。請用本機伺服器開啟網站。</div>`;
      return;
    }
    const settled = sortNewest(state.tips.filter((t) => t.status === "settled"));
    if (!settled.length) {
      list.innerHTML = emptyBox(
        "尚未有已結算往績",
        "紙上紀錄會在官方賽果確認後在這裏公開，並且不會改寫歷史。現階段沒有已結算提示。"
      );
      return;
    }
    const filtered = state.recordFilter === "全部"
      ? settled
      : settled.filter((t) => t.league === state.recordFilter);
    if (!filtered.length) {
      list.innerHTML = emptyBox(
        "此聯賽尚未有已結算提示",
        "可改選其他聯賽，或返回「全部」。"
      );
      return;
    }
    list.innerHTML = filtered.map(renderCard).join("");
  }

  function statCard(label, value, extraClass, wide) {
    const cls = extraClass ? ` ${extraClass}` : "";
    const wideCls = wide ? " wide" : "";
    return `<article class="stat-card${wideCls}"><p class="stat-label">${escapeHtml(label)}</p><p class="stat-value${cls}">${escapeHtml(value)}</p></article>`;
  }

  function renderStats() {
    const view = $("stats-view");
    if (state.loadError) {
      view.innerHTML = `<div class="error-box">無法讀取成績資料。請用本機伺服器開啟網站。</div>`;
      return;
    }
    const stats = computeStats(state.tips);
    if (!stats.has_settled_record) {
      view.innerHTML = emptyBox(
        "尚未有已結算成績",
        "沒有已結算提示時，不會顯示勝率或單位盈虧，以免被誤認為往績。"
      );
      return;
    }
    const o = stats.overall;
    const pl = formatPl(o.unit_pl);
    let html = `<div class="stats-grid">
      ${statCard("已結算提示", String(o.tips))}
      ${statCard("勝率", pct(o.win_rate))}
      ${statCard("單位盈虧", pl.text, pl.cls)}
      ${statCard("中／不中／走水", `${o.wins}／${o.losses}／${o.pushes}`)}
    </div>`;
    const order = ["英超", "西甲", "德甲", "法甲"];
    const names = [
      ...order.filter((n) => stats.by_league[n]),
      ...Object.keys(stats.by_league).filter((n) => !order.includes(n)),
    ];
    for (const name of names) {
      const b = stats.by_league[name];
      const bpl = formatPl(b.unit_pl);
      html += `<section class="league-block"><h2>${escapeHtml(name)}</h2>
        <div class="stats-grid">
          ${statCard("提示數目", String(b.tips))}
          ${statCard("勝率", pct(b.win_rate))}
          ${statCard("單位盈虧", bpl.text, bpl.cls, true)}
        </div></section>`;
    }
    view.innerHTML = html;
  }

  function showPanel(name) {
    document.querySelectorAll(".panel").forEach((p) => {
      p.hidden = p.dataset.panel !== name;
    });
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("is-active", t.dataset.tab === name);
      if (t.dataset.tab === name) t.setAttribute("aria-current", "page");
      else t.removeAttribute("aria-current");
    });
  }

  function renderUpdated() {
    const el = $("updated");
    const stamp = currentPanel() === "fixtures" ? state.fixturesGeneratedAt : state.generatedAt;
    if (!stamp) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = `資料更新：${stamp}（香港時間）`;
    const dt = new Date(stamp);
    if (!Number.isNaN(dt.getTime())) {
      const fmt = new Intl.DateTimeFormat("zh-HK", {
        timeZone: "Asia/Hong_Kong",
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(dt);
      el.textContent = `資料更新：${fmt}（香港時間）`;
    }
  }

  function renderAll() {
    const panel = currentPanel();
    showPanel(panel);
    renderToday();
    renderFixtures();
    renderRecord();
    renderStats();
    renderUpdated();
    const banner = $("demo-banner");
    banner.hidden = !state.demo;
  }

  async function loadData() {
    state.demo = isDemoMode();
    const url = state.demo ? "./data/example-tips.json" : "./data/tips.json";
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const doc = await res.json();
      state.tips = Array.isArray(doc.tips) ? doc.tips : [];
      state.generatedAt = doc.generated_at || null;
      state.loadError = null;
      if (state.demo) document.title = "示範資料，非真實往績 — DRAGON SPORTS";
    } catch (err) {
      state.tips = [];
      state.generatedAt = null;
      state.loadError = err;
    }
    try {
      const fxRes = await fetch("./data/fixtures.json", { cache: "no-store" });
      if (!fxRes.ok) throw new Error(String(fxRes.status));
      const fxDoc = await fxRes.json();
      state.fixtures = Array.isArray(fxDoc.fixtures) ? fxDoc.fixtures : [];
      state.fixturesGeneratedAt = fxDoc.generated_at || null;
      state.fixturesError = null;
    } catch (err) {
      state.fixtures = [];
      state.fixturesGeneratedAt = null;
      state.fixturesError = err;
    }
    renderAll();
  }

  window.addEventListener("hashchange", () => {
    showPanel(currentPanel());
    renderUpdated();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  }

  loadData();
})();
