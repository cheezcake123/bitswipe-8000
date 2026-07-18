(() => {
  "use strict";

  const architecture = window.MobilePreviewArchitecture;
  if (!architecture || typeof architecture.buildViewModel !== "function") {
    throw new Error("Decision Layers view model contract is unavailable.");
  }

  const ALLOWED_READS = new Set([
    "/api/analyze?include_latest=true",
    "/api/analysis-history?limit=50"
  ]);
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

  const DEMO_ANALYSES = [
    {
      symbol: "BTCUSDT",
      pair_label: "BTC/USDT",
      timestamp: "2026-07-16T11:08:00+09:00",
      signal: "BUY",
      confidence: 82,
      analysis_json: {
        view: "상방 우위",
        confidence: 82,
        key_facts: ["1시간 종가가 주요 이동평균 위에 있습니다.", "상방 트리거는 64,800입니다."],
        inferences: ["구조가 유지되면 눌림 이후 상방 시나리오가 우세합니다."],
        counter_scenario: ["64,200 이탈 시 상방 구조가 무효화됩니다."],
        trade: { entry: 64800, stop: 64200, target: 66900, leverage: 2 },
        invalidation: "64,200 아래에서 1시간 종가 마감"
      },
      risk_guard: { verdict: "PASS", risk_reward_ratio: 3.5, warnings: [], hard_blocks: [] }
    },
    {
      symbol: "ETHUSDT",
      pair_label: "ETH/USDT",
      timestamp: "2026-07-16T10:42:00+09:00",
      signal: "HOLD",
      confidence: 69,
      analysis_json: {
        view: "중립",
        confidence: 69,
        key_facts: ["박스 구간 내부에 있습니다."],
        inferences: ["방향 트리거 전에는 관찰이 적절합니다."],
        counter_scenario: ["거래량을 동반한 박스 이탈 시 새 시나리오가 필요합니다."],
        trade: { entry: null, stop: null, target: null, leverage: 1 },
        invalidation: "현재는 실행 가능한 계획이 없습니다."
      },
      risk_guard: { verdict: "SKIPPED", warnings: [], hard_blocks: [] }
    },
    {
      symbol: "SOLUSDT",
      pair_label: "SOL/USDT",
      timestamp: "2026-07-16T09:58:00+09:00",
      signal: "SELL",
      confidence: 76,
      analysis_json: {
        view: "하방 우위",
        confidence: 76,
        key_facts: ["148 하방 트리거가 계획에 포함되어 있습니다."],
        inferences: ["지지 이탈 확인 전에는 관찰이 우선입니다."],
        counter_scenario: ["152 회복 시 하방 관점이 약해집니다."],
        trade: { entry: 147.8, stop: 152.1, target: 139.2, leverage: 1 },
        invalidation: "152.1 위에서 시나리오 무효"
      },
      risk_guard: { verdict: "CAUTION", risk_reward_ratio: 2, warnings: ["변동성 확대 구간입니다."], hard_blocks: [] }
    }
  ];

  const state = {
    analysis: null,
    candidates: [],
    sourceState: "loading",
    filter: "all"
  };

  const elements = {
    demoBanner: document.getElementById("demo-banner"),
    sourceState: document.getElementById("source-state"),
    metricTotal: document.getElementById("metric-total"),
    metricQuality: document.getElementById("metric-quality"),
    metricWait: document.getElementById("metric-wait"),
    reflectionWait: document.getElementById("reflection-wait"),
    reflectionRisk: document.getElementById("reflection-risk"),
    reflectionInvalidation: document.getElementById("reflection-invalidation"),
    recordsState: document.getElementById("records-state"),
    journalList: document.getElementById("journal-list"),
    template: document.getElementById("journal-item-template"),
    filters: Array.from(document.querySelectorAll("[data-filter]"))
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
      if (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value.trim())) {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
      }
    }
    return null;
  }

  function analysisFromStatus(payloadValue) {
    const payload = asObject(payloadValue);
    const result = asObject(payload.result);
    const latest = asObject(payload.latest_result);
    if (Object.keys(result).length) return result;
    if (Object.keys(latest).length) return latest;
    return null;
  }

  function scenarioAction(scenario) {
    const risk = asObject(scenario.risk);
    const plan = asObject(scenario.plan);
    const hardBlocks = asArray(risk.hardBlocks);
    const verdict = cleanText(risk.verdict, "UNAVAILABLE").toUpperCase();
    const quality = finiteNumber(scenario.quality);
    const hasPlan = [plan.entry, plan.stop, plan.target].every((value) => Number.isFinite(value));
    if (hardBlocks.length || verdict === "BLOCK") return "INVALIDATED";
    if (quality !== null && quality >= 85 && verdict === "PASS" && hasPlan) return "READY";
    if (quality !== null && quality >= 75 && hasPlan) return "PREPARE";
    if (quality !== null && quality >= 65) return "WATCH";
    return "WAIT";
  }

  function buildJournalModel() {
    return architecture.buildViewModel({
      analysis: state.analysis,
      candidates: state.candidates,
      market: {},
      macro: {},
      account: {},
      sources: {
        analysis: state.sourceState,
        history: state.sourceState
      },
      streamState: {},
      demo: demoMode
    });
  }

  function journalRecords() {
    const model = buildJournalModel();
    return asArray(asObject(model.public).setups).map((scenario) => ({
      scenario,
      action: scenarioAction(scenario)
    }));
  }

  function filteredRecords(records) {
    if (state.filter === "quality") return records.filter((item) => ["A", "B+"].includes(item.scenario.grade));
    if (state.filter === "wait") return records.filter((item) => ["WAIT", "WATCH"].includes(item.action));
    if (state.filter === "actionable") return records.filter((item) => ["PREPARE", "READY"].includes(item.action));
    return records;
  }

  function setSourceState(value) {
    state.sourceState = value;
    const labels = {
      loading: "불러오는 중",
      ready: "기록 준비됨",
      error: "일부 기록 실패",
      demo: "샘플 모드"
    };
    elements.sourceState.dataset.state = value;
    elements.sourceState.textContent = labels[value] || "상태 확인 중";
  }

  function renderSummary(records) {
    const qualityCount = records.filter((item) => ["A", "B+"].includes(item.scenario.grade)).length;
    const waitCount = records.filter((item) => ["WAIT", "WATCH"].includes(item.action)).length;
    const invalidationCount = records.filter((item) => cleanText(asObject(item.scenario.plan).invalidation, "")).length;
    elements.metricTotal.textContent = String(records.length);
    elements.metricQuality.textContent = String(qualityCount);
    elements.metricWait.textContent = String(waitCount);
    elements.reflectionWait.textContent = records.length
      ? `${records.length}개 기록 중 ${waitCount}개는 실행보다 기다림이 우선인 상태였습니다.`
      : "판단 기록이 아직 없습니다.";
    elements.reflectionRisk.textContent = qualityCount
      ? `A·B+ 기록이 ${qualityCount}개 있어도 Risk Guard 상태는 별도로 확인해야 합니다.`
      : "높은 품질 등급 기록이 아직 없습니다. 품질과 위험은 항상 별개로 확인합니다.";
    elements.reflectionInvalidation.textContent = records.length
      ? `${records.length}개 중 ${invalidationCount}개 기록에 무효화 조건이 확인됩니다.`
      : "무효화 조건을 확인할 기록이 없습니다.";
  }

  function formatQuality(value) {
    if (!Number.isFinite(value)) return "—";
    return `${Math.round(value)} / 100`;
  }

  function firstEvidence(scenario) {
    const evidence = asObject(scenario.evidence);
    return cleanText(asArray(evidence.supporting)[0], "확인 가능한 근거 없음");
  }

  function reviewQuestion(action) {
    if (action === "READY") return "준비된 시나리오에서도 계획 밖의 추격 진입을 피할 수 있었는가?";
    if (action === "PREPARE") return "트리거 확인 전까지 실제로 기다릴 수 있었는가?";
    if (action === "WATCH") return "관찰 상태를 억지로 실행 신호로 바꾸지 않았는가?";
    if (action === "INVALIDATED") return "무효화된 관점을 붙잡지 않고 폐기할 수 있었는가?";
    return "아무것도 하지 않는 선택을 지킬 수 있었는가?";
  }

  function renderRecords(records) {
    const visible = filteredRecords(records);
    elements.journalList.replaceChildren();
    if (!records.length) {
      elements.recordsState.hidden = false;
      elements.recordsState.firstElementChild.textContent = "표시할 분석 기록이 없습니다.";
      elements.recordsState.lastElementChild.textContent = "새 분석이 저장되면 이곳에서 판단 과정을 복기합니다.";
      return;
    }
    if (!visible.length) {
      elements.recordsState.hidden = false;
      elements.recordsState.firstElementChild.textContent = "현재 필터에 맞는 기록이 없습니다.";
      elements.recordsState.lastElementChild.textContent = "다른 필터를 선택해 전체 판단 기록을 확인하세요.";
      return;
    }
    elements.recordsState.hidden = true;
    const fragment = document.createDocumentFragment();
    visible.forEach((item) => {
      const scenario = item.scenario;
      const card = elements.template.content.firstElementChild.cloneNode(true);
      card.querySelector(".journal-pair").textContent = cleanText(scenario.pair || scenario.symbol, "자산 미확인");
      card.querySelector(".journal-time").textContent = cleanText(scenario.timestamp, "시각 미확인");
      card.querySelector(".journal-grade").textContent = cleanText(scenario.grade, "—");
      const risk = cleanText(asObject(scenario.risk).verdict, "UNAVAILABLE");
      const riskBadge = card.querySelector(".journal-risk");
      riskBadge.textContent = risk;
      riskBadge.dataset.tone = risk.toUpperCase() === "BLOCK" ? "block" : "normal";
      card.querySelector(".journal-action").textContent = item.action;
      card.querySelector(".journal-direction").textContent = cleanText(asObject(scenario.direction).label, "방향 미확인");
      card.querySelector(".journal-quality").textContent = formatQuality(scenario.quality);
      card.querySelector(".journal-evidence").textContent = firstEvidence(scenario);
      card.querySelector(".journal-invalidation").textContent = cleanText(asObject(scenario.plan).invalidation, "명시된 조건 없음");
      card.querySelector(".journal-question").textContent = reviewQuestion(item.action);
      fragment.appendChild(card);
    });
    elements.journalList.appendChild(fragment);
  }

  function renderAll() {
    const records = journalRecords();
    renderSummary(records);
    renderRecords(records);
  }

  async function requestJson(path) {
    if (!ALLOWED_READS.has(path)) throw new Error("승인되지 않은 읽기 경로입니다.");
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) throw new Error("same-origin API 읽기만 허용됩니다.");
    const response = await window.fetch(url.pathname + url.search, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error("읽기 요청 실패");
    return response.json();
  }

  async function loadLiveReads() {
    const results = await Promise.allSettled(Array.from(ALLOWED_READS).map((path) => requestJson(path)));
    const analyzeResult = results[0];
    const historyResult = results[1];
    state.analysis = analyzeResult.status === "fulfilled" ? analysisFromStatus(analyzeResult.value) : null;
    state.candidates = historyResult.status === "fulfilled" ? asArray(asObject(historyResult.value).entries) : [];
    if (state.analysis) state.candidates = [state.analysis, ...state.candidates];
    setSourceState(results.every((item) => item.status === "fulfilled") ? "ready" : "error");
    renderAll();
  }

  function loadDemoData() {
    elements.demoBanner.hidden = false;
    state.analysis = DEMO_ANALYSES[0];
    state.candidates = DEMO_ANALYSES;
    setSourceState("demo");
    renderAll();
  }

  function bindEvents() {
    elements.filters.forEach((button) => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "all";
        elements.filters.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        renderRecords(journalRecords());
      });
    });
  }

  function initialize() {
    bindEvents();
    renderAll();
    if (demoMode) {
      loadDemoData();
      return;
    }
    loadLiveReads();
  }

  initialize();
})();
