(() => {
  "use strict";

  const STORAGE_PREFIX = "bitswipe_mobile_preview_v2";
  const PRIVACY_KEY = STORAGE_PREFIX + ":privacy-hidden";
  const REQUEST_TIMEOUT_MS = 8000;
  const ANALYSIS_STALE_MS = 6 * 60 * 60 * 1000;
  const STREAM_STALE_MS = 45 * 1000;
  const ALLOWED_STREAMS = new Set(["/api/market-stream", "/api/account-stream"]);
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

  const DEMO_MARKET = {
    symbol: "BTCUSDT",
    pair_label: "BTC/USDT",
    price: 65042.6,
    last_update: "11:17:51",
    indicators: { rsi: 57.4, macd_hist: 38.2, vol_ratio: 118.0, close: 65018.4 },
    charts: {}
  };

  function demoChart(base, slope) {
    const labels = [];
    const candles = [];
    const ema9 = [];
    const sma50 = [];
    const sma200 = [];
    const volume = [];
    const volumeMa = [];
    let close = base;
    for (let index = 0; index < 72; index += 1) {
      const wave = Math.sin(index / 4.2) * base * 0.0018;
      const drift = slope * index;
      const open = close;
      close = base + drift + wave + Math.sin(index * 1.7) * base * 0.00045;
      const high = Math.max(open, close) + base * (0.0007 + (index % 4) * 0.00008);
      const low = Math.min(open, close) - base * (0.00065 + (index % 3) * 0.00009);
      labels.push("7/16 " + String(5 + Math.floor(index / 12)).padStart(2, "0") + ":" + String((index % 12) * 5).padStart(2, "0"));
      candles.push([open, close, low, high]);
      ema9.push(base + slope * Math.max(0, index - 3) + Math.sin((index - 2) / 5) * base * 0.0012);
      sma50.push(base - base * 0.004 + slope * Math.max(0, index - 16));
      sma200.push(base - base * 0.010 + slope * Math.max(0, index - 30));
      const vol = 780 + Math.abs(Math.sin(index / 3)) * 520 + (index > 63 ? 260 : 0);
      volume.push(vol);
      volumeMa.push(980);
    }
    return {
      labels: labels,
      candles: candles,
      ema_9: ema9,
      sma_50: sma50,
      sma_200: sma200,
      volume: volume,
      volume_ma: volumeMa,
      rsi: labels.map((_, index) => 49 + Math.sin(index / 6) * 8 + index * 0.06),
      macd_hist: labels.map((_, index) => Math.sin(index / 7) * 22 + index * 0.55)
    };
  }

  DEMO_MARKET.charts["5m"] = demoChart(64280, 10.4);
  DEMO_MARKET.charts["15m"] = demoChart(63800, 17.1);
  DEMO_MARKET.charts["1h"] = demoChart(61100, 55.2);
  DEMO_MARKET.charts["4h"] = demoChart(57400, 106.8);

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
        regime: "상승 추세",
        confidence_breakdown: {
          price_structure: 26,
          momentum: 16,
          derivatives: 15,
          macro: 12,
          account_risk_fit: 14,
          data_quality_penalty: -1,
          counter_scenario_penalty: 0
        },
        key_facts: ["1시간 종가가 EMA 9·SMA 50·SMA 200 위에 있습니다.", "상방 트리거는 64,800입니다."],
        inferences: ["구조가 유지되면 눌림 이후 상승 시나리오가 우세합니다."],
        counter_scenario: ["64,200 이탈 시 상방 구조가 무효화됩니다."],
        levels: { resistance: 66900, support: 64200, bull_trigger: 64800, bear_trigger: 64200 },
        trade: { entry: 64800, stop: 64200, target: 66900, leverage: 2 },
        actions: { aggressive: "트리거 돌파 후 재확인", conservative: "진입 구간 지지와 거래량을 함께 확인" },
        invalidation: "64,200 아래에서 1시간 종가 마감",
        summary: "상승 구조는 분명하지만 진입 트리거와 손절 기준을 먼저 확인합니다."
      },
      trade_levels: { entry: 64800, stop: 64200, target: 66900 },
      risk_guard: {
        valid: true,
        verdict: "PASS",
        leverage: 2,
        risk_percent: 1,
        stop_distance: 600,
        stop_distance_percent: 0.93,
        leveraged_loss_percent_on_margin: 1.86,
        risk_reward_ratio: 3.5,
        max_position_percent_by_account_risk: 53.76,
        warnings: [],
        hard_blocks: []
      }
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
        regime: "박스",
        confidence_breakdown: {
          price_structure: 20,
          momentum: 11,
          derivatives: 14,
          macro: 11,
          account_risk_fit: 14,
          data_quality_penalty: -1,
          counter_scenario_penalty: 0
        },
        key_facts: ["3,420과 3,470 사이 박스 구간입니다."],
        inferences: ["방향 트리거 전에는 관찰이 적절합니다."],
        counter_scenario: ["거래량을 동반한 박스 이탈 시 새 시나리오가 필요합니다."],
        levels: { resistance: 3470, support: 3420, bull_trigger: 3470, bear_trigger: 3420 },
        trade: { entry: null, stop: null, target: null, leverage: 1 },
        actions: { aggressive: "진입하지 않음", conservative: "박스 이탈을 기다림" },
        invalidation: "현재는 실행 가능한 계획이 없습니다.",
        summary: "방향성이 부족해 관찰 후보로 유지합니다."
      },
      risk_guard: { valid: false, verdict: "SKIPPED", warnings: [], hard_blocks: [] }
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
        regime: "변동성 확장",
        confidence_breakdown: {
          price_structure: 26,
          momentum: 16,
          derivatives: 15,
          macro: 10,
          account_risk_fit: 13,
          data_quality_penalty: -1,
          counter_scenario_penalty: -3
        },
        key_facts: ["148 하방 트리거가 계획에 포함되어 있습니다."],
        inferences: ["지지 이탈이 확인되면 하방 변동성이 커질 수 있습니다."],
        counter_scenario: ["152 회복 시 하방 관점이 약해집니다."],
        levels: { resistance: 152, support: 148, bull_trigger: 152, bear_trigger: 148 },
        trade: { entry: 147.8, stop: 152.1, target: 139.2, leverage: 1 },
        actions: { aggressive: "148 이탈 확인", conservative: "리테스트 실패 확인" },
        invalidation: "152.1 위에서 시나리오 무효",
        summary: "하방 트리거 확인 전에는 관찰합니다."
      },
      trade_levels: { entry: 147.8, stop: 152.1, target: 139.2 },
      risk_guard: {
        valid: true,
        verdict: "CAUTION",
        leverage: 1,
        risk_percent: 1,
        stop_distance: 4.3,
        stop_distance_percent: 2.91,
        leveraged_loss_percent_on_margin: 2.91,
        risk_reward_ratio: 2,
        max_position_percent_by_account_risk: 34.36,
        warnings: ["변동성 확대 구간입니다."],
        hard_blocks: []
      }
    }
  ];

  const DEMO_ACCOUNT = {
    updated_at: "11:17:51",
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

  const DEMO_MACRO = {
    trad_markets: {
      SPX: { price: 6314.18, chg_pct: 0.42 },
      NDX: { price: 22784.7, chg_pct: 0.61 },
      VIX: { price: 16.42, chg_pct: -2.18 },
      GOLD: { price: 3371.5, chg_pct: 0.17 }
    },
    IBIT_PX: { value: 61.42, change_24h: 0.73 }
  };

  const state = {
    market: {},
    analysis: null,
    candidates: [],
    account: {},
    macro: {},
    activeSymbol: "",
    currentTf: "1h",
    selectedId: null,
    detailOrigin: "candidates",
    candidateFilter: "all",
    privacyHidden: readPrivacyPreference(),
    sources: { analysis: "loading", history: "loading", macro: "loading", symbol: "loading" },
    streamState: { market: "loading", account: "loading" },
    lastReceipt: { market: 0, account: 0 },
    streams: { market: null, account: null },
    reconnectTimers: { market: null, account: null },
    chartObserver: null
  };

  const elements = {
    screens: Array.from(document.querySelectorAll("[data-screen]")),
    navButtons: Array.from(document.querySelectorAll(".bottom-nav [data-route]")),
    demoBanner: document.querySelector("#demo-banner"),
    marketConnection: document.querySelector("#market-connection"),
    marketConnectionLabel: document.querySelector("#market-connection-label"),
    marketPair: document.querySelector("#market-pair"),
    marketSymbol: document.querySelector("#market-symbol"),
    marketPrice: document.querySelector("#market-price"),
    marketUpdated: document.querySelector("#market-updated"),
    timeframeButtons: Array.from(document.querySelectorAll("[data-timeframe]")),
    chartWrap: document.querySelector("#chart-wrap"),
    canvas: document.querySelector("#market-chart"),
    chartEmpty: document.querySelector("#chart-empty"),
    heroGrade: document.querySelector("#hero-grade"),
    heroScore: document.querySelector("#hero-score"),
    heroGradeBadge: document.querySelector("#hero-grade-badge"),
    heroRiskBadge: document.querySelector("#hero-risk-badge"),
    heroFreshness: document.querySelector("#hero-freshness"),
    heroView: document.querySelector("#hero-view"),
    heroTitle: document.querySelector("#hero-title"),
    heroRegime: document.querySelector("#hero-regime"),
    primaryActionTitle: document.querySelector("#primary-action-title"),
    primaryActionCopy: document.querySelector("#primary-action-copy"),
    criteriaList: document.querySelector("#criteria-list"),
    planDirection: document.querySelector("#plan-direction"),
    planEntry: document.querySelector("#plan-entry"),
    planStop: document.querySelector("#plan-stop"),
    planTarget: document.querySelector("#plan-target"),
    planRr: document.querySelector("#plan-rr"),
    planRecommendedLeverage: document.querySelector("#plan-recommended-leverage"),
    planRisk: document.querySelector("#plan-risk"),
    planStopDistance: document.querySelector("#plan-stop-distance"),
    planAllocation: document.querySelector("#plan-allocation"),
    planInvalidation: document.querySelector("#plan-invalidation"),
    factList: document.querySelector("#fact-list"),
    inferenceList: document.querySelector("#inference-list"),
    breakdownList: document.querySelector("#breakdown-list"),
    breakdownCheck: document.querySelector("#breakdown-check"),
    candidateSummary: document.querySelector("#candidate-summary"),
    candidateFilter: document.querySelector("#candidate-filter"),
    btcHistoryNote: document.querySelector("#btc-history-note"),
    tradfiGrid: document.querySelector("#tradfi-grid"),
    candidateState: document.querySelector("#candidate-state"),
    candidateList: document.querySelector("#candidate-list"),
    candidateTemplate: document.querySelector("#candidate-template"),
    privacyToggle: document.querySelector("#privacy-toggle"),
    privacyIconUse: document.querySelector("#privacy-icon-use"),
    accountConnection: document.querySelector("#account-connection"),
    accountConnectionLabel: document.querySelector("#account-connection-label"),
    accountUpdated: document.querySelector("#account-updated"),
    accountEquity: document.querySelector("#account-equity"),
    accountWallet: document.querySelector("#account-wallet"),
    accountAvailable: document.querySelector("#account-available"),
    accountPnl: document.querySelector("#account-pnl"),
    accountPositionCount: document.querySelector("#account-position-count"),
    accountNotional: document.querySelector("#account-notional"),
    accountEffectiveLeverage: document.querySelector("#account-effective-leverage"),
    accountConfiguredLeverage: document.querySelector("#account-configured-leverage"),
    accountLeverageDisplay: document.querySelector("#account-leverage-display"),
    positionsCount: document.querySelector("#positions-count"),
    positionsState: document.querySelector("#positions-state"),
    positionsList: document.querySelector("#positions-list"),
    positionTemplate: document.querySelector("#position-template"),
    detailBack: document.querySelector("#detail-back"),
    detailPair: document.querySelector("#detail-pair"),
    detailSetup: document.querySelector("#detail-setup"),
    detailGrade: document.querySelector("#detail-grade"),
    detailRisk: document.querySelector("#detail-risk"),
    detailUpdated: document.querySelector("#detail-updated"),
    detailActionTitle: document.querySelector("#detail-action-title"),
    detailActionCopy: document.querySelector("#detail-action-copy"),
    detailPlanGrid: document.querySelector("#detail-plan-grid"),
    detailCriteriaList: document.querySelector("#detail-criteria-list"),
    detailFacts: document.querySelector("#detail-facts"),
    detailInferences: document.querySelector("#detail-inferences"),
    detailBreakdownList: document.querySelector("#detail-breakdown-list"),
    detailBreakdownCheck: document.querySelector("#detail-breakdown-check"),
    favoriteButton: document.querySelector("#favorite-button"),
    localMemo: document.querySelector("#local-memo"),
    memoStatus: document.querySelector("#memo-status")
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function cleanText(value, fallback, maxLength) {
    const safeFallback = fallback === undefined ? "" : fallback;
    const safeMax = maxLength === undefined ? 240 : maxLength;
    if (typeof value !== "string" && typeof value !== "number") return safeFallback;
    const text = String(value).replace(/\s+/g, " ").trim();
    return text ? text.slice(0, safeMax) : safeFallback;
  }

  function firstText(values, fallback) {
    for (const value of values) {
      const text = cleanText(value, "");
      if (text) return text;
    }
    return fallback === undefined ? "" : fallback;
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

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function formatNumber(value, digits) {
    if (!Number.isFinite(value)) return "—";
    const maximumFractionDigits = digits === undefined
      ? (Math.abs(value) >= 1000 ? 2 : Math.abs(value) >= 1 ? 3 : 6)
      : digits;
    return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: maximumFractionDigits }).format(value);
  }

  function formatPrice(value) {
    return Number.isFinite(value) ? formatNumber(value) : "—";
  }

  function formatMoney(value, signed) {
    if (!Number.isFinite(value)) return "—";
    const prefix = signed && value > 0 ? "+" : "";
    return prefix + "$" + formatNumber(value, 2);
  }

  function formatPercent(value, signed) {
    if (!Number.isFinite(value)) return "—";
    const prefix = signed && value > 0 ? "+" : "";
    return prefix + formatNumber(value, 2) + "%";
  }

  function formatLeverage(value) {
    return Number.isFinite(value) ? formatNumber(value, 1) + "x" : "—";
  }

  function safeIdentifier(value) {
    return cleanText(value, "scenario", 120).replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  function normalizedSymbol(raw) {
    const object = asObject(raw);
    const direct = cleanText(object.symbol, "", 30).toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (direct) return direct;
    return cleanText(object.pair_label, "", 30).toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function normalizedPair(raw) {
    const object = asObject(raw);
    const explicit = cleanText(object.pair_label, "", 30).toUpperCase();
    if (/^[A-Z0-9]{2,15}\/[A-Z0-9]{2,10}$/.test(explicit)) return explicit;
    const symbol = normalizedSymbol(object);
    const quote = ["USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD"].find((item) => symbol.endsWith(item));
    if (quote && symbol.length > quote.length) return symbol.slice(0, -quote.length) + "/" + quote;
    return symbol || "자산 미확인";
  }

  function tokenForPair(pair) {
    const token = pair.split("/")[0].replace(/[^A-Z0-9]/g, "");
    return token.slice(0, 4) || "—";
  }

  function scoreGrade(score) {
    if (!Number.isFinite(score)) return "—";
    if (score >= 85) return "A";
    if (score >= 75) return "B+";
    if (score >= 65) return "B";
    if (score >= 50) return "C";
    return "D";
  }

  function directionMeta(signal, view) {
    const combined = firstText([view, signal], "").toLowerCase();
    if (/(상방|매수|롱|buy|long|strong_buy)/i.test(combined)) return { key: "long", label: "상방 우위" };
    if (/(하방|매도|숏|sell|short|strong_sell)/i.test(combined)) return { key: "short", label: "하방 우위" };
    if (/(중립|관망|대기|hold|wait|neutral|no.?trade)/i.test(combined)) return { key: "neutral", label: "중립·관찰" };
    return { key: "unknown", label: "방향 미확인" };
  }

  function riskMeta(verdict) {
    const exact = cleanText(verdict, "UNAVAILABLE", 20).toUpperCase();
    if (exact === "PASS") return { verdict: exact, label: "리스크 PASS", tone: "supporting" };
    if (exact === "CAUTION") return { verdict: exact, label: "리스크 CAUTION", tone: "caution" };
    if (exact === "BLOCK" || exact === "INVALID") return { verdict: exact, label: "리스크 " + exact, tone: "block" };
    if (exact === "SKIPPED") return { verdict: exact, label: "리스크 SKIPPED", tone: "neutral" };
    return { verdict: "UNAVAILABLE", label: "리스크 확인 불가", tone: "neutral" };
  }

  function parseTimestamp(value) {
    const raw = cleanText(value, "", 64);
    let parsed = raw ? Date.parse(raw) : NaN;
    if (!Number.isFinite(parsed) && /^\d{2}:\d{2}:\d{2}$/.test(raw)) {
      const parts = raw.split(":").map(Number);
      const today = new Date();
      today.setHours(parts[0], parts[1], parts[2], 0);
      if (today.getTime() - Date.now() > 60 * 60 * 1000) today.setDate(today.getDate() - 1);
      parsed = today.getTime();
    }
    if (!Number.isFinite(parsed)) return { raw: raw, value: null, label: raw || "시각 미확인", stale: false, available: false };
    const date = new Date(parsed);
    const label = new Intl.DateTimeFormat("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    }).format(date);
    return { raw: raw, value: parsed, label: label, stale: !demoMode && Date.now() - parsed > ANALYSIS_STALE_MS, available: true };
  }

  function normalizeCandidate(rawValue, index) {
    const raw = asObject(rawValue);
    const analysisJson = asObject(raw.analysis_json);
    const trade = asObject(analysisJson.trade);
    const levels = asObject(analysisJson.levels);
    const tradeLevels = asObject(raw.trade_levels);
    const riskGuard = asObject(raw.risk_guard);
    const tradingSignal = asObject(raw.trading_signal);
    const reportSections = asObject(raw.report_sections);
    const scoreValue = finiteNumber(analysisJson.confidence, raw.confidence, tradingSignal.confidence);
    const score = scoreValue !== null ? clamp(scoreValue, 0, 100) : null;
    const direction = directionMeta(
      firstText([raw.signal, tradingSignal.signal_en, tradingSignal.signal_kr], ""),
      cleanText(analysisJson.view, "")
    );
    const entry = finiteNumber(trade.entry, tradeLevels.entry, riskGuard.entry_price);
    const stop = finiteNumber(trade.stop, tradeLevels.stop, riskGuard.stop_price);
    const target = finiteNumber(trade.target, tradeLevels.target, riskGuard.target_price);
    const rr = finiteNumber(riskGuard.risk_reward_ratio, trade.risk_reward_ratio, tradeLevels.risk_reward_ratio);
    const leverage = finiteNumber(trade.leverage, raw.claude_leverage, riskGuard.leverage);
    const riskPercent = finiteNumber(riskGuard.risk_percent, trade.risk_percent);
    const timestamp = parseTimestamp(firstText([raw.timestamp, raw.completed_at, raw.analysis_time, raw.updated_at], ""));
    const pair = normalizedPair(raw);
    const symbol = normalizedSymbol(raw);
    const risk = riskMeta(riskGuard.verdict);
    const seed = [symbol || pair, timestamp.raw, entry, stop, target].join("|");
    return {
      id: safeIdentifier(seed || String(index)),
      raw: raw,
      analysisJson: analysisJson,
      riskGuard: riskGuard,
      pair: pair,
      symbol: symbol,
      token: tokenForPair(pair),
      score: score,
      grade: scoreGrade(score),
      direction: direction,
      regime: firstText([analysisJson.regime, reportSections.regime, raw.regime], "시장 국면 미확인"),
      summary: firstText([analysisJson.summary, reportSections.summary, raw.summary], "분석 요약이 없습니다."),
      entry: entry,
      stop: stop,
      target: target,
      rr: rr,
      leverage: leverage,
      riskPercent: riskPercent,
      risk: risk,
      timestamp: timestamp,
      levels: levels,
      breakdown: asObject(analysisJson.confidence_breakdown),
      facts: stringList(analysisJson.key_facts),
      inferences: stringList(analysisJson.inferences),
      counterScenario: stringList(analysisJson.counter_scenario),
      dataQualityNotes: stringList(analysisJson.data_quality_notes),
      actions: asObject(analysisJson.actions),
      invalidation: firstText([analysisJson.invalidation, trade.invalidation], "무효화 조건 데이터 없음")
    };
  }

  function stringList(value) {
    return asArray(value).map((item) => cleanText(item, "", 220)).filter(Boolean);
  }

  function lastFinite(values) {
    if (!Array.isArray(values)) return null;
    for (let index = values.length - 1; index >= 0; index -= 1) {
      const value = finiteNumber(values[index]);
      if (value !== null) return value;
    }
    return null;
  }

  function currentCandidate() {
    if (!state.candidates.length) return null;
    const active = state.activeSymbol || normalizedSymbol(state.market);
    return state.candidates.find((candidate) => active && candidate.symbol === active) || state.candidates[0];
  }

  function marketApplies(candidate) {
    const marketSymbol = normalizedSymbol(state.market);
    return Boolean(candidate && marketSymbol && candidate.symbol === marketSymbol);
  }

  function latestIndicators(candidate) {
    if (!marketApplies(candidate)) return {};
    const chart = asObject(asObject(state.market.charts)[state.currentTf]);
    const indicators = asObject(state.market.indicators);
    const candle = asArray(chart.candles).length ? asArray(chart.candles)[asArray(chart.candles).length - 1] : [];
    return {
      close: finiteNumber(Array.isArray(candle) ? candle[1] : null, indicators.close, state.market.price),
      ema9: lastFinite(chart.ema_9),
      sma50: lastFinite(chart.sma_50),
      sma200: lastFinite(chart.sma_200),
      rsi: finiteNumber(lastFinite(chart.rsi), indicators.rsi),
      macdHist: finiteNumber(lastFinite(chart.macd_hist), indicators.macd_hist),
      volume: lastFinite(chart.volume),
      volumeMa: lastFinite(chart.volume_ma),
      volRatio: finiteNumber(indicators.vol_ratio)
    };
  }

  function buildCriteria(candidate) {
    const unavailable = (name, copy) => ({ name: name, status: "unavailable", value: "데이터 없음", copy: copy });
    if (!candidate) {
      return [
        unavailable("분석 신선도", "분석 시각을 확인할 수 없습니다."),
        unavailable("가격 트리거", "계획 데이터가 없습니다."),
        unavailable("추세 구조", "차트 데이터가 없습니다."),
        unavailable("모멘텀", "RSI·MACD 데이터가 없습니다."),
        unavailable("거래량 참여", "거래량 데이터가 없습니다."),
        unavailable("리스크 가드", "risk guard 결과가 없습니다."),
        unavailable("계좌 리스크 적합도", "계좌 적합도 점수가 없습니다.")
      ];
    }

    const criteria = [];
    if (!candidate.timestamp.available) {
      criteria.push(unavailable("분석 신선도", "분석 생성 시각이 없어 최신 여부를 판정할 수 없습니다."));
    } else if (candidate.timestamp.stale) {
      criteria.push({ name: "분석 신선도", status: "caution", value: candidate.timestamp.label + " · 오래된 분석", copy: "새 분석을 요청해 현재 구조를 다시 확인해야 합니다." });
    } else {
      criteria.push({ name: "분석 신선도", status: "supporting", value: candidate.timestamp.label + " · 최신 범위", copy: "6시간 이내에 생성된 저장 분석입니다." });
    }

    const livePrice = marketApplies(candidate) ? finiteNumber(state.market.price) : null;
    const trigger = candidate.direction.key === "long"
      ? finiteNumber(candidate.levels.bull_trigger)
      : candidate.direction.key === "short"
        ? finiteNumber(candidate.levels.bear_trigger)
        : null;
    if (livePrice !== null && trigger !== null) {
      const passed = candidate.direction.key === "long" ? livePrice >= trigger : livePrice <= trigger;
      const distance = trigger !== 0 ? Math.abs(livePrice - trigger) / Math.abs(trigger) * 100 : null;
      criteria.push({
        name: "가격 트리거",
        status: passed ? "supporting" : "neutral",
        value: "현재 " + formatPrice(livePrice) + " / 트리거 " + formatPrice(trigger),
        copy: passed ? "계획 방향의 가격 트리거를 통과했습니다." : "트리거까지 " + formatPercent(distance, false) + " 남아 있어 관찰합니다."
      });
    } else if (livePrice !== null && candidate.entry !== null) {
      const distance = candidate.entry !== 0 ? Math.abs(livePrice - candidate.entry) / Math.abs(candidate.entry) * 100 : null;
      criteria.push({
        name: "가격 트리거",
        status: "neutral",
        value: "현재 " + formatPrice(livePrice) + " / 계획 진입 " + formatPrice(candidate.entry),
        copy: "명시 트리거가 없어 계획 진입가까지 실제 거리 " + formatPercent(distance, false) + "를 표시합니다."
      });
    } else {
      criteria.push(unavailable("가격 트리거", "라이브 가격·트리거 또는 계획 진입가가 함께 필요합니다."));
    }

    const indicators = latestIndicators(candidate);
    const trendValues = [indicators.close, indicators.ema9, indicators.sma50, indicators.sma200];
    if (trendValues.every(Number.isFinite)) {
      const longOrdered = indicators.close > indicators.ema9 && indicators.ema9 > indicators.sma50 && indicators.sma50 > indicators.sma200;
      const shortOrdered = indicators.close < indicators.ema9 && indicators.ema9 < indicators.sma50 && indicators.sma50 < indicators.sma200;
      const aligned = candidate.direction.key === "long" ? longOrdered : candidate.direction.key === "short" ? shortOrdered : false;
      const opposite = candidate.direction.key === "long" ? shortOrdered : candidate.direction.key === "short" ? longOrdered : false;
      criteria.push({
        name: "추세 구조",
        status: aligned ? "supporting" : opposite ? "caution" : "neutral",
        value: "종가 " + formatPrice(indicators.close) + " · EMA9 " + formatPrice(indicators.ema9) + " · SMA50 " + formatPrice(indicators.sma50) + " · SMA200 " + formatPrice(indicators.sma200),
        copy: aligned ? "네 값의 실제 배열이 계획 방향과 일치합니다." : opposite ? "네 값의 실제 배열이 계획 방향과 반대입니다." : "이동평균 배열이 혼재해 단일 추세로 단정하지 않습니다."
      });
    } else {
      criteria.push(unavailable("추세 구조", "종가·EMA 9·SMA 50·SMA 200이 모두 있어야 배열을 판정합니다."));
    }

    if (Number.isFinite(indicators.rsi) && Number.isFinite(indicators.macdHist)) {
      const longSupport = indicators.rsi >= 50 && indicators.macdHist > 0;
      const shortSupport = indicators.rsi <= 50 && indicators.macdHist < 0;
      const aligned = candidate.direction.key === "long" ? longSupport : candidate.direction.key === "short" ? shortSupport : false;
      const opposite = candidate.direction.key === "long" ? shortSupport : candidate.direction.key === "short" ? longSupport : false;
      criteria.push({
        name: "모멘텀",
        status: aligned ? "supporting" : opposite ? "caution" : "neutral",
        value: "RSI " + formatNumber(indicators.rsi, 1) + " · MACD 히스토그램 " + formatNumber(indicators.macdHist, 2),
        copy: aligned ? "RSI와 MACD 히스토그램이 함께 계획 방향을 지지합니다." : opposite ? "두 모멘텀 값이 계획 방향과 반대입니다." : "두 지표가 혼재해 중립 근거로 다룹니다."
      });
    } else {
      criteria.push(unavailable("모멘텀", "RSI와 MACD 히스토그램의 최신값이 모두 필요합니다."));
    }

    let ratio = null;
    if (Number.isFinite(indicators.volume) && Number.isFinite(indicators.volumeMa) && indicators.volumeMa > 0) ratio = indicators.volume / indicators.volumeMa;
    else if (Number.isFinite(indicators.volRatio)) ratio = indicators.volRatio / 100;
    if (Number.isFinite(ratio)) {
      const status = ratio >= 1 ? "supporting" : ratio < 0.7 ? "caution" : "neutral";
      criteria.push({
        name: "거래량 참여",
        status: status,
        value: "최근 / 평균 " + formatNumber(ratio, 2) + "x",
        copy: status === "supporting" ? "최근 거래량이 이동평균 이상입니다." : status === "caution" ? "참여가 낮아 확인 강도가 약합니다. 단독 하드 블록으로 사용하지 않습니다." : "거래량이 평균 부근이며 중립 근거로 다룹니다."
      });
    } else {
      criteria.push(unavailable("거래량 참여", "최근 거래량과 거래량 이동평균을 확인할 수 없습니다."));
    }

    const risk = candidate.risk;
    const warnings = stringList(candidate.riskGuard.warnings);
    const hardBlocks = stringList(candidate.riskGuard.hard_blocks);
    if (risk.verdict === "PASS") {
      criteria.push({ name: "리스크 가드", status: "supporting", value: "PASS", copy: "기계적 손절 방향과 손익비 검증을 통과했습니다." });
    } else if (risk.verdict === "CAUTION") {
      criteria.push({ name: "리스크 가드", status: "caution", value: "CAUTION", copy: warnings[0] || "주의 조건이 있어 더 작은 크기와 낮은 레버리지를 검토합니다." });
    } else if (risk.verdict === "BLOCK" || risk.verdict === "INVALID") {
      criteria.push({ name: "리스크 가드", status: "block", value: risk.verdict, copy: hardBlocks[0] || warnings[0] || "실행을 막는 기계적 위험 조건이 있습니다." });
    } else if (risk.verdict === "SKIPPED") {
      criteria.push({ name: "리스크 가드", status: "unavailable", value: "SKIPPED", copy: "계획값이 부족해 risk guard가 실행되지 않았습니다." });
    } else {
      criteria.push(unavailable("리스크 가드", "저장 기록에 risk guard 결과가 없습니다."));
    }

    const fit = finiteNumber(candidate.breakdown.account_risk_fit);
    const exposureCount = finiteNumber(state.account.open_position_count);
    const exposure = exposureCount !== null ? " 현재 오픈 포지션 " + formatNumber(exposureCount, 0) + "개입니다." : "";
    if (fit === null) {
      criteria.push(unavailable("계좌 리스크 적합도", "confidence_breakdown.account_risk_fit 값이 없습니다." + exposure));
    } else if (fit >= 12) {
      criteria.push({ name: "계좌 리스크 적합도", status: "supporting", value: formatNumber(fit, 0) + " / 15", copy: "AI 원점수 기준 계좌 여력이 양호합니다." + exposure });
    } else if (fit >= 7) {
      criteria.push({ name: "계좌 리스크 적합도", status: "caution", value: formatNumber(fit, 0) + " / 15", copy: "계좌 적합도가 제한적이므로 노출을 보수적으로 검토합니다." + exposure });
    } else {
      criteria.push({ name: "계좌 리스크 적합도", status: "block", value: formatNumber(fit, 0) + " / 15", copy: "계좌 적합도가 제약 구간입니다. 방향 근거와 별도로 실행을 제한합니다." + exposure });
    }
    return criteria;
  }

  function primaryAction(candidate) {
    if (!candidate || !["long", "short"].includes(candidate.direction.key) || candidate.entry === null || candidate.stop === null) {
      return { title: "데이터가 충분하지 않습니다", copy: "방향·진입·손절 계획이 모두 확인될 때까지 실행하지 말고 관찰하세요." };
    }
    if (candidate.risk.verdict === "BLOCK" || candidate.risk.verdict === "INVALID") {
      return { title: "관찰만 유지하세요", copy: "리스크 가드가 " + candidate.risk.verdict + " 상태입니다. 높은 등급이어도 실행하지 않습니다." };
    }
    if (candidate.timestamp.stale) {
      return { title: "새 분석을 요청해 확인하세요", copy: "저장 분석이 오래되었습니다. 현재 구조와 계획을 다시 검증해야 합니다." };
    }
    if ((candidate.grade === "A" || candidate.grade === "B+") && candidate.risk.verdict === "PASS") {
      return { title: "진입 조건을 검토하세요", copy: firstText([candidate.actions.conservative], "가격 트리거·추세·거래량 조건을 실제값으로 확인하세요.") };
    }
    if ((candidate.grade === "A" || candidate.grade === "B+") && candidate.risk.verdict === "CAUTION") {
      return { title: "더 작은 크기와 낮은 레버리지를 검토하세요", copy: firstText([candidate.actions.conservative], "주의 조건이 사라지기 전에는 노출을 보수적으로 제한하세요.") };
    }
    return { title: "관찰을 계속하세요", copy: "구조적 명확도가 더 높아지고 위험 조건이 확인될 때까지 기다립니다." };
  }

  function setStatusBadge(element, label, tone) {
    element.textContent = label;
    element.dataset.tone = tone || "neutral";
  }

  function criterionLabel(status) {
    return {
      supporting: "지지",
      neutral: "중립",
      caution: "주의",
      block: "차단",
      unavailable: "확인 불가"
    }[status] || "확인 불가";
  }

  function renderCriteria(container, criteria) {
    container.replaceChildren();
    criteria.forEach((criterion) => {
      const row = document.createElement("article");
      row.className = "criterion-row";
      row.dataset.status = criterion.status;
      const heading = document.createElement("div");
      heading.className = "criterion-heading";
      const title = document.createElement("strong");
      title.textContent = criterion.name;
      const status = document.createElement("span");
      status.className = "criterion-state";
      status.textContent = criterionLabel(criterion.status);
      heading.append(title, status);
      const value = document.createElement("p");
      value.className = "criterion-value";
      value.textContent = criterion.value;
      const copy = document.createElement("p");
      copy.className = "criterion-copy";
      copy.textContent = criterion.copy;
      row.append(heading, value, copy);
      container.append(row);
    });
  }

  const BREAKDOWN_DIMENSIONS = [
    { key: "price_structure", label: "가격 구조", max: 30, penalty: false },
    { key: "momentum", label: "모멘텀", max: 20, penalty: false },
    { key: "derivatives", label: "파생시장", max: 20, penalty: false },
    { key: "macro", label: "매크로", max: 15, penalty: false },
    { key: "account_risk_fit", label: "계좌 리스크 적합도", max: 15, penalty: false },
    { key: "data_quality_penalty", label: "데이터 품질 감점", max: 15, penalty: true },
    { key: "counter_scenario_penalty", label: "반대 시나리오 감점", max: 10, penalty: true }
  ];

  function breakdownState(candidate) {
    if (!candidate) return { rows: [], check: "점수 데이터 없음", consistent: null };
    let complete = true;
    let sum = 0;
    const rows = BREAKDOWN_DIMENSIONS.map((dimension) => {
      const value = finiteNumber(candidate.breakdown[dimension.key]);
      if (value === null) complete = false;
      else sum += value;
      return { dimension: dimension, value: value };
    });
    if (!complete || candidate.score === null) return { rows: rows, check: "점수 내부값 확인 필요", consistent: false };
    const bounded = clamp(sum, 1, 100);
    const consistent = Math.round(bounded) === Math.round(candidate.score);
    return {
      rows: rows,
      check: consistent ? "합계 " + formatNumber(sum, 0) + " · 일치" : "점수 내부값 확인 필요",
      consistent: consistent
    };
  }

  function renderBreakdown(container, checkElement, candidate) {
    const result = breakdownState(candidate);
    container.replaceChildren();
    if (!result.rows.length) {
      const empty = document.createElement("p");
      empty.className = "muted-copy";
      empty.textContent = "confidence_breakdown 데이터가 없습니다.";
      container.append(empty);
    } else {
      result.rows.forEach((rowData) => {
        const row = document.createElement("div");
        row.className = "breakdown-row";
        row.dataset.penalty = String(rowData.dimension.penalty);
        const label = document.createElement("span");
        label.textContent = rowData.dimension.label;
        const track = document.createElement("div");
        track.className = "breakdown-track";
        const fill = document.createElement("div");
        fill.className = "breakdown-fill";
        const magnitude = rowData.value === null ? 0 : Math.abs(rowData.value);
        fill.style.width = clamp(magnitude / rowData.dimension.max * 100, 0, 100) + "%";
        track.append(fill);
        const value = document.createElement("strong");
        value.textContent = rowData.value === null ? "—" : (rowData.value > 0 && rowData.dimension.penalty ? "+" : "") + formatNumber(rowData.value, 0) + " / " + (rowData.dimension.penalty ? "−" : "") + rowData.dimension.max;
        row.append(label, track, value);
        container.append(row);
      });
    }
    checkElement.textContent = result.check;
    checkElement.dataset.tone = result.consistent === false ? "caution" : "neutral";
  }

  function appendListItems(container, items, emptyCopy) {
    container.replaceChildren();
    const source = items.length ? items : [emptyCopy];
    source.forEach((copy) => {
      const item = document.createElement("li");
      item.textContent = cleanText(copy, "데이터 없음", 260);
      container.append(item);
    });
  }

  function evidenceFor(candidate) {
    if (!candidate) return { facts: [], inferences: [] };
    const facts = candidate.facts.slice();
    const inferences = candidate.inferences.slice();
    const indicators = latestIndicators(candidate);
    if ([indicators.close, indicators.ema9, indicators.sma50, indicators.sma200].every(Number.isFinite)) {
      facts.push("현재 " + state.currentTf + " 종가 " + formatPrice(indicators.close) + ", EMA 9 " + formatPrice(indicators.ema9) + ", SMA 50 " + formatPrice(indicators.sma50) + ", SMA 200 " + formatPrice(indicators.sma200) + "입니다.");
    }
    if (Number.isFinite(indicators.rsi)) facts.push("현재 " + state.currentTf + " RSI는 " + formatNumber(indicators.rsi, 1) + "입니다.");
    if (Number.isFinite(indicators.macdHist)) facts.push("현재 " + state.currentTf + " MACD 히스토그램은 " + formatNumber(indicators.macdHist, 2) + "입니다.");
    let volumeRatio = null;
    if (Number.isFinite(indicators.volume) && Number.isFinite(indicators.volumeMa) && indicators.volumeMa > 0) volumeRatio = indicators.volume / indicators.volumeMa;
    else if (Number.isFinite(indicators.volRatio)) volumeRatio = indicators.volRatio / 100;
    if (Number.isFinite(volumeRatio)) facts.push("최근 거래량은 이동평균의 " + formatNumber(volumeRatio, 2) + "배입니다.");
    const livePrice = marketApplies(candidate) ? finiteNumber(state.market.price) : null;
    const trigger = candidate.direction.key === "long" ? finiteNumber(candidate.levels.bull_trigger) : candidate.direction.key === "short" ? finiteNumber(candidate.levels.bear_trigger) : null;
    if (livePrice !== null && trigger !== null) facts.push("현재가는 " + formatPrice(livePrice) + ", 계획 방향 트리거는 " + formatPrice(trigger) + "입니다.");
    const trad = asObject(state.macro.trad_markets);
    const vix = finiteNumber(asObject(trad.VIX).price);
    if (vix !== null) facts.push("전통시장 컨텍스트의 VIX는 " + formatNumber(vix, 2) + "입니다. 이는 거래 후보가 아닌 배경 정보입니다.");
    if (candidate.summary) inferences.unshift(candidate.summary);
    candidate.counterScenario.forEach((item) => inferences.push("반대 시나리오: " + item));
    if (candidate.invalidation && candidate.invalidation !== "무효화 조건 데이터 없음") inferences.push("무효화: " + candidate.invalidation);
    candidate.dataQualityNotes.forEach((item) => inferences.push("데이터 품질: " + item));
    return { facts: facts.slice(0, 9), inferences: inferences.slice(0, 9) };
  }

  function planMetrics(candidate) {
    if (!candidate) {
      return [
        ["진입", "—"], ["손절", "—"], ["목표", "—"], ["손익비", "—"],
        ["권장 레버리지", "—"], ["거래당 리스크", "—"], ["손절 거리", "—"], ["최대 계좌 배분", "—"], ["무효화 조건", "—"]
      ];
    }
    const guard = candidate.riskGuard;
    const rr = finiteNumber(candidate.rr);
    const stopDistance = finiteNumber(guard.stop_distance);
    const stopDistancePercent = finiteNumber(guard.stop_distance_percent);
    const allocation = finiteNumber(guard.max_position_percent_by_account_risk);
    return [
      ["진입", formatPrice(candidate.entry)],
      ["손절", formatPrice(candidate.stop)],
      ["목표", formatPrice(candidate.target)],
      ["손익비", rr === null ? "—" : "1:" + formatNumber(rr, 2)],
      ["권장 레버리지", formatLeverage(candidate.leverage) + (candidate.leverage === null ? "" : " · 표시 전용")],
      ["거래당 리스크", candidate.riskPercent === null ? "—" : formatPercent(candidate.riskPercent, false)],
      ["손절 거리", stopDistance === null && stopDistancePercent === null ? "—" : (stopDistance !== null ? formatPrice(stopDistance) : "") + (stopDistancePercent !== null ? " (" + formatPercent(stopDistancePercent, false) + ")" : "")],
      ["최대 계좌 배분", allocation === null ? "—" : formatPercent(allocation, false)],
      ["무효화 조건", candidate.invalidation]
    ];
  }

  function renderScenario() {
    const candidate = currentCandidate();
    const pair = firstText([state.market.pair_label, candidate ? candidate.pair : ""], "자산 확인 중");
    const symbol = firstText([state.market.symbol, state.activeSymbol, candidate ? candidate.symbol : ""], "심볼 데이터 대기");
    const livePrice = finiteNumber(state.market.price);
    elements.marketPair.textContent = pair;
    elements.marketSymbol.textContent = symbol;
    elements.marketPrice.textContent = livePrice === null ? "—" : formatPrice(livePrice);
    elements.marketUpdated.textContent = state.market.last_update ? "최근 업데이트 " + cleanText(state.market.last_update, "") : "업데이트 대기";

    if (!candidate) {
      elements.heroGrade.textContent = "—";
      elements.heroScore.textContent = "—";
      setStatusBadge(elements.heroGradeBadge, "등급 확인 중", "neutral");
      setStatusBadge(elements.heroRiskBadge, "리스크 확인 중", "neutral");
      setStatusBadge(elements.heroFreshness, "시각 확인 중", "neutral");
      elements.heroView.textContent = "분석 관점 확인 중";
      elements.heroTitle.textContent = "저장된 시나리오가 없습니다";
      elements.heroRegime.textContent = "분석 기록이 생기면 실제 계획과 기준을 표시합니다.";
      elements.primaryActionTitle.textContent = "데이터를 기다리세요";
      elements.primaryActionCopy.textContent = "샘플 화면은 주소에 ?demo=1을 추가해 확인할 수 있습니다.";
    } else {
      const action = primaryAction(candidate);
      elements.heroGrade.textContent = candidate.grade;
      elements.heroScore.textContent = candidate.score === null ? "—" : Math.round(candidate.score);
      setStatusBadge(elements.heroGradeBadge, candidate.grade === "—" ? "등급 확인 불가" : "등급 " + candidate.grade, candidate.grade === "A" || candidate.grade === "B+" ? "supporting" : candidate.grade === "D" ? "caution" : "neutral");
      setStatusBadge(elements.heroRiskBadge, candidate.risk.label, candidate.risk.tone);
      setStatusBadge(elements.heroFreshness, candidate.timestamp.available ? (candidate.timestamp.stale ? "오래된 분석" : "최신 범위") : "시각 확인 불가", candidate.timestamp.stale ? "caution" : "neutral");
      elements.heroView.textContent = candidate.direction.label;
      elements.heroTitle.textContent = candidate.summary;
      elements.heroRegime.textContent = candidate.regime + " · " + candidate.timestamp.label;
      elements.primaryActionTitle.textContent = action.title;
      elements.primaryActionCopy.textContent = action.copy;
    }

    const criteria = buildCriteria(candidate);
    renderCriteria(elements.criteriaList, criteria);
    elements.planDirection.textContent = candidate ? candidate.direction.label : "방향 미확인";
    elements.planDirection.dataset.tone = candidate && candidate.direction.key !== "unknown" ? "neutral" : "caution";
    const metrics = planMetrics(candidate);
    const planElements = [
      elements.planEntry, elements.planStop, elements.planTarget, elements.planRr,
      elements.planRecommendedLeverage, elements.planRisk, elements.planStopDistance,
      elements.planAllocation, elements.planInvalidation
    ];
    metrics.forEach((metric, index) => { planElements[index].textContent = metric[1]; });

    const evidence = evidenceFor(candidate);
    appendListItems(elements.factList, evidence.facts, "확인 가능한 사실 데이터가 없습니다.");
    appendListItems(elements.inferenceList, evidence.inferences, "확인 가능한 해석 데이터가 없습니다.");
    renderBreakdown(elements.breakdownList, elements.breakdownCheck, candidate);
    drawChart();
  }

  function candidateFilterMatch(candidate) {
    if (state.candidateFilter === "all") return true;
    if (state.candidateFilter === "watch") return !["A", "B+", "B"].includes(candidate.grade) || candidate.direction.key === "neutral" || ["BLOCK", "INVALID", "SKIPPED", "UNAVAILABLE"].includes(candidate.risk.verdict);
    return candidate.grade === state.candidateFilter;
  }

  function renderCandidates() {
    elements.candidateList.replaceChildren();
    const candidates = state.candidates.filter(candidateFilterMatch);
    const symbols = new Set(state.candidates.map((item) => item.symbol).filter(Boolean));
    const onlyBtc = symbols.size > 0 && Array.from(symbols).every((symbol) => symbol.startsWith("BTC"));
    elements.btcHistoryNote.hidden = demoMode || !onlyBtc;
    const failures = Object.values(state.sources).filter((status) => status === "error").length;
    elements.candidateSummary.textContent = "저장 분석 " + state.candidates.length + "개" + (failures ? " · 일부 읽기 실패" : "");

    if (!state.candidates.length) {
      elements.candidateState.hidden = false;
      elements.candidateState.querySelector("strong").textContent = failures ? "분석 기록을 불러오지 못했습니다" : "아직 저장된 분석이 없습니다";
      elements.candidateState.querySelector("p").textContent = demoMode ? "샘플 후보가 준비되지 않았습니다." : "실제 분석이 저장되면 이 목록에 표시됩니다.";
      return;
    }
    if (!candidates.length) {
      elements.candidateState.hidden = false;
      elements.candidateState.querySelector("strong").textContent = "이 필터에 맞는 후보가 없습니다";
      elements.candidateState.querySelector("p").textContent = "다른 등급 필터를 선택해 보세요.";
      return;
    }
    elements.candidateState.hidden = true;
    candidates.forEach((candidate) => {
      const fragment = elements.candidateTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".candidate-card");
      const button = fragment.querySelector(".candidate-open");
      card.dataset.active = String(Boolean(state.activeSymbol && candidate.symbol === state.activeSymbol));
      fragment.querySelector(".asset-token").textContent = candidate.token;
      fragment.querySelector(".candidate-pair").textContent = candidate.pair;
      fragment.querySelector(".candidate-setup").textContent = candidate.regime;
      setStatusBadge(fragment.querySelector(".candidate-grade"), "등급 " + candidate.grade + (candidate.score === null ? "" : " · " + Math.round(candidate.score)), candidate.grade === "A" || candidate.grade === "B+" ? "supporting" : "neutral");
      setStatusBadge(fragment.querySelector(".candidate-risk"), candidate.risk.verdict, candidate.risk.tone);
      fragment.querySelector('[data-field="direction"]').textContent = candidate.direction.label;
      fragment.querySelector('[data-field="entry"]').textContent = formatPrice(candidate.entry);
      fragment.querySelector('[data-field="stop"]').textContent = formatPrice(candidate.stop);
      fragment.querySelector('[data-field="target"]').textContent = formatPrice(candidate.target);
      const time = fragment.querySelector("time");
      time.textContent = candidate.timestamp.label;
      if (candidate.timestamp.raw) time.dateTime = candidate.timestamp.raw;
      button.setAttribute("aria-label", candidate.pair + " 시나리오 상세 보기");
      button.addEventListener("click", () => openDetail(candidate.id, "candidates"));
      elements.candidateList.append(fragment);
    });
  }

  function macroItem(label, value, change) {
    const item = document.createElement("article");
    item.className = "tradfi-item";
    const name = document.createElement("span");
    name.textContent = label;
    const price = document.createElement("strong");
    price.textContent = Number.isFinite(value) ? formatNumber(value, 2) : "데이터 없음";
    const delta = document.createElement("small");
    delta.textContent = Number.isFinite(change) ? formatPercent(change, true) : "변화율 없음";
    if (Number.isFinite(change)) delta.classList.add(change >= 0 ? "positive" : "negative");
    item.append(name, price, delta);
    return item;
  }

  function renderMacro() {
    elements.tradfiGrid.replaceChildren();
    const trad = asObject(state.macro.trad_markets);
    ["SPX", "NDX", "VIX", "GOLD"].forEach((key) => {
      const item = asObject(trad[key]);
      elements.tradfiGrid.append(macroItem(key, finiteNumber(item.price), finiteNumber(item.chg_pct)));
    });
    const ibit = asObject(state.macro.IBIT_PX);
    elements.tradfiGrid.append(macroItem("IBIT", finiteNumber(ibit.value, ibit.price), finiteNumber(ibit.change_24h, ibit.chg_pct, ibit.change_1d)));
  }

  function readPrivacyPreference() {
    try {
      const stored = window.localStorage.getItem(PRIVACY_KEY);
      return stored === null ? true : stored !== "false";
    } catch (_error) {
      return true;
    }
  }

  function savePrivacyPreference() {
    try {
      window.localStorage.setItem(PRIVACY_KEY, String(state.privacyHidden));
    } catch (_error) {
      // Privacy remains in memory when local storage is unavailable.
    }
  }

  function localScenarioKey(kind, candidate) {
    return STORAGE_PREFIX + ":" + kind + ":" + safeIdentifier(candidate.id);
  }

  function readLocalText(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (_error) {
      return fallback;
    }
  }

  function writeLocalText(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function privateValue(value, formatter) {
    if (state.privacyHidden) return "••••••";
    return formatter(value);
  }

  function renderAccount() {
    const account = asObject(state.account);
    elements.privacyToggle.setAttribute("aria-pressed", String(state.privacyHidden));
    elements.privacyToggle.querySelector("span").textContent = state.privacyHidden ? "잔고 표시" : "잔고 숨김";
    elements.privacyIconUse.setAttribute("href", state.privacyHidden ? "#icon-eye-off" : "#icon-eye");
    elements.accountUpdated.textContent = account.updated_at ? "최근 스냅샷 " + cleanText(account.updated_at, "") : "계좌 스냅샷을 기다리고 있습니다.";
    elements.accountEquity.textContent = privateValue(finiteNumber(account.account_equity), (value) => formatMoney(value, false));
    elements.accountWallet.textContent = privateValue(finiteNumber(account.wallet_balance), (value) => formatMoney(value, false));
    elements.accountAvailable.textContent = privateValue(finiteNumber(account.available_balance), (value) => formatMoney(value, false));
    const pnl = finiteNumber(account.today_total_pnl, account.today_cash_pnl);
    const pnlPct = finiteNumber(account.today_pnl_pct);
    elements.accountPnl.textContent = state.privacyHidden ? "••••••" : (formatMoney(pnl, true) + (pnlPct === null ? "" : " · " + formatPercent(pnlPct, true)));
    elements.accountPositionCount.textContent = finiteNumber(account.open_position_count) === null ? "—" : formatNumber(finiteNumber(account.open_position_count), 0) + "개";
    elements.accountNotional.textContent = privateValue(finiteNumber(account.open_position_notional), (value) => formatMoney(value, false));
    elements.accountEffectiveLeverage.textContent = formatLeverage(finiteNumber(account.effective_leverage));
    elements.accountConfiguredLeverage.textContent = formatLeverage(finiteNumber(account.configured_leverage));
    elements.accountLeverageDisplay.textContent = cleanText(account.leverage_display, "포지션 레버리지 데이터 없음", 160);

    const positionsReady = Object.prototype.hasOwnProperty.call(account, "open_positions");
    const positions = asArray(account.open_positions);
    elements.positionsCount.textContent = positions.length + "개";
    elements.positionsList.replaceChildren();
    if (!positions.length) {
      elements.positionsState.hidden = false;
      elements.positionsState.querySelector("strong").textContent = !positionsReady ? "포지션 데이터 대기" : account.open_positions === null ? "포지션 조회 실패" : "오픈 포지션 없음";
      elements.positionsState.querySelector("p").textContent = !positionsReady ? "계좌 스트림이 연결되면 현재 포지션을 표시합니다." : account.open_positions === null ? "민감한 오류 원문은 표시하지 않습니다." : "현재 계좌에 열린 포지션이 없습니다.";
      return;
    }
    elements.positionsState.hidden = true;
    positions.forEach((positionValue) => {
      const position = asObject(positionValue);
      const fragment = elements.positionTemplate.content.cloneNode(true);
      fragment.querySelector(".position-symbol").textContent = cleanText(position.symbol, "심볼 미확인", 24);
      const side = cleanText(position.side, position.side_code || "방향 미확인", 16);
      setStatusBadge(fragment.querySelector(".position-side"), side, /롱|LONG/i.test(side) ? "supporting" : /숏|SHORT/i.test(side) ? "caution" : "neutral");
      const leverage = finiteNumber(position.leverage);
      const notional = finiteNumber(position.notional);
      const margin = leverage && notional !== null ? notional / leverage : null;
      fragment.querySelector('[data-position="entry"]').textContent = formatPrice(finiteNumber(position.entry_price));
      fragment.querySelector('[data-position="mark"]').textContent = formatPrice(finiteNumber(position.mark_price));
      fragment.querySelector('[data-position="pnl"]').textContent = privateValue(finiteNumber(position.unrealized_pnl), (value) => formatMoney(value, true));
      fragment.querySelector('[data-position="leverage"]').textContent = formatLeverage(leverage);
      fragment.querySelector('[data-position="liquidation"]').textContent = formatPrice(finiteNumber(position.liquidation_price));
      fragment.querySelector('[data-position="margin"]').textContent = privateValue(margin, (value) => formatMoney(value, false)) + (position.margin_type ? " · " + cleanText(position.margin_type, "", 16) : "");
      fragment.querySelector('[data-position="tp"]').textContent = formatPrice(finiteNumber(position.tp_price));
      fragment.querySelector('[data-position="sl"]').textContent = formatPrice(finiteNumber(position.sl_price));
      elements.positionsList.append(fragment);
    });
  }

  function renderDetail() {
    const candidate = state.candidates.find((item) => item.id === state.selectedId) || null;
    if (!candidate) {
      elements.detailPair.textContent = "시나리오 없음";
      elements.detailSetup.textContent = "후보 목록에서 항목을 선택하세요.";
      setStatusBadge(elements.detailGrade, "—", "neutral");
      setStatusBadge(elements.detailRisk, "—", "neutral");
      elements.detailUpdated.textContent = "시각 미확인";
      elements.detailPlanGrid.replaceChildren();
      renderCriteria(elements.detailCriteriaList, buildCriteria(null));
      appendListItems(elements.detailFacts, [], "확인 가능한 사실 데이터가 없습니다.");
      appendListItems(elements.detailInferences, [], "확인 가능한 해석 데이터가 없습니다.");
      renderBreakdown(elements.detailBreakdownList, elements.detailBreakdownCheck, null);
      elements.localMemo.value = "";
      return;
    }
    const action = primaryAction(candidate);
    elements.detailPair.textContent = candidate.pair + " · " + candidate.direction.label;
    elements.detailSetup.textContent = candidate.regime + " · 시나리오 명확도 " + (candidate.score === null ? "확인 불가" : Math.round(candidate.score));
    setStatusBadge(elements.detailGrade, "등급 " + candidate.grade, candidate.grade === "A" || candidate.grade === "B+" ? "supporting" : "neutral");
    setStatusBadge(elements.detailRisk, candidate.risk.label, candidate.risk.tone);
    setStatusBadge(elements.detailUpdated, candidate.timestamp.stale ? candidate.timestamp.label + " · 오래됨" : candidate.timestamp.label, candidate.timestamp.stale ? "caution" : "neutral");
    elements.detailActionTitle.textContent = action.title;
    elements.detailActionCopy.textContent = action.copy;
    elements.detailPlanGrid.replaceChildren();
    planMetrics(candidate).forEach((metric, index) => {
      const wrapper = document.createElement("div");
      if (index === 8) wrapper.className = "wide";
      const term = document.createElement("dt");
      term.textContent = metric[0];
      const definition = document.createElement("dd");
      definition.textContent = metric[1];
      wrapper.append(term, definition);
      elements.detailPlanGrid.append(wrapper);
    });
    renderCriteria(elements.detailCriteriaList, buildCriteria(candidate));
    const evidence = evidenceFor(candidate);
    appendListItems(elements.detailFacts, evidence.facts, "확인 가능한 사실 데이터가 없습니다.");
    appendListItems(elements.detailInferences, evidence.inferences, "확인 가능한 해석 데이터가 없습니다.");
    renderBreakdown(elements.detailBreakdownList, elements.detailBreakdownCheck, candidate);
    const favorite = readLocalText(localScenarioKey("favorite", candidate), "false") === "true";
    elements.favoriteButton.setAttribute("aria-pressed", String(favorite));
    elements.favoriteButton.setAttribute("aria-label", favorite ? "관심 시나리오 표시 해제" : "관심 시나리오로 표시");
    elements.localMemo.value = readLocalText(localScenarioKey("memo", candidate), "").slice(0, 300);
    elements.memoStatus.textContent = "이 기기에만 저장 · 서버로 전송되지 않습니다.";
  }

  function chartData() {
    return asObject(asObject(state.market.charts)[state.currentTf]);
  }

  function validCandle(value) {
    return Array.isArray(value) && value.length >= 4 && value.slice(0, 4).every((item) => finiteNumber(item) !== null);
  }

  function drawChart() {
    const canvas = elements.canvas;
    const context = canvas.getContext("2d");
    if (!context) {
      elements.chartEmpty.hidden = false;
      elements.chartEmpty.querySelector("strong").textContent = "Canvas를 지원하지 않는 브라우저입니다";
      elements.chartEmpty.querySelector("span").textContent = "가격 차트 대신 위의 실측 지표를 확인하세요.";
      return;
    }
    const chart = chartData();
    const sourceCandles = asArray(chart.candles);
    const count = Math.min(80, sourceCandles.length);
    const start = Math.max(0, sourceCandles.length - count);
    const candles = sourceCandles.slice(start);
    if (candles.length < 2 || !candles.some(validCandle)) {
      elements.chartEmpty.hidden = false;
      context.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    elements.chartEmpty.hidden = true;
    const rect = elements.chartWrap.getBoundingClientRect();
    const width = Math.max(280, rect.width);
    const height = Math.max(245, rect.height);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const targetWidth = Math.round(width * dpr);
    const targetHeight = Math.round(height * dpr);
    if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
      canvas.width = targetWidth;
      canvas.height = targetHeight;
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);

    const pad = { top: 18, right: 54, bottom: 26, left: 10 };
    const volumeHeight = Math.max(42, height * 0.18);
    const priceBottom = height - pad.bottom - volumeHeight - 14;
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = priceBottom - pad.top;
    const series = {
      ema: asArray(chart.ema_9).slice(start, start + candles.length),
      sma50: asArray(chart.sma_50).slice(start, start + candles.length),
      sma200: asArray(chart.sma_200).slice(start, start + candles.length)
    };
    const prices = [];
    candles.forEach((candle) => {
      if (validCandle(candle)) prices.push(finiteNumber(candle[2]), finiteNumber(candle[3]));
    });
    Object.values(series).forEach((values) => values.forEach((value) => {
      const number = finiteNumber(value);
      if (number !== null) prices.push(number);
    }));
    const currentPrice = finiteNumber(state.market.price);
    if (currentPrice !== null) prices.push(currentPrice);
    let minPrice = Math.min.apply(null, prices);
    let maxPrice = Math.max.apply(null, prices);
    if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) return;
    const pricePadding = Math.max((maxPrice - minPrice) * 0.08, Math.abs(maxPrice) * 0.0005, 0.001);
    minPrice -= pricePadding;
    maxPrice += pricePadding;
    const xAt = (index) => pad.left + (index + 0.5) * plotWidth / candles.length;
    const yAt = (price) => pad.top + (maxPrice - price) / (maxPrice - minPrice) * plotHeight;

    context.strokeStyle = "rgba(58,80,102,.45)";
    context.fillStyle = "#8194a6";
    context.lineWidth = 1;
    context.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    context.textAlign = "left";
    for (let line = 0; line <= 4; line += 1) {
      const y = pad.top + plotHeight * line / 4;
      context.beginPath();
      context.moveTo(pad.left, y);
      context.lineTo(width - pad.right, y);
      context.stroke();
      const labelPrice = maxPrice - (maxPrice - minPrice) * line / 4;
      context.fillText(formatNumber(labelPrice, labelPrice >= 100 ? 0 : 2), width - pad.right + 5, y + 3);
    }

    const volumes = asArray(chart.volume).slice(start, start + candles.length);
    const maxVolume = Math.max.apply(null, volumes.map((item) => finiteNumber(item) || 0).concat([1]));
    const volumeTop = height - pad.bottom - volumeHeight;
    const candleWidth = clamp(plotWidth / candles.length * 0.62, 2, 9);
    volumes.forEach((value, index) => {
      const number = finiteNumber(value);
      if (number === null) return;
      const candle = candles[index];
      const bullish = validCandle(candle) && finiteNumber(candle[1]) >= finiteNumber(candle[0]);
      const barHeight = number / maxVolume * volumeHeight;
      context.fillStyle = bullish ? "rgba(98,220,139,.28)" : "rgba(255,133,133,.25)";
      context.fillRect(xAt(index) - candleWidth / 2, height - pad.bottom - barHeight, candleWidth, barHeight);
    });
    context.strokeStyle = "rgba(58,80,102,.6)";
    context.beginPath();
    context.moveTo(pad.left, volumeTop);
    context.lineTo(width - pad.right, volumeTop);
    context.stroke();

    candles.forEach((candle, index) => {
      if (!validCandle(candle)) return;
      const open = finiteNumber(candle[0]);
      const close = finiteNumber(candle[1]);
      const low = finiteNumber(candle[2]);
      const high = finiteNumber(candle[3]);
      const bullish = close >= open;
      const color = bullish ? "#62dc8b" : "#ff8585";
      context.strokeStyle = color;
      context.fillStyle = bullish ? "rgba(98,220,139,.34)" : "rgba(255,133,133,.34)";
      context.beginPath();
      context.moveTo(xAt(index), yAt(high));
      context.lineTo(xAt(index), yAt(low));
      context.stroke();
      const bodyTop = Math.min(yAt(open), yAt(close));
      const bodyHeight = Math.max(1, Math.abs(yAt(open) - yAt(close)));
      context.fillRect(xAt(index) - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
      context.strokeRect(xAt(index) - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    });

    function drawLine(values, color, dash) {
      context.save();
      context.strokeStyle = color;
      context.lineWidth = 1.5;
      context.setLineDash(dash || []);
      context.beginPath();
      let started = false;
      values.forEach((value, index) => {
        const number = finiteNumber(value);
        if (number === null) return;
        if (!started) {
          context.moveTo(xAt(index), yAt(number));
          started = true;
        } else {
          context.lineTo(xAt(index), yAt(number));
        }
      });
      if (started) context.stroke();
      context.restore();
    }
    drawLine(series.ema, "#51d7ca");
    drawLine(series.sma50, "#78abff");
    drawLine(series.sma200, "#c4a5ff", [5, 4]);
    if (currentPrice !== null && currentPrice >= minPrice && currentPrice <= maxPrice) {
      const y = yAt(currentPrice);
      context.save();
      context.strokeStyle = "#f2c66d";
      context.fillStyle = "#f2c66d";
      context.setLineDash([5, 4]);
      context.beginPath();
      context.moveTo(pad.left, y);
      context.lineTo(width - pad.right, y);
      context.stroke();
      context.setLineDash([]);
      context.textAlign = "right";
      context.fillText(formatNumber(currentPrice, currentPrice >= 100 ? 0 : 2), width - 3, y - 4);
      context.restore();
    }
    const labels = asArray(chart.labels).slice(start, start + candles.length);
    context.fillStyle = "#6f8293";
    context.textAlign = "center";
    [0, Math.floor((candles.length - 1) / 2), candles.length - 1].forEach((index) => {
      if (index >= 0 && labels[index]) context.fillText(cleanText(labels[index], "", 16).slice(-11), xAt(index), height - 8);
    });
    elements.canvas.setAttribute("aria-label", state.currentTf + " 차트. 최근 " + candles.length + "개 캔들, 현재가 " + (currentPrice === null ? "미확인" : formatPrice(currentPrice)) + ", EMA 9, SMA 50, SMA 200, 거래량을 표시합니다.");
  }

  function sameOriginStreamPath(path) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !ALLOWED_STREAMS.has(url.pathname) || url.search || url.hash) {
      throw new Error("허용되지 않은 스트림 경로");
    }
    return url.pathname;
  }

  function setConnection(kind, streamState) {
    state.streamState[kind] = streamState;
    const connection = kind === "market" ? elements.marketConnection : elements.accountConnection;
    const label = kind === "market" ? elements.marketConnectionLabel : elements.accountConnectionLabel;
    const labels = {
      loading: "연결 준비 중",
      online: "실시간 연결",
      reconnecting: "재연결 중",
      stale: "업데이트 지연",
      offline: "연결 확인 필요",
      demo: "샘플 모드"
    };
    connection.dataset.state = streamState === "stale" ? "partial" : streamState;
    label.textContent = labels[streamState] || "상태 확인 중";
  }

  function scheduleOffline(kind) {
    if (state.reconnectTimers[kind]) window.clearTimeout(state.reconnectTimers[kind]);
    state.reconnectTimers[kind] = window.setTimeout(() => {
      if (state.streamState[kind] === "reconnecting") setConnection(kind, "offline");
    }, 12000);
  }

  function mergeMarket(data) {
    const update = asObject(data);
    const charts = { ...asObject(state.market.charts), ...asObject(update.charts) };
    state.market = { ...state.market, ...update, charts: charts };
    state.lastReceipt.market = Date.now();
    if (update.symbol) state.activeSymbol = normalizedSymbol(update);
    setConnection("market", "online");
    renderScenario();
    renderCandidates();
  }

  function handleStreamMessage(kind, event) {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (_error) {
      return;
    }
    const data = asObject(asObject(message).data);
    if (kind === "market" && ["snapshot", "market", "price"].includes(cleanText(message.type, ""))) mergeMarket(data);
    if (kind === "account" && ["snapshot", "account"].includes(cleanText(message.type, ""))) {
      state.account = { ...state.account, ...data };
      state.lastReceipt.account = Date.now();
      setConnection("account", "online");
      renderAccount();
      renderScenario();
      if (routeFromHash().screen === "detail") renderDetail();
    }
  }

  function connectMarketStream() {
    sameOriginStreamPath("/api/market-stream");
    const source = new EventSource("/api/market-stream");
    state.streams.market = source;
    source.addEventListener("open", () => setConnection("market", "online"));
    source.addEventListener("message", (event) => handleStreamMessage("market", event));
    source.addEventListener("error", () => {
      if (source.readyState === EventSource.CLOSED) setConnection("market", "offline");
      else {
        setConnection("market", "reconnecting");
        scheduleOffline("market");
      }
    });
  }

  function connectAccountStream() {
    sameOriginStreamPath("/api/account-stream");
    const source = new EventSource("/api/account-stream");
    state.streams.account = source;
    source.addEventListener("open", () => setConnection("account", "online"));
    source.addEventListener("message", (event) => handleStreamMessage("account", event));
    source.addEventListener("error", () => {
      if (source.readyState === EventSource.CLOSED) setConnection("account", "offline");
      else {
        setConnection("account", "reconnecting");
        scheduleOffline("account");
      }
    });
  }

  function closeStreams() {
    Object.values(state.streams).forEach((source) => {
      if (source) source.close();
    });
    Object.values(state.reconnectTimers).forEach((timer) => {
      if (timer) window.clearTimeout(timer);
    });
  }

  async function requestJson(path, timeoutMs) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) throw new Error("same-origin API 읽기만 허용됩니다.");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS);
    try {
      const response = await window.fetch(url.pathname + url.search, {
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
    const seenPayloads = new Set();
    const normalized = [];
    rawCandidates.forEach((raw, index) => {
      const candidate = normalizeCandidate(raw, index);
      const signature = [candidate.symbol || candidate.pair, candidate.timestamp.raw, candidate.entry, candidate.stop, candidate.target].join("|");
      const payloadSignature = Object.keys(candidate.analysisJson).length
        ? (candidate.symbol || candidate.pair) + "|" + JSON.stringify(candidate.analysisJson)
        : "";
      if (seen.has(signature) || (payloadSignature && seenPayloads.has(payloadSignature))) return;
      seen.add(signature);
      if (payloadSignature) seenPayloads.add(payloadSignature);
      normalized.push(candidate);
    });
    return normalized;
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
      const symbolPayload = asObject(results[3].value);
      state.activeSymbol = normalizedSymbol(symbolPayload);
      if (!state.market.symbol && symbolPayload.symbol) {
        state.market.symbol = symbolPayload.symbol;
        state.market.pair_label = symbolPayload.pair_label;
      }
    }
    state.candidates = deduplicateCandidates(rawCandidates);
    renderAll();
  }

  function loadDemoData() {
    elements.demoBanner.hidden = false;
    state.market = DEMO_MARKET;
    state.activeSymbol = "BTCUSDT";
    state.analysis = DEMO_ANALYSES[0];
    state.candidates = deduplicateCandidates(DEMO_ANALYSES);
    state.account = DEMO_ACCOUNT;
    state.macro = DEMO_MACRO;
    state.sources = { analysis: "ready", history: "ready", macro: "ready", symbol: "ready" };
    state.lastReceipt.market = Date.now();
    state.lastReceipt.account = Date.now();
    setConnection("market", "demo");
    setConnection("account", "demo");
    renderAll();
  }

  function routeFromHash() {
    const hash = window.location.hash.replace(/^#/, "");
    if (hash.startsWith("detail/")) return { screen: "detail", id: decodeURIComponent(hash.slice(7)) };
    if (hash === "candidates" || hash === "account" || hash === "scenario") return { screen: hash, id: null };
    return { screen: "scenario", id: null };
  }

  function renderRoute() {
    const route = routeFromHash();
    if (route.screen === "detail") state.selectedId = route.id;
    elements.screens.forEach((screen) => {
      screen.hidden = screen.dataset.screen !== route.screen;
    });
    elements.navButtons.forEach((button) => {
      const active = button.dataset.route === route.screen || (route.screen === "detail" && button.dataset.route === state.detailOrigin);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (route.screen === "detail") renderDetail();
    if (route.screen === "scenario") window.requestAnimationFrame(drawChart);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigate(route) {
    const next = "#" + route;
    if (window.location.hash === next) renderRoute();
    else window.location.hash = next;
  }

  function openDetail(id, origin) {
    state.selectedId = id;
    state.detailOrigin = origin;
    navigate("detail/" + encodeURIComponent(id));
  }

  function renderAll() {
    renderScenario();
    renderCandidates();
    renderMacro();
    renderAccount();
    renderRoute();
  }

  function bindEvents() {
    elements.navButtons.forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
    elements.timeframeButtons.forEach((button) => button.addEventListener("click", () => {
      state.currentTf = button.dataset.timeframe;
      elements.timeframeButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      elements.canvas.setAttribute("aria-label", state.currentTf + " 시장 차트");
      renderScenario();
      if (routeFromHash().screen === "detail") renderDetail();
    }));
    elements.candidateFilter.addEventListener("change", () => {
      state.candidateFilter = elements.candidateFilter.value;
      renderCandidates();
    });
    elements.privacyToggle.addEventListener("click", () => {
      state.privacyHidden = !state.privacyHidden;
      savePrivacyPreference();
      renderAccount();
    });
    elements.detailBack.addEventListener("click", () => {
      if (window.history.length > 1) window.history.back();
      else navigate(state.detailOrigin || "candidates");
    });
    elements.favoriteButton.addEventListener("click", () => {
      const candidate = state.candidates.find((item) => item.id === state.selectedId);
      if (!candidate) return;
      const favorite = elements.favoriteButton.getAttribute("aria-pressed") !== "true";
      writeLocalText(localScenarioKey("favorite", candidate), String(favorite));
      elements.favoriteButton.setAttribute("aria-pressed", String(favorite));
      elements.favoriteButton.setAttribute("aria-label", favorite ? "관심 시나리오 표시 해제" : "관심 시나리오로 표시");
    });
    elements.localMemo.addEventListener("input", () => {
      const candidate = state.candidates.find((item) => item.id === state.selectedId);
      if (!candidate) return;
      const saved = writeLocalText(localScenarioKey("memo", candidate), elements.localMemo.value.slice(0, 300));
      elements.memoStatus.textContent = saved ? "이 기기에만 저장했습니다." : "이 기기의 저장소를 사용할 수 없습니다.";
    });
    window.addEventListener("hashchange", renderRoute);
    window.addEventListener("beforeunload", closeStreams);
    if ("ResizeObserver" in window) {
      state.chartObserver = new ResizeObserver(() => window.requestAnimationFrame(drawChart));
      state.chartObserver.observe(elements.chartWrap);
    } else {
      window.addEventListener("resize", drawChart);
    }
  }

  function monitorFreshness() {
    if (demoMode) return;
    ["market", "account"].forEach((kind) => {
      if (state.lastReceipt[kind] && Date.now() - state.lastReceipt[kind] > STREAM_STALE_MS && state.streamState[kind] === "online") {
        setConnection(kind, "stale");
      }
    });
  }

  function initialize() {
    if (!window.location.hash) window.history.replaceState(null, "", window.location.pathname + window.location.search + "#scenario");
    bindEvents();
    renderAll();
    if (demoMode) {
      loadDemoData();
      return;
    }
    connectMarketStream();
    connectAccountStream();
    loadLiveReads();
    window.setInterval(monitorFreshness, 10000);
  }

  initialize();
})();
