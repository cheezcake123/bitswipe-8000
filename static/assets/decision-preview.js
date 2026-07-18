(() => {
  "use strict";

  const architecture = window.MobilePreviewArchitecture;
  if (!architecture || typeof architecture.buildViewModel !== "function") {
    throw new Error("Decision Layers view model contract is unavailable.");
  }

  const ALLOWED_READS = new Set([
    "/api/analyze?include_latest=true",
    "/api/analysis-history?limit=50",
    "/api/macro",
    "/api/symbol"
  ]);
  const ALLOWED_STREAMS = new Set([
    "/api/market-stream"
  ]);
  const ACTION_STATES = Object.freeze({
    WAIT: "WAIT",
    WATCH: "WATCH",
    PREPARE: "PREPARE",
    READY: "READY",
    MANAGE: "MANAGE",
    EXIT: "EXIT",
    INVALIDATED: "INVALIDATED"
  });
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

  const DEMO_MARKET = {
    symbol: "BTCUSDT",
    pair_label: "BTC/USDT",
    price: 65042.6,
    last_update: "2026-07-18T14:20:00+09:00"
  };
  const DEMO_MACRO = {
    _trad_markets: {
      SPX: { price: 6314.18, chg_pct: 0.42 },
      NDX: { price: 22784.7, chg_pct: 0.61 },
      VIX: { price: 16.42, chg_pct: -2.18 },
      GOLD: { price: 3371.5, chg_pct: 0.17 }
    },
    IBIT_PX: { value: 61.42, change24h: 0.73 }
  };
  const DEMO_ANALYSES = [
    {
      id: "demo-btc-ready",
      symbol: "BTCUSDT",
      pair_label: "BTC/USDT",
      timestamp: "2026-07-18T14:18:00+09:00",
      signal: "BUY",
      confidence: 86,
      analysis_json: {
        view: "상방 우위",
        confidence: 86,
        key_facts: [
          "1시간 구조가 EMA 9·SMA 50 위에서 유지되고 있습니다.",
          "64,800 상방 트리거 위에서 가격이 유지되고 있습니다.",
          "거래량 참여가 최근 평균보다 높습니다."
        ],
        inferences: [
          "상승 구조는 유지되지만 추격보다 계획 구간 재확인이 우선입니다.",
          "리스크 가드 PASS와 구조적 명확도가 동시에 확인됩니다."
        ],
        counter_scenario: [
          "64,200 아래에서 1시간 종가가 마감되면 상방 구조가 약해집니다.",
          "거래량이 급감하면 돌파 신뢰도를 다시 평가해야 합니다."
        ],
        trade: { entry: 64800, stop: 64200, target: 66900, leverage: 2, risk_percent: 1 },
        invalidation: "64,200 아래에서 1시간 종가 마감",
        actions: { conservative: "진입 구간 지지와 거래량을 함께 확인" }
      },
      risk_guard: { verdict: "PASS", risk_reward_ratio: 3.5, leverage: 2, risk_percent: 1, warnings: [], hard_blocks: [] }
    },
    {
      id: "demo-sol-prepare",
      symbol: "SOLUSDT",
      pair_label: "SOL/USDT",
      timestamp: "2026-07-18T14:05:00+09:00",
      signal: "SELL",
      confidence: 78,
      analysis_json: {
        view: "하방 우위",
        confidence: 78,
        key_facts: ["148 하방 트리거가 계획에 포함되어 있습니다.", "변동성 범위가 확대되고 있습니다."],
        inferences: ["지지 이탈이 확인되면 하방 시나리오가 강화될 수 있습니다."],
        counter_scenario: ["152 회복 시 하방 관점이 약해집니다."],
        trade: { entry: 147.8, stop: 152.1, target: 139.2, leverage: 1, risk_percent: 1 },
        invalidation: "152.1 위에서 시나리오 무효"
      },
      risk_guard: { verdict: "CAUTION", risk_reward_ratio: 2, leverage: 1, risk_percent: 1, warnings: ["변동성 확대 구간입니다."], hard_blocks: [] }
    },
    {
      id: "demo-eth-watch",
      symbol: "ETHUSDT",
      pair_label: "ETH/USDT",
      timestamp: "2026-07-18T13:50:00+09:00",
      signal: "HOLD",
      confidence: 69,
      analysis_json: {
        view: "중립",
        confidence: 69,
        key_facts: ["3,420과 3,470 사이 박스 구간입니다."],
        inferences: ["방향 트리거 전에는 관찰이 적절합니다."],
        counter_scenario: ["거래량을 동반한 박스 이탈 시 새 시나리오가 필요합니다."],
        trade: { entry: null, stop: null, target: null, leverage: 1 },
        invalidation: "현재 실행 가능한 계획이 없습니다."
      },
      risk_guard: { verdict: "SKIPPED", warnings: [], hard_blocks: [] }
    }
  ];

  const state = {
    market: {},
    macro: {},
    analysis: null,
    candidates: [],
    activeSymbol: "",
    currentTf: "1h",
    sources: { analysis: "loading", history: "loading", macro: "loading", symbol: "loading" },
    streamState: { market: "loading", account: "unavailable" },
    selectedId: null,
    setupFilter: "all",
    marketStream: null
  };

  const elements = {
    demoBanner: document.querySelector("#decision-demo-banner"),
    screenHeading: document.querySelector("#screen-heading"),
    screens: Array.from(document.querySelectorAll("[data-screen]")),
    routeButtons: Array.from(document.querySelectorAll("[data-route]")),
    connectionState: document.querySelector("#connection-state"),
    marketPair: document.querySelector("#market-pair"),
    marketSymbol: document.querySelector("#market-symbol"),
    marketPrice: document.querySelector("#market-price"),
    marketUpdated: document.querySelector("#market-updated"),
    marketState: document.querySelector("#market-state"),
    marketTimeframe: document.querySelector("#market-timeframe"),
    marketLiveStatus: document.querySelector("#market-live-status"),
    marketActionCard: document.querySelector("#market-screen .action-card"),
    marketAction: document.querySelector("#market-action"),
    marketActionTitle: document.querySelector("#market-action-title"),
    marketActionCopy: document.querySelector("#market-action-copy"),
    bestPair: document.querySelector("#best-pair"),
    bestDirection: document.querySelector("#best-direction"),
    bestGrade: document.querySelector("#best-grade"),
    bestQuality: document.querySelector("#best-quality"),
    bestRisk: document.querySelector("#best-risk"),
    marketSupporting: document.querySelector("#market-supporting"),
    marketCounter: document.querySelector("#market-counter"),
    marketEntry: document.querySelector("#market-entry"),
    marketStop: document.querySelector("#market-stop"),
    marketTarget: document.querySelector("#market-target"),
    marketLeverage: document.querySelector("#market-leverage"),
    openBestDetail: document.querySelector("#open-best-detail"),
    macroGrid: document.querySelector("#macro-grid"),
    marketSetupSummary: document.querySelector("#market-setup-summary"),
    analysisPair: document.querySelector("#analysis-pair"),
    analysisView: document.querySelector("#analysis-view"),
    analysisGrade: document.querySelector("#analysis-grade"),
    analysisQuality: document.querySelector("#analysis-quality"),
    analysisRisk: document.querySelector("#analysis-risk"),
    analysisAction: document.querySelector("#analysis-action"),
    analysisActionCopy: document.querySelector("#analysis-action-copy"),
    analysisSupporting: document.querySelector("#analysis-supporting"),
    analysisInterpretation: document.querySelector("#analysis-interpretation"),
    analysisCounter: document.querySelector("#analysis-counter"),
    analysisDirection: document.querySelector("#analysis-direction"),
    analysisEntry: document.querySelector("#analysis-entry"),
    analysisStop: document.querySelector("#analysis-stop"),
    analysisTarget: document.querySelector("#analysis-target"),
    analysisRr: document.querySelector("#analysis-rr"),
    analysisLeverage: document.querySelector("#analysis-leverage"),
    analysisInvalidation: document.querySelector("#analysis-invalidation"),
    setupsSummary: document.querySelector("#setups-summary"),
    setupFilters: Array.from(document.querySelectorAll("[data-setup-filter]")),
    setupsList: document.querySelector("#setups-list"),
    setupTemplate: document.querySelector("#setup-card-template"),
    detailBack: document.querySelector("#detail-back"),
    detailPair: document.querySelector("#detail-pair"),
    detailDirection: document.querySelector("#detail-direction"),
    detailGrade: document.querySelector("#detail-grade"),
    detailQuality: document.querySelector("#detail-quality"),
    detailRisk: document.querySelector("#detail-risk"),
    detailActionCard: document.querySelector("#detail-screen .detail-action"),
    detailAction: document.querySelector("#detail-action"),
    detailActionTitle: document.querySelector("#detail-action-title"),
    detailActionCopy: document.querySelector("#detail-action-copy"),
    detailSupporting: document.querySelector("#detail-supporting"),
    detailInterpretation: document.querySelector("#detail-interpretation"),
    detailCounter: document.querySelector("#detail-counter"),
    detailEntry: document.querySelector("#detail-entry"),
    detailStop: document.querySelector("#detail-stop"),
    detailTarget: document.querySelector("#detail-target"),
    detailRr: document.querySelector("#detail-rr"),
    detailLeverage: document.querySelector("#detail-leverage"),
    detailRiskPercent: document.querySelector("#detail-risk-percent"),
    detailInvalidation: document.querySelector("#detail-invalidation")
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
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

  function formatNumber(value, digits) {
    if (!Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: digits === undefined ? (Math.abs(value) >= 1000 ? 2 : 3) : digits
    }).format(value);
  }

  function formatPrice(value) {
    return Number.isFinite(value) ? formatNumber(value) : "—";
  }

  function formatPercent(value) {
    if (!Number.isFinite(value)) return "—";
    const prefix = value > 0 ? "+" : "";
    return prefix + formatNumber(value, 2) + "%";
  }

  function formatLeverage(value) {
    return Number.isFinite(value) ? formatNumber(value, 1) + "x" : "—";
  }

  function approvedReadPath(path) {
    const url = new URL(path, window.location.origin);
    const exact = url.pathname + url.search;
    if (url.origin !== window.location.origin || !ALLOWED_READS.has(exact) || url.hash) {
      throw new Error("허용되지 않은 읽기 경로");
    }
    return exact;
  }

  function approvedStreamPath(path) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !ALLOWED_STREAMS.has(url.pathname) || url.search || url.hash) {
      throw new Error("허용되지 않은 스트림 경로");
    }
    return url.pathname;
  }

  async function requestJson(path) {
    const exact = approvedReadPath(path);
    const response = await window.fetch(exact, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error("읽기 요청 실패");
    return await response.json();
  }

  function analysisFromStatus(payloadValue) {
    const payload = asObject(payloadValue);
    const result = asObject(payload.result);
    const latest = asObject(payload.latest_result);
    if (Object.keys(result).length) return result;
    if (Object.keys(latest).length) return latest;
    return null;
  }

  function deduplicateRawCandidates(rawCandidates) {
    const seen = new Set();
    const result = [];
    rawCandidates.forEach((raw, index) => {
      const object = asObject(raw);
      const signature = [object.id || object.scenario_id || "", object.symbol || object.pair_label || "", object.timestamp || object.created_at || "", index].join("|");
      const stable = [object.symbol || object.pair_label || "", object.timestamp || object.created_at || "", JSON.stringify(asObject(object.analysis_json).trade || {})].join("|");
      if (seen.has(stable)) return;
      seen.add(stable);
      result.push({ ...object, id: object.id || object.scenario_id || signature });
    });
    return result;
  }

  function buildCurrentViewModel() {
    return architecture.buildViewModel({
      market: state.market,
      macro: state.macro,
      analysis: state.analysis,
      candidates: state.candidates,
      activeSymbol: state.activeSymbol,
      currentTf: state.currentTf,
      sources: state.sources,
      streamState: state.streamState,
      demo: demoMode
    });
  }

  function scenarioPriority(scenario) {
    const blocked = scenario.risk.verdict === "BLOCK" || scenario.risk.verdict === "INVALID";
    const executable = scenario.risk.verdict === "PASS" ? 2 : scenario.risk.verdict === "CAUTION" ? 1 : 0;
    return (blocked ? -1000 : 0) + executable * 200 + (Number.isFinite(scenario.quality) ? scenario.quality : 0);
  }

  function sortedSetups(viewModel) {
    return asArray(viewModel.public.setups).slice().sort((a, b) => scenarioPriority(b) - scenarioPriority(a));
  }

  function bestScenario(viewModel) {
    return sortedSetups(viewModel)[0] || viewModel.public.analysis || null;
  }

  function actionFor(scenario) {
    const noTradeSentence = "조건이 명확하지 않은 시장에서는 거래하지 않는 것도 하나의 결정입니다";
    if (!scenario) {
      return { state: ACTION_STATES.WAIT, title: "데이터를 확인하세요", copy: noTradeSentence + "." };
    }
    const verdict = scenario.risk.verdict;
    if (verdict === "BLOCK" || verdict === "INVALID") {
      return { state: ACTION_STATES.INVALIDATED, title: "실행하지 마세요", copy: "리스크 차단 조건이 있습니다. 시나리오 품질과 무관하게 관찰만 유지합니다." };
    }
    if (scenario.direction.key === "neutral" || scenario.direction.key === "unknown") {
      return { state: ACTION_STATES.WATCH, title: "방향이 생길 때까지 관찰하세요", copy: noTradeSentence + "." };
    }
    if (verdict === "CAUTION" && (scenario.grade === "A" || scenario.grade === "B+")) {
      return { state: ACTION_STATES.PREPARE, title: "준비하되 노출은 보수적으로", copy: scenario.actions.conservative || "주의 조건이 줄어드는지 확인하고 더 작은 크기를 검토합니다." };
    }
    if (verdict === "PASS" && (scenario.grade === "A" || scenario.grade === "B+") && Number.isFinite(scenario.plan.entry) && Number.isFinite(scenario.plan.stop)) {
      return { state: ACTION_STATES.READY, title: "진입 조건을 실제값으로 재확인하세요", copy: scenario.actions.conservative || "가격 트리거, 구조, 거래량이 계획과 일치하는지 확인합니다." };
    }
    if (verdict === "PASS") {
      return { state: ACTION_STATES.WATCH, title: "구조적 명확도가 더 높아질 때까지 관찰", copy: noTradeSentence + "." };
    }
    return { state: ACTION_STATES.WAIT, title: "실행 계획이 완성될 때까지 기다리세요", copy: noTradeSentence + "." };
  }

  function marketStateLabel(scenario) {
    if (!scenario) return "분석 대기";
    if (scenario.direction.key === "long") return "상방 구조 우위";
    if (scenario.direction.key === "short") return "하방 구조 우위";
    if (scenario.direction.key === "neutral") return "중립·관찰";
    return "구조 미확인";
  }

  function replaceList(container, items, fallback) {
    container.replaceChildren();
    const source = items.length ? items : [fallback];
    source.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = String(item || fallback);
      container.append(li);
    });
  }

  function setBadge(element, prefix, value) {
    element.textContent = prefix + " " + (value === null || value === undefined || value === "" ? "—" : String(value));
  }

  function renderConnection(viewModel) {
    const stream = viewModel.public.context.marketStream;
    const sourceStates = Object.values(viewModel.public.context.sources || {});
    let label = "데이터 확인 중";
    let tone = "neutral";
    if (viewModel.mode === "demo") {
      label = "샘플 모드";
      tone = "ready";
    } else if (stream === "ready") {
      label = "실시간 연결";
      tone = "ready";
    } else if (stream === "stale") {
      label = "업데이트 지연";
      tone = "stale";
    } else if (sourceStates.includes("endpoint_failure")) {
      label = "일부 데이터 실패";
      tone = "error";
    }
    elements.connectionState.textContent = label;
    elements.connectionState.dataset.tone = tone;
  }

  function renderMacro(viewModel) {
    elements.macroGrid.replaceChildren();
    const macro = viewModel.public.macro;
    const rows = [
      ["SPX", macro.tradMarkets.SPX],
      ["NDX", macro.tradMarkets.NDX],
      ["VIX", macro.tradMarkets.VIX],
      ["GOLD", macro.tradMarkets.GOLD]
    ];
    rows.forEach(([ticker, data]) => {
      const row = document.createElement("div");
      row.className = "context-row";
      const label = document.createElement("span");
      label.textContent = ticker;
      const value = document.createElement("strong");
      const price = formatPrice(data.price);
      const change = formatPercent(data.changePercent);
      value.textContent = price + (change === "—" ? "" : " · " + change);
      row.append(label, value);
      elements.macroGrid.append(row);
    });
    const ibitRow = document.createElement("div");
    ibitRow.className = "context-row";
    const ibitLabel = document.createElement("span");
    ibitLabel.textContent = "IBIT";
    const ibitValue = document.createElement("strong");
    ibitValue.textContent = formatPrice(macro.ibit.value) + (Number.isFinite(macro.ibit.change24h) ? " · " + formatPercent(macro.ibit.change24h) : "");
    ibitRow.append(ibitLabel, ibitValue);
    elements.macroGrid.append(ibitRow);
  }

  function renderMarketSetupSummary(viewModel) {
    elements.marketSetupSummary.replaceChildren();
    const setups = sortedSetups(viewModel).slice(0, 4);
    if (!setups.length) {
      const empty = document.createElement("p");
      empty.className = "muted small";
      empty.textContent = "표시할 후보가 없습니다.";
      elements.marketSetupSummary.append(empty);
      return;
    }
    setups.forEach((scenario) => {
      const action = actionFor(scenario);
      const row = document.createElement("div");
      row.className = "mini-setup";
      const copy = document.createElement("span");
      const pair = document.createElement("strong");
      pair.textContent = scenario.pair || scenario.symbol || "자산 미확인";
      const grade = document.createElement("small");
      grade.textContent = "Grade " + scenario.grade + " · " + scenario.risk.verdict;
      copy.append(pair, grade);
      const stateLabel = document.createElement("b");
      stateLabel.textContent = action.state;
      row.append(copy, stateLabel);
      elements.marketSetupSummary.append(row);
    });
  }

  function renderMarket(viewModel) {
    const market = viewModel.public.market;
    const best = bestScenario(viewModel);
    const action = actionFor(best);
    elements.marketPair.textContent = market.pair || best?.pair || "자산 확인 중";
    elements.marketSymbol.textContent = market.symbol || viewModel.public.context.activeSymbol || "심볼 미확인";
    elements.marketPrice.textContent = formatPrice(market.price);
    elements.marketUpdated.textContent = market.updatedAt || "업데이트 시각 미확인";
    elements.marketState.textContent = marketStateLabel(best);
    elements.marketTimeframe.textContent = viewModel.public.context.currentTimeframe || "1h";
    elements.marketLiveStatus.textContent = viewModel.mode === "demo" ? "샘플" : viewModel.public.context.marketStream;
    elements.marketAction.textContent = action.state;
    elements.marketActionTitle.textContent = action.title;
    elements.marketActionCopy.textContent = action.copy;
    elements.marketActionCard.dataset.action = action.state;

    if (!best) {
      elements.bestPair.textContent = "유효한 시나리오 대기";
      elements.bestDirection.textContent = "방향 미확인";
      setBadge(elements.bestGrade, "Grade", "—");
      setBadge(elements.bestQuality, "Scenario Quality", "—");
      setBadge(elements.bestRisk, "Risk guard", "—");
      replaceList(elements.marketSupporting, [], "분석 근거를 기다리고 있습니다.");
      replaceList(elements.marketCounter, [], "반대 시나리오를 기다리고 있습니다.");
      elements.marketEntry.textContent = "—";
      elements.marketStop.textContent = "—";
      elements.marketTarget.textContent = "—";
      elements.marketLeverage.textContent = "—";
      elements.openBestDetail.disabled = true;
    } else {
      elements.bestPair.textContent = best.pair || best.symbol || "자산 미확인";
      elements.bestDirection.textContent = best.direction.label;
      setBadge(elements.bestGrade, "Grade", best.grade);
      setBadge(elements.bestQuality, "Scenario Quality", Number.isFinite(best.quality) ? formatNumber(best.quality, 0) : "—");
      setBadge(elements.bestRisk, "Risk guard", best.risk.verdict);
      replaceList(elements.marketSupporting, best.evidence.supporting, "확인된 근거가 없습니다.");
      replaceList(elements.marketCounter, best.evidence.counter, "반대 시나리오 데이터가 없습니다.");
      elements.marketEntry.textContent = formatPrice(best.plan.entry);
      elements.marketStop.textContent = formatPrice(best.plan.stop);
      elements.marketTarget.textContent = formatPrice(best.plan.target);
      elements.marketLeverage.textContent = formatLeverage(best.plan.leverage);
      elements.openBestDetail.disabled = false;
      elements.openBestDetail.dataset.scenarioId = best.id;
    }
    renderMacro(viewModel);
    renderMarketSetupSummary(viewModel);
  }

  function renderAnalysis(viewModel) {
    const scenario = viewModel.public.analysis || bestScenario(viewModel);
    const action = actionFor(scenario);
    if (!scenario) {
      elements.analysisPair.textContent = "—";
      elements.analysisView.textContent = "분석 대기";
      setBadge(elements.analysisGrade, "Grade", "—");
      setBadge(elements.analysisQuality, "Scenario Quality", "—");
      setBadge(elements.analysisRisk, "Risk guard", "—");
      elements.analysisAction.textContent = action.state;
      elements.analysisActionCopy.textContent = action.copy;
      replaceList(elements.analysisSupporting, [], "확인된 사실이 없습니다.");
      replaceList(elements.analysisInterpretation, [], "해석 데이터가 없습니다.");
      replaceList(elements.analysisCounter, [], "반대 시나리오 데이터가 없습니다.");
      return;
    }
    elements.analysisPair.textContent = scenario.pair || scenario.symbol || "자산 미확인";
    elements.analysisView.textContent = scenario.direction.label;
    setBadge(elements.analysisGrade, "Grade", scenario.grade);
    setBadge(elements.analysisQuality, "Scenario Quality", Number.isFinite(scenario.quality) ? formatNumber(scenario.quality, 0) : "—");
    setBadge(elements.analysisRisk, "Risk guard", scenario.risk.verdict);
    elements.analysisAction.textContent = action.state;
    elements.analysisActionCopy.textContent = action.copy;
    replaceList(elements.analysisSupporting, scenario.evidence.supporting, "확인된 사실이 없습니다.");
    replaceList(elements.analysisInterpretation, scenario.evidence.interpretation, "해석 데이터가 없습니다.");
    replaceList(elements.analysisCounter, scenario.evidence.counter, "반대 시나리오 데이터가 없습니다.");
    elements.analysisDirection.textContent = scenario.direction.label;
    elements.analysisEntry.textContent = formatPrice(scenario.plan.entry);
    elements.analysisStop.textContent = formatPrice(scenario.plan.stop);
    elements.analysisTarget.textContent = formatPrice(scenario.plan.target);
    elements.analysisRr.textContent = Number.isFinite(scenario.plan.riskReward) ? formatNumber(scenario.plan.riskReward, 2) : "—";
    elements.analysisLeverage.textContent = formatLeverage(scenario.plan.leverage);
    elements.analysisInvalidation.textContent = scenario.plan.invalidation || "무효화 조건 미확인";
  }

  function setupMatchesFilter(scenario) {
    if (state.setupFilter === "all") return true;
    if (state.setupFilter === "watch") return !["A", "B+"].includes(scenario.grade) || ["SKIPPED", "UNAVAILABLE"].includes(scenario.risk.verdict);
    return scenario.grade === state.setupFilter;
  }

  function renderSetups(viewModel) {
    const all = sortedSetups(viewModel);
    const filtered = all.filter(setupMatchesFilter);
    elements.setupsSummary.textContent = "전체 " + all.length + "개 · 현재 필터 " + filtered.length + "개";
    elements.setupsList.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement("article");
      empty.className = "panel";
      const title = document.createElement("strong");
      title.textContent = "조건에 맞는 후보가 없습니다.";
      const copy = document.createElement("p");
      copy.className = "muted";
      copy.textContent = "후보가 없다는 것도 유효한 결과입니다. 필터를 바꾸거나 다음 분석을 기다리세요.";
      empty.append(title, copy);
      elements.setupsList.append(empty);
      return;
    }
    filtered.forEach((scenario) => {
      const fragment = elements.setupTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".setup-card");
      const button = fragment.querySelector(".setup-open");
      const action = actionFor(scenario);
      fragment.querySelector(".setup-pair").textContent = scenario.pair || scenario.symbol || "자산 미확인";
      fragment.querySelector(".setup-direction").textContent = scenario.direction.label;
      fragment.querySelector(".setup-action").textContent = action.state;
      fragment.querySelector(".setup-grade").textContent = "Grade " + scenario.grade;
      fragment.querySelector(".setup-quality").textContent = "Scenario Quality " + (Number.isFinite(scenario.quality) ? formatNumber(scenario.quality, 0) : "—");
      fragment.querySelector(".setup-risk").textContent = "Risk guard " + scenario.risk.verdict;
      fragment.querySelector('[data-field="entry"]').textContent = formatPrice(scenario.plan.entry);
      fragment.querySelector('[data-field="stop"]').textContent = formatPrice(scenario.plan.stop);
      fragment.querySelector('[data-field="target"]').textContent = formatPrice(scenario.plan.target);
      button.addEventListener("click", () => openDetail(scenario.id));
      card.dataset.action = action.state;
      elements.setupsList.append(fragment);
    });
  }

  function renderDetail(viewModel) {
    const scenario = sortedSetups(viewModel).find((item) => item.id === state.selectedId) || null;
    if (!scenario) return;
    const action = actionFor(scenario);
    elements.detailPair.textContent = scenario.pair || scenario.symbol || "자산 미확인";
    elements.detailDirection.textContent = scenario.direction.label;
    setBadge(elements.detailGrade, "Grade", scenario.grade);
    setBadge(elements.detailQuality, "Scenario Quality", Number.isFinite(scenario.quality) ? formatNumber(scenario.quality, 0) : "—");
    setBadge(elements.detailRisk, "Risk guard", scenario.risk.verdict);
    elements.detailAction.textContent = action.state;
    elements.detailActionTitle.textContent = action.title;
    elements.detailActionCopy.textContent = action.copy;
    elements.detailActionCard.dataset.action = action.state;
    replaceList(elements.detailSupporting, scenario.evidence.supporting, "확인된 근거가 없습니다.");
    replaceList(elements.detailInterpretation, scenario.evidence.interpretation, "해석 데이터가 없습니다.");
    replaceList(elements.detailCounter, scenario.evidence.counter, "반대 시나리오 데이터가 없습니다.");
    elements.detailEntry.textContent = formatPrice(scenario.plan.entry);
    elements.detailStop.textContent = formatPrice(scenario.plan.stop);
    elements.detailTarget.textContent = formatPrice(scenario.plan.target);
    elements.detailRr.textContent = Number.isFinite(scenario.plan.riskReward) ? formatNumber(scenario.plan.riskReward, 2) : "—";
    elements.detailLeverage.textContent = formatLeverage(scenario.plan.leverage);
    elements.detailRiskPercent.textContent = Number.isFinite(scenario.plan.riskPercent) ? formatPercent(scenario.plan.riskPercent) : "—";
    elements.detailInvalidation.textContent = scenario.plan.invalidation || "무효화 조건 미확인";
  }

  function renderAll() {
    const viewModel = buildCurrentViewModel();
    renderConnection(viewModel);
    renderMarket(viewModel);
    renderAnalysis(viewModel);
    renderSetups(viewModel);
    if (state.selectedId) renderDetail(viewModel);
    renderRoute();
  }

  function routeFromHash() {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash.startsWith("detail/")) return { screen: "detail", id: decodeURIComponent(hash.slice(7)) };
    if (["market", "analysis", "setups"].includes(hash)) return { screen: hash, id: null };
    return { screen: "market", id: null };
  }

  function renderRoute() {
    const route = routeFromHash();
    if (route.id) state.selectedId = route.id;
    elements.screens.forEach((screen) => {
      screen.hidden = screen.dataset.screen !== route.screen;
    });
    elements.routeButtons.forEach((button) => {
      const active = button.dataset.route === route.screen || (route.screen === "detail" && button.dataset.route === "setups");
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    elements.screenHeading.textContent = route.screen === "detail" ? "Scenario detail" : route.screen.charAt(0).toUpperCase() + route.screen.slice(1);
    if (route.screen === "detail") renderDetail(buildCurrentViewModel());
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigate(route) {
    const next = "#" + route;
    if (window.location.hash === next) renderRoute();
    else window.location.hash = next;
  }

  function openDetail(id) {
    state.selectedId = id;
    navigate("detail/" + encodeURIComponent(id));
  }

  function setMarketStreamState(value) {
    state.streamState.market = value;
    renderConnection(buildCurrentViewModel());
  }

  function mergeMarket(data) {
    const update = asObject(data);
    state.market = { ...state.market, ...update };
    if (update.symbol) state.activeSymbol = String(update.symbol);
    setMarketStreamState("online");
    renderAll();
  }

  function connectMarketStream() {
    approvedStreamPath("/api/market-stream");
    const source = new EventSource("/api/market-stream");
    state.marketStream = source;
    source.addEventListener("open", () => setMarketStreamState("online"));
    source.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      const message = asObject(payload);
      if (["snapshot", "market", "price"].includes(String(message.type || ""))) mergeMarket(asObject(message.data));
    });
    source.addEventListener("error", () => setMarketStreamState("stale"));
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
    const rawCandidates = [];
    if (results[0].status === "fulfilled") {
      const latest = analysisFromStatus(results[0].value);
      if (latest) {
        state.analysis = latest;
        rawCandidates.push(latest);
      }
    }
    if (results[1].status === "fulfilled") {
      const entries = asArray(asObject(results[1].value).entries);
      rawCandidates.push.apply(rawCandidates, entries);
    }
    if (results[2].status === "fulfilled") state.macro = asObject(results[2].value);
    if (results[3].status === "fulfilled") {
      const symbol = asObject(results[3].value);
      state.activeSymbol = String(symbol.symbol || "");
      if (!state.market.symbol && symbol.symbol) state.market = { ...state.market, symbol: symbol.symbol, pair_label: symbol.pair_label };
    }
    state.candidates = deduplicateRawCandidates(rawCandidates);
    renderAll();
  }

  function loadDemoData() {
    elements.demoBanner.hidden = false;
    state.market = DEMO_MARKET;
    state.macro = DEMO_MACRO;
    state.analysis = DEMO_ANALYSES[0];
    state.candidates = DEMO_ANALYSES;
    state.activeSymbol = "BTCUSDT";
    state.sources = { analysis: "ready", history: "ready", macro: "ready", symbol: "ready" };
    state.streamState.market = "demo";
    renderAll();
  }

  function bindEvents() {
    elements.routeButtons.forEach((button) => {
      if (button.disabled) return;
      button.addEventListener("click", () => navigate(button.dataset.route));
    });
    elements.setupFilters.forEach((button) => {
      button.addEventListener("click", () => {
        state.setupFilter = button.dataset.setupFilter;
        elements.setupFilters.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        renderSetups(buildCurrentViewModel());
      });
    });
    elements.openBestDetail.addEventListener("click", () => {
      const id = elements.openBestDetail.dataset.scenarioId;
      if (id) openDetail(id);
    });
    elements.detailBack.addEventListener("click", () => navigate("setups"));
    window.addEventListener("hashchange", renderRoute);
    window.addEventListener("beforeunload", () => {
      if (state.marketStream) state.marketStream.close();
    });
  }

  function initialize() {
    if (!window.location.hash) window.history.replaceState(null, "", window.location.pathname + window.location.search + "#market");
    bindEvents();
    renderAll();
    if (demoMode) {
      loadDemoData();
      return;
    }
    connectMarketStream();
    loadLiveReads();
  }

  window.addEventListener("DOMContentLoaded", initialize);
})();
