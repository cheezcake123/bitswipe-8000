(() => {
  "use strict";

  const STORAGE_PREFIX = "bitswipe_mobile_preview_v1";
  const HOME_CHECKS_KEY = `${STORAGE_PREFIX}:home-checks`;
  const REQUEST_TIMEOUT_MS = 6000;
  const STALE_AFTER_MS = 6 * 60 * 60 * 1000;
  const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";

  const DEMO_ENTRIES = [
    {
      symbol: "BTCUSDT",
      pair_label: "BTC/USDT",
      timestamp: "2026-07-16T09:20:00+09:00",
      signal: "BUY",
      confidence: 78,
      trade_levels: { entry: 64800, stop: 64200, target: 66180 },
      report_sections: {
        regime: "눌림 후 반등 관찰",
        summary: "진입 구간 안에서 지지가 확인될 때만 계획을 검토합니다."
      },
      analysis_json: {
        trade: { entry: 64800, stop: 64200, target: 66180, risk_reward_ratio: 2.3 },
        invalidation: "64,200 아래에서 계획 무효",
        key_facts: ["4시간 추세와 진입 구간이 같은 방향인지 확인"]
      },
      risk_guard: { valid: true, risk_percent: 1, risk_reward_ratio: 2.3 }
    },
    {
      symbol: "ETHUSDT",
      pair_label: "ETH/USDT",
      timestamp: "2026-07-16T08:45:00+09:00",
      signal: "HOLD",
      confidence: 61,
      trade_levels: { entry: 3420, stop: 3360, target: 3590 },
      report_sections: {
        regime: "돌파 후 리테스트 관찰",
        summary: "거래량 확인 전에는 진입을 보류합니다."
      },
      analysis_json: {
        trade: { entry: 3420, stop: 3360, target: 3590, risk_reward_ratio: 2.1 },
        invalidation: "리테스트 지지가 확인되지 않으면 관찰 종료"
      },
      risk_guard: { valid: false, risk_percent: 1, risk_reward_ratio: 2.1 }
    },
    {
      symbol: "SOLUSDT",
      pair_label: "SOL/USDT",
      timestamp: "2026-07-16T08:10:00+09:00",
      signal: "SELL",
      confidence: 66,
      trade_levels: { entry: 149, stop: 153, target: 141 },
      report_sections: {
        regime: "지지선 이탈 관찰",
        summary: "반등 실패와 거래량 조건이 함께 확인되어야 합니다."
      },
      analysis_json: {
        trade: { entry: 149, stop: 153, target: 141, risk_reward_ratio: 2 },
        invalidation: "153 위에서 시나리오 무효"
      },
      risk_guard: { valid: true, risk_percent: 0.8, risk_reward_ratio: 2 }
    }
  ];

  const state = {
    candidates: [],
    selectedId: null,
    detailOrigin: "signals",
    filter: "all",
    sourceStatus: { fulfilled: 0, rejected: 0, total: 2 },
    loading: true
  };

  const elements = {
    screens: Array.from(document.querySelectorAll("[data-screen]")),
    navButtons: Array.from(document.querySelectorAll(".bottom-nav [data-route]")),
    demoBanner: document.querySelector("#demo-banner"),
    connectionStatus: document.querySelector("#connection-status"),
    connectionLabel: document.querySelector(".connection-label"),
    focusCard: document.querySelector("#focus-card"),
    focusStatus: document.querySelector("#focus-status"),
    focusSymbol: document.querySelector("#focus-symbol"),
    focusSetup: document.querySelector("#focus-setup"),
    focusNext: document.querySelector("#focus-next"),
    focusToken: document.querySelector("#focus-symbol-icon"),
    homeRisk: document.querySelector("#home-risk"),
    homeRr: document.querySelector("#home-rr"),
    homeStop: document.querySelector("#home-stop"),
    loadedCount: document.querySelector("#loaded-count"),
    plannedCount: document.querySelector("#planned-count"),
    homeCheckProgress: document.querySelector("#home-check-progress"),
    homeCheckButtons: Array.from(document.querySelectorAll("[data-home-check]")),
    signalsSummary: document.querySelector("#signals-summary"),
    signalsState: document.querySelector("#signals-state"),
    signalList: document.querySelector("#signal-list"),
    signalTemplate: document.querySelector("#signal-card-template"),
    signalFilter: document.querySelector("#signal-filter"),
    detailBack: document.querySelector("#detail-back"),
    detailToken: document.querySelector("#detail-token"),
    detailSymbol: document.querySelector("#detail-symbol"),
    detailState: document.querySelector("#detail-state"),
    detailSetup: document.querySelector("#detail-setup"),
    detailUpdated: document.querySelector("#detail-updated"),
    detailEntry: document.querySelector("#detail-entry"),
    detailStop: document.querySelector("#detail-stop"),
    detailTarget: document.querySelector("#detail-target"),
    detailRr: document.querySelector("#detail-rr"),
    actionTitle: document.querySelector("#action-banner-title"),
    actionCopy: document.querySelector("#action-banner-copy"),
    favoriteButton: document.querySelector("#favorite-button"),
    checklistInputs: Array.from(document.querySelectorAll("[data-checklist]")),
    scenarioNotes: document.querySelector("#scenario-notes"),
    localMemo: document.querySelector("#local-memo"),
    memoStatus: document.querySelector("#memo-status"),
    completeChecklist: document.querySelector("#complete-checklist"),
    detailFeedback: document.querySelector("#detail-feedback")
  };

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function cleanText(value, fallback = "", maxLength = 180) {
    if (typeof value !== "string" && typeof value !== "number") return fallback;
    const text = String(value).replace(/\s+/g, " ").trim();
    return text ? text.slice(0, maxLength) : fallback;
  }

  function firstText(values, fallback = "") {
    for (const value of values) {
      const text = cleanText(value);
      if (text) return text;
    }
    return fallback;
  }

  function finiteNumber(...values) {
    for (const value of values) {
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (typeof value === "string") {
        const normalized = value.replaceAll(",", "").trim();
        if (/^-?\d+(?:\.\d+)?$/.test(normalized)) {
          const parsed = Number(normalized);
          if (Number.isFinite(parsed)) return parsed;
        }
      }
    }
    return null;
  }

  function formatNumber(value) {
    if (!Number.isFinite(value)) return null;
    const magnitude = Math.abs(value);
    const maximumFractionDigits = magnitude >= 100 ? 2 : magnitude >= 1 ? 3 : 6;
    return new Intl.NumberFormat("ko-KR", { maximumFractionDigits }).format(value);
  }

  function formatPrice(value, fallback) {
    const formatted = formatNumber(value);
    return formatted === null ? fallback : formatted;
  }

  function normalizedPair(raw) {
    const explicit = cleanText(raw.pair_label, "", 24).toUpperCase();
    if (/^[A-Z0-9]{2,12}\/[A-Z0-9]{2,8}$/.test(explicit)) return explicit;
    const symbol = cleanText(raw.symbol, "", 24).toUpperCase().replace(/[^A-Z0-9]/g, "");
    const quote = ["USDT", "USDC", "FDUSD", "BUSD", "TUSD"].find((item) => symbol.endsWith(item));
    if (quote && symbol.length > quote.length) return `${symbol.slice(0, -quote.length)}/${quote}`;
    return symbol || "자산 미확인";
  }

  function tokenForPair(pair) {
    const base = pair.split("/")[0].replace(/[^A-Z0-9]/g, "");
    return base.slice(0, 3) || "—";
  }

  function safeIdentifier(value) {
    return cleanText(value, "scenario", 80).replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  function isNoTradeSignal(signal) {
    return /^(HOLD|WAIT|NO[_ -]?TRADE|NEUTRAL|관망|중립|대기)$/i.test(signal.trim());
  }

  function stateMeta(signal, hasPlan, riskValid) {
    if (isNoTradeSignal(signal)) {
      return { key: "no-trade", label: "진입 보류", className: "state-no-trade" };
    }
    if (hasPlan && riskValid) {
      return { key: "planned", label: "계획 가능", className: "state-planned" };
    }
    if (signal || hasPlan) {
      return { key: "watch", label: "관찰", className: "state-watch" };
    }
    return { key: "watch", label: "데이터 확인", className: "state-neutral" };
  }

  function setupLabel(signal, regime) {
    if (regime) return regime;
    if (/^(BUY|LONG|STRONG_BUY)$/i.test(signal)) return "상승 방향 시나리오";
    if (/^(SELL|SHORT|STRONG_SELL)$/i.test(signal)) return "하락 방향 시나리오";
    if (isNoTradeSignal(signal)) return "조건 확인 전 진입 보류";
    return "설정 정보 없음";
  }

  function timestampMeta(rawValue) {
    const raw = cleanText(rawValue, "", 48);
    if (!raw) return { label: "시각 미확인", stale: false, value: "" };
    const parsed = Date.parse(raw);
    if (!Number.isFinite(parsed)) return { label: `최근 분석 · ${raw}`, stale: false, value: raw };
    const date = new Date(parsed);
    const label = new Intl.DateTimeFormat("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    }).format(date);
    return { label, stale: !demoMode && Date.now() - parsed > STALE_AFTER_MS, value: raw };
  }

  function normalizeCandidate(rawValue, index) {
    const raw = asObject(rawValue);
    const analysisJson = asObject(raw.analysis_json);
    const reportSections = asObject(raw.report_sections);
    const tradeLevels = asObject(raw.trade_levels);
    const trade = asObject(analysisJson.trade);
    const riskGuard = asObject(raw.risk_guard);
    const tradingSignal = asObject(raw.trading_signal);
    const pair = normalizedPair(raw);
    const signal = firstText([raw.signal, tradingSignal.signal_en, tradingSignal.signal_kr, analysisJson.view], "");
    const regime = firstText([reportSections.regime, raw.regime, analysisJson.regime, tradingSignal.regime], "");
    const entry = finiteNumber(tradeLevels.entry, trade.entry);
    const entryLow = finiteNumber(tradeLevels.entry_low, tradeLevels.entry_min, trade.entry_low, trade.entry_min);
    const entryHigh = finiteNumber(tradeLevels.entry_high, tradeLevels.entry_max, trade.entry_high, trade.entry_max);
    const stop = finiteNumber(tradeLevels.stop, trade.stop, riskGuard.stop_price);
    const target = finiteNumber(tradeLevels.target, trade.target, riskGuard.target_price);
    const riskReward = finiteNumber(riskGuard.risk_reward_ratio, trade.risk_reward_ratio, tradeLevels.risk_reward_ratio);
    const riskPercent = finiteNumber(riskGuard.risk_percent, trade.risk_percent);
    const confidence = finiteNumber(raw.confidence, tradingSignal.confidence, analysisJson.confidence);
    const hasEntry = entry !== null || (entryLow !== null && entryHigh !== null);
    const hasPlan = hasEntry && stop !== null && target !== null;
    const status = stateMeta(signal, hasPlan, riskGuard.valid === true);
    const timestamp = timestampMeta(firstText([raw.timestamp, raw.completed_at, raw.updated_at, raw.analysis_time], ""));
    const summary = firstText([reportSections.summary, analysisJson.summary, raw.summary], "");
    const invalidation = firstText([analysisJson.invalidation, trade.invalidation], "");
    const facts = Array.isArray(analysisJson.key_facts)
      ? analysisJson.key_facts.map((item) => cleanText(item, "", 110)).filter(Boolean).slice(0, 2)
      : [];
    const seed = `${pair}-${timestamp.value}-${entry ?? entryLow ?? "none"}-${index}`;

    return {
      id: safeIdentifier(seed),
      pair,
      token: tokenForPair(pair),
      signal,
      setup: setupLabel(signal, regime),
      summary,
      invalidation,
      facts,
      entry,
      entryLow,
      entryHigh,
      stop,
      target,
      riskReward,
      riskPercent,
      confidence: confidence !== null && confidence >= 0 && confidence <= 100 ? confidence : null,
      status,
      timestamp
    };
  }

  function entryLabel(candidate) {
    if (candidate.entryLow !== null && candidate.entryHigh !== null) {
      return `${formatPrice(candidate.entryLow, "—")} ~ ${formatPrice(candidate.entryHigh, "—")}`;
    }
    return formatPrice(candidate.entry, "데이터 없음");
  }

  function riskRewardLabel(candidate) {
    return candidate.riskReward === null ? "데이터 없음" : `1:${formatPrice(candidate.riskReward, "—")}`;
  }

  function riskPercentLabel(candidate) {
    return candidate.riskPercent === null ? "데이터 없음" : `${formatPrice(candidate.riskPercent, "—")}%`;
  }

  function nextAction(candidate) {
    if (candidate.status.key === "planned") return "조건이 맞는지 체크리스트부터 확인하세요.";
    if (candidate.status.key === "no-trade") return "진입보다 관찰이 우선인 분석입니다.";
    return "계획이 완성될 때까지 관찰을 이어가세요.";
  }

  function actionCopy(candidate) {
    if (candidate.status.key === "planned") {
      return {
        title: "조건 충족 전까지 대기",
        copy: "체크리스트 기준이 모두 맞을 때만 계획을 검토하세요."
      };
    }
    if (candidate.status.key === "no-trade") {
      return {
        title: "진입 보류를 유지",
        copy: "현재 분석은 거래보다 관찰을 권합니다."
      };
    }
    return {
      title: "계획을 먼저 완성하세요",
      copy: "누락된 기준과 데이터를 확인하는 중입니다."
    };
  }

  function setBadge(element, status) {
    element.textContent = status.label;
    element.classList.remove("state-neutral", "state-planned", "state-watch", "state-no-trade");
    element.classList.add(status.className);
  }

  function readLocalJson(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      if (!value) return fallback;
      return JSON.parse(value);
    } catch (_error) {
      return fallback;
    }
  }

  function writeLocalJson(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function storageKey(kind, candidate) {
    return `${STORAGE_PREFIX}:${kind}:${safeIdentifier(candidate.id)}`;
  }

  async function requestJson(path, timeoutMs = REQUEST_TIMEOUT_MS) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) throw new Error("same-origin 요청만 허용됩니다.");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await window.fetch(`${url.pathname}${url.search}`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`읽기 요청 실패 (${response.status})`);
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function unpackReadResults(results) {
    const rawCandidates = [];
    const analysisResult = results[0];
    const historyResult = results[1];

    if (analysisResult.status === "fulfilled") {
      const payload = asObject(analysisResult.value);
      const latest = asObject(payload.result);
      const stored = asObject(payload.latest_result);
      if (Object.keys(latest).length) rawCandidates.push(latest);
      else if (Object.keys(stored).length) rawCandidates.push(stored);
    }

    if (historyResult.status === "fulfilled") {
      const entries = asObject(historyResult.value).entries;
      if (Array.isArray(entries)) rawCandidates.push(...entries.slice(0, 20));
    }

    const unique = [];
    const seen = new Set();
    rawCandidates.forEach((raw, index) => {
      const candidate = normalizeCandidate(raw, index);
      const signature = [candidate.pair, candidate.timestamp.value, candidate.entry, candidate.stop, candidate.target].join("|");
      if (!seen.has(signature)) {
        seen.add(signature);
        unique.push(candidate);
      }
    });
    return unique;
  }

  function updateConnection() {
    if (demoMode) {
      elements.connectionStatus.dataset.state = "partial";
      elements.connectionLabel.textContent = "샘플 모드";
      return;
    }
    if (state.sourceStatus.fulfilled === state.sourceStatus.total) {
      elements.connectionStatus.dataset.state = "online";
      elements.connectionLabel.textContent = "읽기 연결됨";
    } else if (state.sourceStatus.fulfilled > 0) {
      elements.connectionStatus.dataset.state = "partial";
      elements.connectionLabel.textContent = "일부 연결";
    } else {
      elements.connectionStatus.dataset.state = "offline";
      elements.connectionLabel.textContent = "연결 확인 필요";
    }
  }

  function renderHome() {
    const focus = state.candidates.find((candidate) => candidate.status.key === "planned") || state.candidates[0];
    elements.loadedCount.textContent = state.loading ? "—" : `${state.candidates.length}개`;
    elements.plannedCount.textContent = state.loading
      ? "—"
      : `${state.candidates.filter((candidate) => candidate.status.key === "planned").length}개`;

    if (!focus) {
      elements.focusCard.disabled = true;
      elements.focusToken.textContent = "—";
      elements.focusSymbol.textContent = state.loading ? "분석을 확인하고 있어요" : "표시할 분석이 없습니다";
      elements.focusSetup.textContent = state.loading ? "잠시만 기다려 주세요." : "읽기 전용 분석 기록이 아직 없어요.";
      elements.focusNext.textContent = state.loading
        ? "사용 가능한 데이터를 찾는 중입니다."
        : "샘플 화면은 주소에 ?demo=1을 추가해 확인할 수 있습니다.";
      setBadge(elements.focusStatus, {
        label: state.loading ? "불러오는 중" : "빈 상태",
        className: "state-neutral"
      });
      elements.homeRisk.textContent = "데이터 없음";
      elements.homeRr.textContent = "데이터 없음";
      elements.homeStop.textContent = "계획 미설정";
      return;
    }

    elements.focusCard.disabled = false;
    elements.focusCard.dataset.candidateId = focus.id;
    elements.focusToken.textContent = focus.token;
    elements.focusSymbol.textContent = focus.pair;
    elements.focusSetup.textContent = focus.setup;
    elements.focusNext.textContent = nextAction(focus);
    setBadge(elements.focusStatus, focus.status);
    elements.homeRisk.textContent = riskPercentLabel(focus);
    elements.homeRr.textContent = riskRewardLabel(focus);
    elements.homeStop.textContent = formatPrice(focus.stop, "계획 미설정");
  }

  function showSignalsState(title, copy) {
    elements.signalsState.hidden = false;
    const orbit = elements.signalsState.querySelector(".loading-orbit");
    orbit.hidden = !state.loading;
    elements.signalsState.querySelector("strong").textContent = title;
    elements.signalsState.querySelector("p").textContent = copy;
  }

  function renderSignalList() {
    elements.signalList.replaceChildren();
    const filtered = state.candidates.filter((candidate) => state.filter === "all" || candidate.status.key === state.filter);

    if (state.loading) {
      showSignalsState("저장된 분석을 확인하고 있어요", "한 항목을 불러오지 못해도 나머지는 계속 표시합니다.");
      elements.signalsSummary.textContent = "분석을 불러오는 중입니다.";
      return;
    }

    if (!state.candidates.length) {
      const failed = state.sourceStatus.rejected === state.sourceStatus.total;
      showSignalsState(
        failed ? "분석을 불러오지 못했습니다" : "아직 저장된 분석이 없습니다",
        failed
          ? "잠시 후 다시 열어보세요. 기존 대시보드는 영향을 받지 않습니다."
          : "실제 분석이 저장되면 이곳에 표시됩니다. 샘플 화면은 ?demo=1에서만 열립니다."
      );
      elements.signalsSummary.textContent = failed ? "읽기 연결을 확인해 주세요." : "표시 가능한 기록 0개";
      return;
    }

    if (!filtered.length) {
      showSignalsState("이 필터에 맞는 분석이 없습니다", "다른 상태 필터를 선택해 보세요.");
    } else {
      elements.signalsState.hidden = true;
    }

    const staleCount = state.candidates.filter((candidate) => candidate.timestamp.stale).length;
    const partialCopy = state.sourceStatus.rejected > 0 ? " · 일부 데이터 확인 실패" : "";
    const staleCopy = staleCount > 0 ? ` · 오래된 기록 ${staleCount}개` : "";
    elements.signalsSummary.textContent = `불러온 분석 ${state.candidates.length}개${partialCopy}${staleCopy}`;

    filtered.forEach((candidate) => {
      const fragment = elements.signalTemplate.content.cloneNode(true);
      const card = fragment.querySelector(".signal-card");
      const token = fragment.querySelector(".signal-token");
      const symbol = fragment.querySelector(".signal-symbol");
      const setup = fragment.querySelector(".signal-setup");
      const status = fragment.querySelector(".signal-state");
      const confidence = fragment.querySelector(".confidence-copy");
      const updated = fragment.querySelector(".updated-copy");
      const scenarioButton = fragment.querySelector(".scenario-button");

      card.dataset.state = candidate.status.key;
      token.textContent = candidate.token;
      symbol.textContent = candidate.pair;
      setup.textContent = candidate.setup;
      setBadge(status, candidate.status);
      fragment.querySelector('[data-field="entry"]').textContent = entryLabel(candidate);
      fragment.querySelector('[data-field="stop"]').textContent = formatPrice(candidate.stop, "계획 미설정");
      fragment.querySelector('[data-field="target"]').textContent = formatPrice(candidate.target, "계획 미설정");
      fragment.querySelector('[data-field="rr"]').textContent = riskRewardLabel(candidate);
      confidence.textContent = candidate.confidence === null ? "신뢰도 데이터 없음" : `신뢰도 ${Math.round(candidate.confidence)}%`;
      updated.textContent = candidate.timestamp.stale ? `${candidate.timestamp.label} · 오래된 분석` : candidate.timestamp.label;
      updated.dateTime = candidate.timestamp.value;
      scenarioButton.setAttribute("aria-label", `${candidate.pair} 시나리오 상세 보기`);
      scenarioButton.addEventListener("click", () => openScenario(candidate.id, "signals"));
      elements.signalList.append(fragment);
    });
  }

  function checklistState(candidate) {
    return asObject(readLocalJson(storageKey("checklist", candidate), {}));
  }

  function renderNotes(candidate) {
    const notes = [];
    if (candidate.summary) notes.push(candidate.summary);
    notes.push(...candidate.facts);
    if (candidate.invalidation) notes.push(`무효화 조건: ${candidate.invalidation}`);
    if (!notes.length) notes.push("확인 가능한 규칙이나 메모 데이터가 없습니다.");
    elements.scenarioNotes.replaceChildren();
    notes.slice(0, 4).forEach((note) => {
      const item = document.createElement("li");
      item.textContent = cleanText(note, "", 140);
      elements.scenarioNotes.append(item);
    });
  }

  function renderDetail() {
    const candidate = state.candidates.find((item) => item.id === state.selectedId) || null;
    if (!candidate) {
      elements.detailToken.textContent = "—";
      elements.detailSymbol.textContent = "시나리오 없음";
      elements.detailSetup.textContent = "시그널 목록에서 항목을 선택해 주세요.";
      setBadge(elements.detailState, { label: "관찰", className: "state-neutral" });
      elements.detailEntry.textContent = "데이터 없음";
      elements.detailStop.textContent = "계획 미설정";
      elements.detailTarget.textContent = "계획 미설정";
      elements.detailRr.textContent = "데이터 없음";
      elements.localMemo.value = "";
      elements.checklistInputs.forEach((input) => { input.checked = false; });
      return;
    }

    const action = actionCopy(candidate);
    elements.detailToken.textContent = candidate.token;
    elements.detailSymbol.textContent = candidate.pair;
    elements.detailSetup.textContent = candidate.setup;
    elements.detailUpdated.textContent = candidate.timestamp.stale
      ? `${candidate.timestamp.label} · 오래된 분석`
      : candidate.timestamp.label;
    setBadge(elements.detailState, candidate.status);
    elements.detailEntry.textContent = entryLabel(candidate);
    elements.detailStop.textContent = formatPrice(candidate.stop, "계획 미설정");
    elements.detailTarget.textContent = formatPrice(candidate.target, "계획 미설정");
    elements.detailRr.textContent = riskRewardLabel(candidate);
    elements.actionTitle.textContent = action.title;
    elements.actionCopy.textContent = action.copy;
    renderNotes(candidate);

    const checks = checklistState(candidate);
    elements.checklistInputs.forEach((input) => {
      input.checked = checks[input.dataset.checklist] === true;
    });
    elements.localMemo.value = cleanText(readLocalJson(storageKey("memo", candidate), ""), "", 300);
    const favorite = readLocalJson(storageKey("favorite", candidate), false) === true;
    elements.favoriteButton.setAttribute("aria-pressed", String(favorite));
    elements.favoriteButton.setAttribute("aria-label", favorite ? "관심 시나리오 표시 해제" : "관심 시나리오로 표시");
    elements.detailFeedback.textContent = "";
    elements.memoStatus.textContent = "서버로 전송되지 않습니다.";
  }

  function routeFromHash() {
    const value = window.location.hash.replace(/^#/, "");
    if (value.startsWith("detail/")) {
      return { screen: "detail", id: decodeURIComponent(value.slice(7)) };
    }
    if (value === "signals") return { screen: "signals", id: null };
    return { screen: "home", id: null };
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
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigate(route) {
    const nextHash = `#${route}`;
    if (window.location.hash === nextHash) renderRoute();
    else window.location.hash = nextHash;
  }

  function openScenario(id, origin) {
    state.selectedId = id;
    state.detailOrigin = origin;
    navigate(`detail/${encodeURIComponent(id)}`);
  }

  function initializeHomeChecks() {
    const checks = asObject(readLocalJson(HOME_CHECKS_KEY, {}));
    elements.homeCheckButtons.forEach((button) => {
      const active = checks[button.dataset.homeCheck] === true;
      button.setAttribute("aria-pressed", String(active));
      button.addEventListener("click", () => {
        const next = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(next));
        const current = asObject(readLocalJson(HOME_CHECKS_KEY, {}));
        current[button.dataset.homeCheck] = next;
        writeLocalJson(HOME_CHECKS_KEY, current);
        updateHomeCheckProgress();
      });
    });
    updateHomeCheckProgress();
  }

  function updateHomeCheckProgress() {
    const completed = elements.homeCheckButtons.filter((button) => button.getAttribute("aria-pressed") === "true").length;
    elements.homeCheckProgress.textContent = `${completed} / ${elements.homeCheckButtons.length}`;
  }

  function saveChecklist() {
    const candidate = state.candidates.find((item) => item.id === state.selectedId);
    if (!candidate) return;
    const values = {};
    elements.checklistInputs.forEach((input) => {
      values[input.dataset.checklist] = input.checked;
    });
    writeLocalJson(storageKey("checklist", candidate), values);
    elements.detailFeedback.textContent = "체크 상태를 이 브라우저에 저장했습니다.";
  }

  function bindEvents() {
    elements.navButtons.forEach((button) => {
      button.addEventListener("click", () => navigate(button.dataset.route));
    });
    elements.focusCard.addEventListener("click", () => {
      const id = elements.focusCard.dataset.candidateId;
      if (id) openScenario(id, "home");
    });
    elements.signalFilter.addEventListener("change", () => {
      state.filter = elements.signalFilter.value;
      renderSignalList();
    });
    elements.detailBack.addEventListener("click", () => {
      if (window.history.length > 1) window.history.back();
      else navigate(state.detailOrigin || "signals");
    });
    elements.checklistInputs.forEach((input) => input.addEventListener("change", saveChecklist));
    elements.localMemo.addEventListener("input", () => {
      const candidate = state.candidates.find((item) => item.id === state.selectedId);
      if (!candidate) return;
      const saved = writeLocalJson(storageKey("memo", candidate), elements.localMemo.value.slice(0, 300));
      elements.memoStatus.textContent = saved ? "이 기기에 저장했습니다." : "브라우저 저장소를 사용할 수 없습니다.";
    });
    elements.favoriteButton.addEventListener("click", () => {
      const candidate = state.candidates.find((item) => item.id === state.selectedId);
      if (!candidate) return;
      const next = elements.favoriteButton.getAttribute("aria-pressed") !== "true";
      writeLocalJson(storageKey("favorite", candidate), next);
      elements.favoriteButton.setAttribute("aria-pressed", String(next));
      elements.favoriteButton.setAttribute("aria-label", next ? "관심 시나리오 표시 해제" : "관심 시나리오로 표시");
    });
    elements.completeChecklist.addEventListener("click", () => {
      const missing = elements.checklistInputs.filter((input) => !input.checked);
      if (missing.length) {
        elements.detailFeedback.textContent = `아직 ${missing.length}개 항목을 확인하지 않았습니다. 충족되지 않은 조건에서는 관찰을 계속하세요.`;
        missing[0].focus();
        return;
      }
      elements.detailFeedback.textContent = "모든 기준을 확인했습니다. 주문 없이 관찰 상태를 유지합니다.";
    });
    window.addEventListener("hashchange", renderRoute);
  }

  async function loadData() {
    if (demoMode) {
      elements.demoBanner.hidden = false;
      state.candidates = DEMO_ENTRIES.map((entry, index) => normalizeCandidate(entry, index));
      state.sourceStatus = { fulfilled: 0, rejected: 0, total: 0 };
    } else {
      const results = await Promise.allSettled([
        requestJson("/api/analyze?include_latest=true"),
        requestJson("/api/analysis-history?limit=20")
      ]);
      state.sourceStatus = {
        fulfilled: results.filter((result) => result.status === "fulfilled").length,
        rejected: results.filter((result) => result.status === "rejected").length,
        total: results.length
      };
      state.candidates = unpackReadResults(results);
    }
    state.loading = false;
    updateConnection();
    renderHome();
    renderSignalList();
    renderRoute();
  }

  function initialize() {
    if (!window.location.hash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#home`);
    }
    initializeHomeChecks();
    bindEvents();
    renderHome();
    renderSignalList();
    renderRoute();
    loadData();
  }

  initialize();
})();
