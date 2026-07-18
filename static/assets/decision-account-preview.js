(() => {
  "use strict";

  const architecture = window.MobilePreviewArchitecture;
  const foundation = window.MobilePreviewFoundation;
  if (!architecture || typeof architecture.buildViewModel !== "function") {
    throw new Error("Decision Layers view model contract is unavailable.");
  }

  const ALLOWED_READS = new Set([
    "/api/analyze?include_latest=true",
    "/api/analysis-history?limit=50"
  ]);
  const ALLOWED_STREAMS = new Set(["/api/account-stream"]);
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
  const canAccessPrivate = !foundation || !foundation.capabilities || typeof foundation.capabilities.canAccess !== "function"
    ? false
    : foundation.capabilities.canAccess("private");

  const DEMO_ACCOUNT = {
    updated_at: "2026-07-18T17:10:00+09:00",
    account_equity: 12482.37,
    wallet_balance: 12240.18,
    available_balance: 9180.44,
    today_total_pnl: 182.46,
    today_pnl_pct: 1.49,
    open_position_count: 2,
    open_position_notional: 6420.5,
    effective_leverage: 2.4,
    configured_leverage: 2,
    leverage_display: "혼합 2x~3x (가중평균 2.4x)",
    open_positions: [
      { symbol: "BTCUSDT", side: "롱", entry_price: 63840, mark_price: 65042.6, unrealized_pnl: 144.31, leverage: 2, liquidation_price: 41280, notional: 3902.56, margin_type: "isolated", tp_price: 66900, sl_price: 64200 },
      { symbol: "ETHUSDT", side: "숏", entry_price: 3490, mark_price: 3451.2, unrealized_pnl: 28.15, leverage: 3, liquidation_price: 4520, notional: 2517.94, margin_type: "cross", tp_price: 3360, sl_price: 3550 }
    ]
  };

  const DEMO_ANALYSES = [
    {
      symbol: "BTCUSDT",
      pair_label: "BTC/USDT",
      timestamp: "2026-07-18T17:08:00+09:00",
      signal: "BUY",
      confidence: 86,
      analysis_json: {
        view: "상방 우위",
        confidence: 86,
        key_facts: ["1시간 구조가 주요 이동평균 위에서 유지되고 있습니다."],
        inferences: ["계획 구간 유지 여부를 우선 확인합니다."],
        counter_scenario: ["64,200 아래 1시간 종가 마감 시 상방 구조가 약해집니다."],
        trade: { entry: 64800, stop: 64200, target: 66900, leverage: 2 },
        invalidation: "64,200 아래에서 1시간 종가 마감"
      },
      risk_guard: { verdict: "PASS", risk_reward_ratio: 3.5, leverage: 2, warnings: [], hard_blocks: [] }
    },
    {
      symbol: "ETHUSDT",
      pair_label: "ETH/USDT",
      timestamp: "2026-07-18T16:54:00+09:00",
      signal: "HOLD",
      confidence: 63,
      analysis_json: {
        view: "중립",
        confidence: 63,
        key_facts: ["방향 트리거가 아직 명확하지 않습니다."],
        inferences: ["기존 포지션은 계획과 무효화 조건을 다시 확인해야 합니다."],
        counter_scenario: ["3,550 상단 회복 시 기존 하방 관점을 재평가합니다."],
        trade: { entry: null, stop: null, target: null, leverage: 1 },
        invalidation: "현재 실행 가능한 신규 시나리오가 없습니다."
      },
      risk_guard: { verdict: "CAUTION", warnings: ["방향성이 약합니다."], hard_blocks: [] }
    }
  ];

  const state = {
    account: {},
    analysis: null,
    candidates: [],
    analysisState: "loading",
    accountStreamState: "loading",
    accountReceived: false,
    privacyHidden: true,
    accountStream: null
  };

  const elements = {
    demoBanner: document.querySelector("#account-demo-banner"),
    connection: document.querySelector("#account-connection"),
    privacyToggle: document.querySelector("#privacy-toggle"),
    accountEquity: document.querySelector("#account-equity"),
    accountAvailable: document.querySelector("#account-available"),
    accountPnl: document.querySelector("#account-pnl"),
    accountPositionCount: document.querySelector("#account-position-count"),
    accountWallet: document.querySelector("#account-wallet"),
    accountNotional: document.querySelector("#account-notional"),
    accountEffectiveLeverage: document.querySelector("#account-effective-leverage"),
    accountConfiguredLeverage: document.querySelector("#account-configured-leverage"),
    accountLeverageDisplay: document.querySelector("#account-leverage-display"),
    accountUpdated: document.querySelector("#account-updated"),
    positionMatchSummary: document.querySelector("#position-match-summary"),
    positionsState: document.querySelector("#positions-state"),
    positionsList: document.querySelector("#positions-list"),
    positionTemplate: document.querySelector("#position-template")
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function cleanText(value, fallback) {
    const safeFallback = fallback === undefined ? "" : fallback;
    if (typeof value !== "string" && typeof value !== "number") return safeFallback;
    const text = String(value).replace(/\s+/g, " ").trim();
    return text || safeFallback;
  }

  function finiteNumber() {
    for (const value of arguments) {
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (typeof value === "string") {
        const normalized = value.replace(/,/g, "").replace(/[xX배%$]/g, "").trim();
        if (/^-?\d+(?:\.\d+)?$/.test(normalized)) {
          const parsed = Number(normalized);
          if (Number.isFinite(parsed)) return parsed;
        }
      }
    }
    return null;
  }

  function normalizeSymbol(value) {
    if (typeof value === "object" && value) return cleanText(value.symbol, "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    return cleanText(value, "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function formatNumber(value, digits) {
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("ko-KR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    }).format(value);
  }

  function formatMoney(value, signed) {
    if (!Number.isFinite(value)) return "—";
    const prefix = signed && value > 0 ? "+" : "";
    return prefix + "$" + formatNumber(value, 2);
  }

  function formatPercent(value) {
    if (!Number.isFinite(value)) return "—";
    const prefix = value > 0 ? "+" : "";
    return prefix + formatNumber(value, 2) + "%";
  }

  function formatPrice(value) {
    if (!Number.isFinite(value)) return "—";
    const digits = Math.abs(value) >= 100 ? 2 : 4;
    return "$" + formatNumber(value, digits);
  }

  function formatLeverage(value) {
    return Number.isFinite(value) ? formatNumber(value, value % 1 === 0 ? 0 : 1) + "x" : "—";
  }

  function privateValue(value, formatter) {
    if (state.privacyHidden) return "••••••";
    return formatter(value);
  }

  function analysisFromStatus(payloadValue) {
    const payload = asObject(payloadValue);
    const result = asObject(payload.result);
    const latest = asObject(payload.latest_result);
    if (Object.keys(result).length) return result;
    if (Object.keys(latest).length) return latest;
    return null;
  }

  function deduplicateCandidates(values) {
    const seen = new Set();
    const output = [];
    asArray(values).forEach((value, index) => {
      const raw = asObject(value);
      const trade = asObject(asObject(raw.analysis_json).trade);
      const key = [normalizeSymbol(raw), cleanText(raw.timestamp, ""), finiteNumber(trade.entry), finiteNumber(trade.stop), finiteNumber(trade.target), index].join("|");
      const stable = key.replace(/\|\d+$/, "");
      if (seen.has(stable)) return;
      seen.add(stable);
      output.push(raw);
    });
    return output;
  }

  function buildCurrentViewModel() {
    return architecture.buildViewModel({
      account: state.account,
      analysis: state.analysis,
      candidates: state.candidates,
      market: {},
      macro: {},
      activeSymbol: "",
      currentTf: "1h",
      sources: { analysis: state.analysisState, history: state.analysisState },
      streamState: { market: "unavailable", account: state.accountStreamState },
      demo: demoMode
    });
  }

  function scenarioTimestamp(scenario) {
    const parsed = Date.parse(cleanText(scenario.timestamp, ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function sortedSetups(viewModel) {
    return asArray(asObject(viewModel.public).setups).slice().sort((left, right) => scenarioTimestamp(right) - scenarioTimestamp(left));
  }

  function latestScenarioForPosition(position, viewModel) {
    const symbol = normalizeSymbol(position);
    if (!symbol) return null;
    return sortedSetups(viewModel).find((scenario) => normalizeSymbol(scenario) === symbol) || null;
  }

  function positionDirection(position) {
    const side = cleanText(position.side, "").toLowerCase();
    if (/(롱|long|buy)/i.test(side)) return "long";
    if (/(숏|short|sell)/i.test(side)) return "short";
    return "unknown";
  }

  function positionAction(position, scenario) {
    if (!scenario) return "MANAGE";
    const risk = asObject(scenario.risk);
    const verdict = cleanText(risk.verdict, "UNAVAILABLE").toUpperCase();
    const hardBlocks = asArray(risk.hardBlocks);
    if (hardBlocks.length || verdict === "BLOCK" || verdict === "INVALID") return "EXIT";
    const positionSide = positionDirection(position);
    const scenarioSide = cleanText(asObject(scenario.direction).key, "unknown");
    if (positionSide !== "unknown" && scenarioSide !== "unknown" && scenarioSide !== "neutral" && positionSide !== scenarioSide) return "EXIT";
    return "MANAGE";
  }

  function setConnection(value) {
    state.accountStreamState = value;
    const labels = {
      loading: "연결 준비 중",
      online: "계좌 스트림 연결",
      reconnecting: "재연결 중",
      stale: "업데이트 지연",
      offline: "연결 확인 필요",
      demo: "샘플 모드",
      blocked: "Private 접근 불가"
    };
    elements.connection.dataset.state = value;
    elements.connection.textContent = labels[value] || "상태 확인 중";
  }

  function setBadge(element, prefix, value, tone) {
    element.textContent = prefix + " " + (value === null || value === undefined || value === "" ? "—" : String(value));
    if (tone) element.dataset.tone = tone;
    else element.removeAttribute("data-tone");
  }

  function renderSummary(account) {
    elements.privacyToggle.setAttribute("aria-pressed", String(state.privacyHidden));
    elements.privacyToggle.textContent = state.privacyHidden ? "민감 정보 표시" : "민감 정보 숨기기";
    elements.accountEquity.textContent = privateValue(account.equity, (value) => formatMoney(value, false));
    elements.accountAvailable.textContent = privateValue(account.availableBalance, (value) => formatMoney(value, false));
    elements.accountWallet.textContent = privateValue(account.walletBalance, (value) => formatMoney(value, false));
    elements.accountNotional.textContent = privateValue(account.openPositionNotional, (value) => formatMoney(value, false));
    const pnlText = formatMoney(account.todayPnl, true) + (Number.isFinite(account.todayPnlPercent) ? " · " + formatPercent(account.todayPnlPercent) : "");
    elements.accountPnl.textContent = state.privacyHidden ? "••••••" : pnlText;
    elements.accountPositionCount.textContent = Number.isFinite(account.openPositionCount) ? formatNumber(account.openPositionCount, 0) + "개" : String(asArray(account.positions).length) + "개";
    elements.accountEffectiveLeverage.textContent = formatLeverage(account.effectiveLeverage);
    elements.accountConfiguredLeverage.textContent = formatLeverage(account.configuredLeverage);
    elements.accountLeverageDisplay.textContent = cleanText(account.leverageDisplay, "포지션 레버리지 데이터 없음");
    elements.accountUpdated.textContent = account.updatedAt ? "최근 스냅샷 " + cleanText(account.updatedAt, "") : "계좌 스냅샷을 기다리고 있습니다.";
  }

  function renderPositions(account, viewModel) {
    const positions = asArray(account.positions);
    elements.positionsList.replaceChildren();

    if (!state.accountReceived) {
      elements.positionsState.hidden = false;
      elements.positionsState.firstElementChild.textContent = "계좌 데이터를 기다리고 있습니다.";
      elements.positionsState.lastElementChild.textContent = "계좌 스트림이 연결되면 현재 포지션을 표시합니다.";
      elements.positionMatchSummary.textContent = "연결 대기";
      return;
    }

    if (!positions.length) {
      elements.positionsState.hidden = false;
      elements.positionsState.firstElementChild.textContent = "오픈 포지션 없음";
      elements.positionsState.lastElementChild.textContent = "현재 계좌에 열린 포지션이 없습니다.";
      elements.positionMatchSummary.textContent = "0개 포지션";
      return;
    }

    elements.positionsState.hidden = true;
    let matched = 0;
    positions.forEach((position) => {
      const scenario = latestScenarioForPosition(position, viewModel);
      if (scenario) matched += 1;
      const action = positionAction(position, scenario);
      const fragment = elements.positionTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".position-card");
      card.dataset.action = action;
      fragment.querySelector(".position-symbol").textContent = cleanText(position.symbol, "심볼 미확인");
      fragment.querySelector(".position-side").textContent = cleanText(position.side, "방향 미확인");
      fragment.querySelector(".position-action").textContent = action;
      fragment.querySelector('[data-position="entry"]').textContent = formatPrice(position.entryPrice);
      fragment.querySelector('[data-position="mark"]').textContent = formatPrice(position.markPrice);
      fragment.querySelector('[data-position="pnl"]').textContent = privateValue(position.unrealizedPnl, (value) => formatMoney(value, true));
      fragment.querySelector('[data-position="leverage"]').textContent = formatLeverage(position.leverage);
      fragment.querySelector('[data-position="liquidation"]').textContent = formatPrice(position.liquidationPrice);
      const margin = Number.isFinite(position.notional) && Number.isFinite(position.leverage) && position.leverage !== 0 ? position.notional / position.leverage : null;
      const marginText = privateValue(margin, (value) => formatMoney(value, false));
      fragment.querySelector('[data-position="margin"]').textContent = marginText + (position.marginType ? " · " + cleanText(position.marginType, "") : "");
      fragment.querySelector('[data-position="tp"]').textContent = formatPrice(position.takeProfit !== null ? position.takeProfit : scenario ? scenario.plan.target : null);
      fragment.querySelector('[data-position="sl"]').textContent = formatPrice(position.stopLoss !== null ? position.stopLoss : scenario ? scenario.plan.stop : null);

      const title = fragment.querySelector(".scenario-title");
      const grade = fragment.querySelector(".scenario-grade");
      const quality = fragment.querySelector(".scenario-quality");
      const risk = fragment.querySelector(".scenario-risk");
      const direction = fragment.querySelector(".scenario-direction");
      const invalidation = fragment.querySelector(".scenario-invalidation");
      if (!scenario) {
        title.textContent = "시나리오 연결 없음";
        setBadge(grade, "Grade", "—");
        setBadge(quality, "Quality", "—");
        setBadge(risk, "Risk", "UNAVAILABLE");
        direction.textContent = "—";
        invalidation.textContent = "동일 심볼의 저장 분석을 찾지 못했습니다.";
      } else {
        title.textContent = (scenario.pair || scenario.symbol || "자산 미확인") + " · " + action;
        setBadge(grade, "Grade", scenario.grade);
        setBadge(quality, "Quality", Number.isFinite(scenario.quality) ? Math.round(scenario.quality) : "—");
        const verdict = cleanText(asObject(scenario.risk).verdict, "UNAVAILABLE");
        setBadge(risk, "Risk", verdict, verdict === "PASS" ? "pass" : verdict === "BLOCK" || verdict === "INVALID" ? "block" : "");
        direction.textContent = cleanText(asObject(scenario.direction).label, "방향 미확인");
        invalidation.textContent = cleanText(asObject(scenario.plan).invalidation, "무효화 조건 미확인");
      }
      elements.positionsList.append(fragment);
    });
    elements.positionMatchSummary.textContent = positions.length + "개 중 " + matched + "개 시나리오 연결";
  }

  function renderAll() {
    const viewModel = buildCurrentViewModel();
    const account = asObject(viewModel.private).account || {};
    renderSummary(account);
    renderPositions(account, viewModel);
  }

  function approvedReadPath(path) {
    const url = new URL(path, window.location.origin);
    const exact = url.pathname + url.search;
    if (url.origin !== window.location.origin || !ALLOWED_READS.has(exact)) throw new Error("승인되지 않은 읽기 경로입니다.");
    return exact;
  }

  function approvedStreamPath(path) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !ALLOWED_STREAMS.has(url.pathname) || url.search || url.hash) throw new Error("승인되지 않은 스트림 경로입니다.");
    return url.pathname;
  }

  async function requestJson(path) {
    const approved = approvedReadPath(path);
    const response = await window.fetch(approved, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error("읽기 요청 실패");
    return response.json();
  }

  async function loadAnalysisReads() {
    const paths = ["/api/analyze?include_latest=true", "/api/analysis-history?limit=50"];
    const results = await Promise.allSettled(paths.map((path) => requestJson(path)));
    const rawCandidates = [];
    if (results[0].status === "fulfilled") {
      const latest = analysisFromStatus(results[0].value);
      if (latest) {
        state.analysis = latest;
        rawCandidates.push(latest);
      }
    }
    if (results[1].status === "fulfilled") {
      rawCandidates.push.apply(rawCandidates, asArray(asObject(results[1].value).entries));
    }
    state.candidates = deduplicateCandidates(rawCandidates);
    state.analysisState = results.every((item) => item.status === "fulfilled") ? "ready" : "error";
    renderAll();
  }

  function connectAccountStream() {
    const path = approvedStreamPath("/api/account-stream");
    const source = new EventSource(path);
    state.accountStream = source;
    source.addEventListener("open", () => setConnection("online"));
    source.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      const message = asObject(payload);
      if (!["snapshot", "account"].includes(cleanText(message.type, ""))) return;
      state.account = { ...state.account, ...asObject(message.data) };
      state.accountReceived = true;
      setConnection("online");
      renderAll();
    });
    source.addEventListener("error", () => {
      setConnection(source.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    });
  }

  function loadDemoData() {
    elements.demoBanner.hidden = false;
    state.account = DEMO_ACCOUNT;
    state.accountReceived = true;
    state.analysis = DEMO_ANALYSES[0];
    state.candidates = DEMO_ANALYSES;
    state.analysisState = "demo";
    setConnection("demo");
    renderAll();
  }

  function preserveDemoPublicLinks() {
    if (!demoMode) return;
    document.querySelectorAll('a[href^="/assets/decision-preview.html"]').forEach((link) => {
      const target = new URL(link.getAttribute("href") || "/assets/decision-preview.html", window.location.origin);
      link.setAttribute("href", target.pathname + "?demo=1" + target.hash);
    });
  }

  function bindEvents() {
    elements.privacyToggle.addEventListener("click", () => {
      state.privacyHidden = !state.privacyHidden;
      renderAll();
    });
    window.addEventListener("beforeunload", () => {
      if (state.accountStream) state.accountStream.close();
    });
  }

  function initialize() {
    preserveDemoPublicLinks();
    bindEvents();
    renderAll();
    if (!canAccessPrivate) {
      setConnection("blocked");
      return;
    }
    if (demoMode) {
      loadDemoData();
      return;
    }
    connectAccountStream();
    loadAnalysisReads();
  }

  window.addEventListener("DOMContentLoaded", initialize);
})();
