(() => {
  "use strict";

  const architecture = window.MobilePreviewArchitecture;
  if (!architecture || typeof architecture.buildViewModel !== "function") {
    throw new Error("Frontend Architecture view model contract is required.");
  }

  const REQUEST_TIMEOUT_MS = 8000;
  const STREAM_STALE_MS = 45 * 1000;
  const ALLOWED_READS = new Set([
    "/api/analyze?include_latest=true",
    "/api/analysis-history?limit=50",
    "/api/macro",
    "/api/symbol"
  ]);
  const ALLOWED_STREAMS = new Set(["/api/market-stream"]);
  const ACTION_STATES = Object.freeze(["WAIT", "WATCH", "PREPARE", "READY", "MANAGE", "EXIT", "INVALIDATED"]);
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

  const DEMO_MARKET = {
    symbol: "BTCUSDT",
    pair_label: "BTC/USDT",
    price: 67420,
    last_update: "14 sec ago"
  };

  const DEMO_ANALYSES = [
    {
      id: "demo-btc-1h",
      symbol: "BTCUSDT",
      pair_label: "BTC/USDT",
      timeframe: "1h",
      timestamp: "2026-07-18T14:00:00+09:00",
      signal: "BUY",
      confidence: 84,
      analysis_json: {
        view: "상방 우위",
        confidence: 84,
        status: "waiting_for_entry",
        key_facts: [
          "1시간 상승 구조가 유지되고 있습니다.",
          "4시간 가격이 장기 이동평균 상단을 유지하고 있습니다.",
          "최근 상승 구간에서 거래량이 회복됐습니다."
        ],
        counter_scenario: [
          "15분 모멘텀이 둔화되고 있습니다.",
          "단기 파생시장 과열 가능성을 재확인해야 합니다."
        ],
        trade: { entry: 67350, stop: 66420, target: 68800, leverage: 2 },
        invalidation: "1시간 종가가 66,420 아래에서 마감하면 시나리오를 다시 평가합니다."
      },
      risk_guard: {
        verdict: "PASS",
        risk_reward_ratio: 1.56,
        risk_percent: 0.5,
        warnings: [],
        hard_blocks: []
      }
    },
    {
      id: "demo-eth-1h",
      symbol: "ETHUSDT",
      pair_label: "ETH/USDT",
      timeframe: "1h",
      timestamp: "2026-07-18T13:40:00+09:00",
      signal: "HOLD",
      confidence: 69,
      analysis_json: {
        view: "중립",
        confidence: 69,
        key_facts: ["방향성이 제한된 구간입니다."],
        counter_scenario: ["박스 이탈 전까지 확정적인 방향 근거가 부족합니다."],
        trade: { entry: null, stop: null, target: null, leverage: 1 }
      },
      risk_guard: { verdict: "SKIPPED", warnings: [], hard_blocks: [] }
    },
    {
      id: "demo-sol-1h",
      symbol: "SOLUSDT",
      pair_label: "SOL/USDT",
      timeframe: "1h",
      timestamp: "2026-07-18T13:25:00+09:00",
      signal: "SELL",
      confidence: 76,
      analysis_json: {
        view: "하방 우위",
        confidence: 76,
        key_facts: ["단기 지지 이탈 여부를 확인하고 있습니다."],
        counter_scenario: ["주요 저항 회복 시 하방 관점이 약해집니다."],
        trade: { entry: 172.2, stop: 176.8, target: 164.4, leverage: 1 },
        invalidation: "176.8 상단 회복 시 시나리오 무효"
      },
      risk_guard: { verdict: "CAUTION", warnings: ["변동성이 높습니다."], hard_blocks: [] }
    }
  ];

  const DEMO_MACRO = {
    _trad_markets: {
      SPX: { price: 6314.18, chg_pct: 0.42 },
      NDX: { price: 22784.7, chg_pct: 0.61 },
      VIX: { price: 19.82, chg_pct: 3.18 },
      GOLD: { price: 3371.5, chg_pct: 0.17 }
    },
    IBIT_PX: { value: 61.42, change24h: 0.73 }
  };

  const state = {
    market: {},
    analysis: null,
    candidates: [],
    macro: {},
    activeSymbol: "",
    currentTf: "1h",
    sources: { analysis: "loading", history: "loading", macro: "loading", symbol: "loading" },
    streamState: { market: "loading", account: "unavailable" },
    lastMarketReceipt: 0,
    marketStream: null
  };

  const elements = {
    demoBanner: document.querySelector("#decision-demo-banner"),
    dataStatus: document.querySelector("#decision-data-status"),
    dataStatusLabel: document.querySelector("#decision-data-status-label"),
    updated: document.querySelector("#decision-updated"),
    marketStateHeading: document.querySelector("#market-state-heading"),
    marketStateCopy: document.querySelector("#market-state-copy"),
    actionHeading: document.querySelector("#current-action-heading"),
    actionStatus: document.querySelector("#current-action-status"),
    actionCopy: document.querySelector("#current-action-copy"),
    actionNext: document.querySelector("#current-action-next"),
    opportunityPair: document.querySelector("#opportunity-pair"),
    opportunityTimeframe: document.querySelector("#opportunity-timeframe"),
    opportunityDirection: document.querySelector("#opportunity-direction"),
    opportunityGrade: document.querySelector("#opportunity-grade"),
    opportunityQuality: document.querySelector("#opportunity-quality"),
    opportunityRisk: document.querySelector("#opportunity-risk"),
    opportunityStatus: document.querySelector("#opportunity-status"),
    supportingEvidence: document.querySelector("#supporting-evidence"),
    counterEvidence: document.querySelector("#counter-evidence"),
    planEntry: document.querySelector("#plan-entry"),
    planStop: document.querySelector("#plan-stop"),
    planTarget: document.querySelector("#plan-target"),
    planLeverage: document.querySelector("#plan-leverage"),
    planInvalidation: document.querySelector("#plan-invalidation"),
    majorAssets: document.querySelector("#major-assets"),
    macroContext: document.querySelector("#macro-context"),
    setupSummaryHeading: document.querySelector("#setup-summary-heading"),
    setupSummaryCopy: document.querySelector("#setup-summary-copy"),
    railAction: document.querySelector("#rail-action"),
    railActionCopy: document.querySelector("#rail-action-copy"),
    railSetupPair: document.querySelector("#rail-setup-pair"),
    railSetupMeta: document.querySelector("#rail-setup-meta")
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function cleanText(value, fallback, maxLength) {
    const safeFallback = fallback === undefined ? "" : fallback;
    const safeMax = maxLength === undefined ? 180 : maxLength;
    if (typeof value !== "string" && typeof value !== "number") return safeFallback;
    const text = String(value).replace(/\s+/g, " ").trim();
    return text ? text.slice(0, safeMax) : safeFallback;
  }

  function finiteNumber(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value.replace(/,/g, ""));
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  }

  function formatNumber(value, digits) {
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: digits === undefined ? 2 : digits
    }).format(value);
  }

  function formatPrice(value) {
    if (!Number.isFinite(value)) return "—";
    const digits = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 1 ? 2 : 4;
    return formatNumber(value, digits);
  }

  function formatSignedPercent(value) {
    if (!Number.isFinite(value)) return "—";
    return (value > 0 ? "+" : "") + formatNumber(value, 2) + "%";
  }

  function normalizedSymbol(rawValue) {
    const raw = asObject(rawValue);
    return cleanText(raw.symbol, "", 30).toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function analysisFromStatus(payloadValue) {
    const payload = asObject(payloadValue);
    const result = asObject(payload.result);
    const latest = asObject(payload.latest_result);
    if (Object.keys(result).length) return result;
    if (Object.keys(latest).length) return latest;
    return null;
  }

  function deduplicateCandidates(rawCandidates) {
    const seen = new Set();
    return rawCandidates.filter((raw, index) => {
      const object = asObject(raw);
      const signature = [
        normalizedSymbol(object) || "unknown",
        cleanText(object.timestamp, "", 80),
        cleanText(object.id || object.scenario_id, String(index), 120)
      ].join("|");
      if (seen.has(signature)) return false;
      seen.add(signature);
      return true;
    });
  }

  function buildViewModel() {
    return architecture.buildViewModel({
      market: state.market,
      analysis: state.analysis,
      candidates: state.candidates,
      account: {},
      macro: state.macro,
      activeSymbol: state.activeSymbol,
      currentTf: state.currentTf,
      sources: state.sources,
      streamState: state.streamState,
      demo: demoMode
    });
  }

  function topSetup(viewModel) {
    const setups = asArray(viewModel.public.setups).slice();
    setups.sort((left, right) => {
      const leftQuality = Number.isFinite(left.quality) ? left.quality : -1;
      const rightQuality = Number.isFinite(right.quality) ? right.quality : -1;
      return rightQuality - leftQuality;
    });
    const analysis = viewModel.public.analysis;
    if (analysis && (analysis.symbol || analysis.pair)) return analysis;
    return setups[0] || null;
  }

  function riskDisplay(scenario) {
    const verdict = scenario ? cleanText(asObject(scenario.risk).verdict, "UNAVAILABLE", 20).toUpperCase() : "UNAVAILABLE";
    if (verdict === "PASS") return "▰▱▱▱ PASS";
    if (verdict === "CAUTION") return "▰▰▰▱ CAUTION";
    if (verdict === "BLOCK" || verdict === "INVALID") return "▰▰▰▰ " + verdict;
    if (verdict === "SKIPPED") return "▱▱▱▱ SKIPPED";
    return "▱▱▱▱ UNAVAILABLE";
  }

  function actionFor(scenario) {
    if (!scenario) {
      return {
        code: "WAIT",
        status: "유효한 시나리오 없음",
        copy: "조건이 명확하지 않은 시장에서는 거래하지 않는 것도 하나의 결정입니다.",
        next: "새로운 시장 구조 또는 진입 조건 확인"
      };
    }

    const rawStatus = cleanText(scenario.status, "", 60).toLowerCase();
    const verdict = cleanText(asObject(scenario.risk).verdict, "UNAVAILABLE", 20).toUpperCase();
    if (rawStatus.includes("invalid")) return { code: "INVALIDATED", status: "시나리오 무효", copy: "무효화 조건이 충족된 시나리오는 진입 대상으로 사용하지 마세요.", next: "새 분석 대기" };
    if (rawStatus.includes("exit")) return { code: "EXIT", status: "종료 조건 확인", copy: "현재 시나리오의 종료 조건을 우선 확인하세요.", next: "위험 노출 재평가" };
    if (rawStatus.includes("manage")) return { code: "MANAGE", status: "포지션 관리", copy: "새 진입보다 기존 포지션의 위험 관리가 우선입니다.", next: "손절·무효화 조건 확인" };
    if (rawStatus.includes("ready")) return { code: "READY", status: "조건 충족 상태", copy: "현재 조건이 유지되는지 마지막으로 재검증하세요.", next: "진입 조건과 무효화 조건 동시 확인" };
    if (verdict === "BLOCK" || verdict === "INVALID") return { code: "WAIT", status: "리스크 차단", copy: "시나리오 품질과 별개로 기계적 위험 조건이 실행을 막고 있습니다.", next: "리스크 차단 조건 해소" };
    if (!["long", "short"].includes(asObject(scenario.direction).key)) return { code: "WAIT", status: "방향성 부족", copy: "방향이 명확해질 때까지 관찰하세요.", next: "시장 구조 재확인" };
    if ((scenario.grade === "A" || scenario.grade === "B+") && verdict === "PASS") {
      return {
        code: "PREPARE",
        status: "진입 조건 접근",
        copy: "좋은 시나리오라도 추격하지 말고 계획된 진입 조건을 다시 확인하세요.",
        next: Number.isFinite(scenario.plan.entry) ? "Entry " + formatPrice(scenario.plan.entry) + " 부근에서 구조 재확인" : "가격 트리거 재확인"
      };
    }
    if ((scenario.grade === "A" || scenario.grade === "B+") && verdict === "CAUTION") {
      return { code: "WATCH", status: "주의 조건 동반", copy: "구조는 관찰할 가치가 있지만 위험 조건이 줄어드는지 기다리세요.", next: "CAUTION 조건 재평가" };
    }
    return { code: "WAIT", status: "조건 미충족", copy: "구조적 명확성과 위험 조건이 더 좋아질 때까지 기다리세요.", next: "다음 분석 갱신" };
  }

  function marketStateFor(scenario) {
    if (!scenario) return { title: "현재 유효한 시나리오가 없습니다", copy: "시장 구조 또는 진입 조건이 개선되면 새로운 시나리오를 표시합니다." };
    const direction = asObject(scenario.direction);
    const firstEvidence = asArray(asObject(scenario.evidence).supporting)[0];
    if (direction.key === "long") return { title: "상승 우위 · 조건 확인 구간 ↗", copy: cleanText(firstEvidence, "상승 구조가 유지되는지 확인하면서 추격 진입을 피합니다.") };
    if (direction.key === "short") return { title: "하락 우위 · 조건 확인 구간 ↘", copy: cleanText(firstEvidence, "하락 구조가 유지되는지 확인하면서 지지 이탈을 재검증합니다.") };
    if (direction.key === "neutral") return { title: "방향성 제한 · 관찰 구간", copy: cleanText(firstEvidence, "명확한 방향이 형성될 때까지 기다립니다.") };
    return { title: "시장 구조를 확인하고 있습니다", copy: "충분한 분석 근거가 도착할 때까지 판단을 미룹니다." };
  }

  function replaceList(container, items, emptyCopy) {
    container.replaceChildren();
    const source = items.length ? items.slice(0, 4) : [emptyCopy];
    source.forEach((copy) => {
      const item = document.createElement("li");
      item.textContent = cleanText(copy, "데이터 없음", 220);
      container.append(item);
    });
  }

  function renderDataStatus(viewModel) {
    const context = viewModel.public.context;
    const sourceStates = Object.values(asObject(context.sources));
    let stateName = context.marketStream;
    if (viewModel.mode === "demo") stateName = "demo";
    else if (sourceStates.includes("endpoint_failure")) stateName = "endpoint_failure";
    else if (context.marketStream === "stale") stateName = "stale";
    else if (context.marketStream === "ready" && sourceStates.some((item) => item === "ready")) stateName = "ready";
    else if (sourceStates.every((item) => item === "loading")) stateName = "loading";
    else if (!stateName || stateName === "unavailable") stateName = "unavailable";

    const labels = {
      ready: "LIVE · 데이터 연결",
      loading: "데이터 확인 중",
      stale: "STALE · 업데이트 지연",
      endpoint_failure: "일부 데이터 연결 실패",
      unavailable: "데이터 상태 확인 필요",
      demo: "DEMO DATA"
    };
    elements.dataStatus.dataset.state = stateName;
    elements.dataStatusLabel.textContent = labels[stateName] || "데이터 상태 확인 필요";
    const updatedAt = cleanText(viewModel.public.market.updatedAt, "", 80);
    elements.updated.textContent = updatedAt ? "Updated " + updatedAt : "업데이트 시각 미확인";
  }

  function renderOpportunity(scenario) {
    if (!scenario) {
      elements.opportunityPair.textContent = "유효한 시나리오 없음";
      elements.opportunityTimeframe.textContent = "—";
      elements.opportunityDirection.textContent = "WAIT";
      elements.opportunityDirection.dataset.direction = "unknown";
      elements.opportunityGrade.textContent = "—";
      elements.opportunityQuality.textContent = "—";
      elements.opportunityRisk.textContent = "▱▱▱▱ UNAVAILABLE";
      elements.opportunityStatus.textContent = "WAIT";
      replaceList(elements.supportingEvidence, [], "조건을 충족한 핵심 근거가 없습니다.");
      replaceList(elements.counterEvidence, [], "새 시나리오가 생기면 반대 근거를 함께 표시합니다.");
      elements.planEntry.textContent = "—";
      elements.planStop.textContent = "—";
      elements.planTarget.textContent = "—";
      elements.planLeverage.textContent = "—";
      elements.planInvalidation.textContent = "현재 실행 가능한 계획이 없습니다.";
      return;
    }

    const direction = asObject(scenario.direction);
    const action = actionFor(scenario);
    elements.opportunityPair.textContent = scenario.pair || scenario.symbol || "자산 미확인";
    elements.opportunityTimeframe.textContent = scenario.timeframe || "TF 미확인";
    elements.opportunityDirection.textContent = (direction.key === "long" ? "↗ " : direction.key === "short" ? "↘ " : "") + (direction.label || "방향 미확인");
    elements.opportunityDirection.dataset.direction = direction.key || "unknown";
    elements.opportunityGrade.textContent = scenario.grade || "—";
    elements.opportunityQuality.textContent = Number.isFinite(scenario.quality) ? formatNumber(scenario.quality, 0) + " / 100" : "—";
    elements.opportunityRisk.textContent = riskDisplay(scenario);
    elements.opportunityStatus.textContent = action.code;

    const evidence = asObject(scenario.evidence);
    replaceList(elements.supportingEvidence, asArray(evidence.supporting), "확인 가능한 지지 근거가 없습니다.");
    replaceList(elements.counterEvidence, asArray(evidence.counter), "확인 가능한 반대 근거가 없습니다.");

    const plan = asObject(scenario.plan);
    elements.planEntry.textContent = formatPrice(finiteNumber(plan.entry));
    elements.planStop.textContent = formatPrice(finiteNumber(plan.stop));
    elements.planTarget.textContent = formatPrice(finiteNumber(plan.target));
    elements.planLeverage.textContent = Number.isFinite(finiteNumber(plan.leverage)) ? formatNumber(finiteNumber(plan.leverage), 1) + "× suggested" : "—";
    elements.planInvalidation.textContent = cleanText(plan.invalidation, "무효화 조건 데이터 없음", 260);
  }

  function assetStatus(scenario) {
    if (!scenario) return "시장 데이터 확인";
    const direction = asObject(scenario.direction);
    if (direction.key === "long") return "상승 구조 · " + (scenario.grade || "등급 미확인");
    if (direction.key === "short") return "하락 구조 · " + (scenario.grade || "등급 미확인");
    if (direction.key === "neutral") return "중립 · 방향 확인 필요";
    return "유효 Setup 확인 중";
  }

  function renderAssets(viewModel) {
    elements.majorAssets.replaceChildren();
    const market = viewModel.public.market;
    const setups = asArray(viewModel.public.setups).slice().sort((left, right) => (right.quality || -1) - (left.quality || -1));
    const rows = [];
    const seen = new Set();

    if (market.symbol || market.pair) {
      const matching = setups.find((item) => item.symbol && item.symbol === market.symbol) || null;
      rows.push({ symbol: market.symbol || market.pair, pair: market.pair || market.symbol, scenario: matching, price: market.price });
      seen.add(market.symbol || market.pair);
    }

    setups.forEach((scenario) => {
      const key = scenario.symbol || scenario.pair;
      if (!key || seen.has(key) || rows.length >= 4) return;
      seen.add(key);
      rows.push({ symbol: key, pair: scenario.pair || key, scenario: scenario, price: null });
    });

    if (!rows.length) {
      const empty = document.createElement("p");
      empty.textContent = "표시할 주요 자산 데이터가 없습니다.";
      empty.className = "decision-empty-copy";
      elements.majorAssets.append(empty);
      return;
    }

    rows.forEach((row) => {
      const article = document.createElement("article");
      article.className = "decision-asset-row";
      article.setAttribute("role", "listitem");
      const copy = document.createElement("div");
      const name = document.createElement("strong");
      const status = document.createElement("small");
      const value = document.createElement("span");
      name.textContent = row.pair;
      status.textContent = assetStatus(row.scenario);
      value.textContent = Number.isFinite(row.price) ? formatPrice(row.price) : "분석 후보";
      copy.append(name, status);
      article.append(copy, value);
      elements.majorAssets.append(article);
    });
  }

  function renderMacro(viewModel) {
    elements.macroContext.replaceChildren();
    const macro = viewModel.public.macro;
    const items = [];
    ["SPX", "NDX", "VIX", "GOLD"].forEach((ticker) => {
      const value = asObject(asObject(macro.tradMarkets)[ticker]);
      items.push({ ticker: ticker, price: value.price, change: value.changePercent });
    });
    items.push({ ticker: "IBIT", price: asObject(macro.ibit).value, change: asObject(macro.ibit).change24h });

    items.forEach((item) => {
      const box = document.createElement("div");
      box.className = "decision-macro-item";
      const ticker = document.createElement("strong");
      const value = document.createElement("span");
      ticker.textContent = item.ticker;
      const priceCopy = Number.isFinite(item.price) ? formatPrice(item.price) : "—";
      const changeCopy = Number.isFinite(item.change) ? " · " + formatSignedPercent(item.change) : "";
      value.textContent = priceCopy + changeCopy;
      box.append(ticker, value);
      elements.macroContext.append(box);
    });
  }

  function renderSetupSummary(viewModel) {
    const setups = asArray(viewModel.public.setups);
    const highQuality = setups.filter((item) => item.grade === "A" || item.grade === "B+").length;
    const watchlist = setups.filter((item) => !["A", "B+"].includes(item.grade)).length;
    if (!setups.length) {
      elements.setupSummaryHeading.textContent = "현재 유효한 시나리오가 없습니다";
      elements.setupSummaryCopy.textContent = "조건이 명확하지 않은 시장에서는 거래하지 않는 것도 하나의 결정입니다.";
      return;
    }
    elements.setupSummaryHeading.textContent = String(setups.length) + " candidates · " + String(highQuality) + " high-quality";
    elements.setupSummaryCopy.textContent = String(watchlist) + " watchlist · 품질과 위험을 분리해 검토합니다.";
  }

  function render() {
    const viewModel = buildViewModel();
    const scenario = topSetup(viewModel);
    const action = actionFor(scenario);
    const marketState = marketStateFor(scenario);

    renderDataStatus(viewModel);
    elements.marketStateHeading.textContent = marketState.title;
    elements.marketStateCopy.textContent = marketState.copy;
    elements.actionHeading.textContent = action.code;
    elements.actionStatus.textContent = action.status;
    elements.actionCopy.textContent = action.copy;
    elements.actionNext.textContent = action.next;
    elements.railAction.textContent = action.code;
    elements.railActionCopy.textContent = action.copy;

    renderOpportunity(scenario);
    renderAssets(viewModel);
    renderMacro(viewModel);
    renderSetupSummary(viewModel);

    elements.railSetupPair.textContent = scenario ? (scenario.pair || scenario.symbol || "—") : "—";
    elements.railSetupMeta.textContent = scenario
      ? (scenario.direction.label || "방향 미확인") + " · " + (scenario.grade || "—") + " · Quality " + (Number.isFinite(scenario.quality) ? formatNumber(scenario.quality, 0) : "—")
      : "유효한 시나리오 대기";
  }

  function approvedReadPath(path) {
    const url = new URL(path, window.location.origin);
    const requestPath = url.pathname + url.search;
    if (url.origin !== window.location.origin || !ALLOWED_READS.has(requestPath) || url.hash) {
      throw new Error("허용되지 않은 읽기 경로");
    }
    return requestPath;
  }

  async function requestJson(path, timeoutMs) {
    const requestPath = approvedReadPath(path);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS);
    try {
      const response = await window.fetch(requestPath, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal
      });
      if (!response.ok) throw new Error("읽기 요청 실패");
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function loadLiveReads() {
    const paths = [
      "/api/analyze?include_latest=true",
      "/api/analysis-history?limit=50",
      "/api/macro",
      "/api/symbol"
    ];
    const results = await Promise.allSettled(paths.map((path) => requestJson(path)));
    state.sources.analysis = results[0].status === "fulfilled" ? "ready" : "error";
    state.sources.history = results[1].status === "fulfilled" ? "ready" : "error";
    state.sources.macro = results[2].status === "fulfilled" ? "ready" : "error";
    state.sources.symbol = results[3].status === "fulfilled" ? "ready" : "error";

    const candidates = [];
    if (results[0].status === "fulfilled") {
      const latest = analysisFromStatus(results[0].value);
      if (latest) {
        state.analysis = latest;
        candidates.push(latest);
      }
    }
    if (results[1].status === "fulfilled") {
      candidates.push.apply(candidates, asArray(asObject(results[1].value).entries));
    }
    if (results[2].status === "fulfilled") state.macro = asObject(results[2].value);
    if (results[3].status === "fulfilled") {
      const symbolPayload = asObject(results[3].value);
      state.activeSymbol = normalizedSymbol(symbolPayload);
      if (!state.market.symbol && symbolPayload.symbol) {
        state.market.symbol = symbolPayload.symbol;
        state.market.pair_label = symbolPayload.pair_label;
      }
    }
    state.candidates = deduplicateCandidates(candidates);
    render();
  }

  function approvedStreamPath(path) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !ALLOWED_STREAMS.has(url.pathname) || url.search || url.hash) {
      throw new Error("허용되지 않은 스트림 경로");
    }
    return url.pathname;
  }

  function connectMarketStream() {
    const path = approvedStreamPath("/api/market-stream");
    const source = new EventSource(path);
    state.marketStream = source;
    source.addEventListener("open", () => {
      state.streamState.market = "online";
      render();
    });
    source.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      if (!["snapshot", "market", "price"].includes(cleanText(message.type, "", 30))) return;
      const update = asObject(asObject(message).data);
      state.market = { ...state.market, ...update };
      state.lastMarketReceipt = Date.now();
      if (update.symbol) state.activeSymbol = normalizedSymbol(update);
      state.streamState.market = "online";
      render();
    });
    source.addEventListener("error", () => {
      state.streamState.market = source.readyState === EventSource.CLOSED ? "unavailable" : "stale";
      render();
    });
  }

  function monitorFreshness() {
    if (!state.lastMarketReceipt || state.streamState.market !== "online") return;
    if (Date.now() - state.lastMarketReceipt > STREAM_STALE_MS) {
      state.streamState.market = "stale";
      render();
    }
  }

  function loadDemoData() {
    elements.demoBanner.hidden = false;
    state.market = DEMO_MARKET;
    state.analysis = DEMO_ANALYSES[0];
    state.candidates = DEMO_ANALYSES;
    state.macro = DEMO_MACRO;
    state.activeSymbol = "BTCUSDT";
    state.sources = { analysis: "ready", history: "ready", macro: "ready", symbol: "ready" };
    state.streamState.market = "demo";
    render();
  }

  function closeStream() {
    if (state.marketStream) state.marketStream.close();
  }

  function initialize() {
    render();
    if (demoMode) {
      loadDemoData();
      return;
    }
    connectMarketStream();
    loadLiveReads();
    window.setInterval(monitorFreshness, 10000);
  }

  window.addEventListener("beforeunload", closeStream);
  initialize();
})();
