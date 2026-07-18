(() => {
  "use strict";

  const product = Object.freeze({
    name: "BitSwipe",
    shortName: "BitSwipe",
    tagline: "Command Center",
    description: "감정적 매매를 줄이고 시장 근거, 시나리오 품질, 위험과 다음 행동을 분리해 보여 주는 트레이딩 의사결정 지원 도구"
  });

  const capabilityGroups = Object.freeze({
    public: Object.freeze([
      "market.data",
      "macro.context",
      "analysis",
      "candidates",
      "scenarios"
    ]),
    private: Object.freeze([
      "account.balances",
      "account.positions",
      "account.context"
    ])
  });

  const defaultAccess = Object.freeze({
    mode: "owner",
    public: true,
    private: true
  });

  const dataStates = Object.freeze({
    loading: "loading",
    unavailable: "unavailable",
    endpointFailure: "endpoint_failure",
    stale: "stale",
    ready: "ready",
    demo: "demo"
  });

  const valueSemantics = Object.freeze({
    zeroIsValue: true,
    missingValue: null
  });

  const capabilities = Object.freeze({
    groups: capabilityGroups,
    defaultAccess,
    canAccess(scope) {
      return scope === "private" ? defaultAccess.private : defaultAccess.public;
    }
  });

  const architectureVersion = "frontend-architecture-v1";

  const sectionContracts = Object.freeze({
    public: Object.freeze(["market", "analysis", "setups", "scenario", "journal"]),
    private: Object.freeze(["account"])
  });

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

  function isMissing(value) {
    return value === null || value === undefined || value === "";
  }

  function normalizeDataState(value) {
    const state = cleanText(value, "", 40).toLowerCase();
    if (state === "ready" || state === "online") return dataStates.ready;
    if (state === "demo") return dataStates.demo;
    if (state === "stale" || state === "reconnecting") return dataStates.stale;
    if (state === "loading") return dataStates.loading;
    if (state === "error" || state === "endpoint_failure" || state === "failed") return dataStates.endpointFailure;
    return dataStates.unavailable;
  }

  function normalizeSymbol(rawValue) {
    const raw = asObject(rawValue);
    const direct = cleanText(raw.symbol, "", 30).toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (direct) return direct;
    return cleanText(raw.pair_label, "", 30).toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function normalizePair(rawValue) {
    const raw = asObject(rawValue);
    const explicit = cleanText(raw.pair || raw.pair_label, "", 30).toUpperCase();
    if (/^[A-Z0-9]{2,15}\/[A-Z0-9]{2,10}$/.test(explicit)) return explicit;
    const symbol = normalizeSymbol(raw);
    const quote = ["USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD"].find((item) => symbol.endsWith(item));
    if (quote && symbol.length > quote.length) return symbol.slice(0, -quote.length) + "/" + quote;
    return symbol || "";
  }

  function gradeForScore(score) {
    if (!Number.isFinite(score)) return "—";
    if (score >= 85) return "A";
    if (score >= 75) return "B+";
    if (score >= 65) return "B";
    if (score >= 50) return "C";
    return "D";
  }

  function normalizeDirection(rawValue, analysisValue) {
    const direct = asObject(rawValue);
    if (cleanText(direct.key, "")) {
      return Object.freeze({
        key: cleanText(direct.key, "unknown", 20),
        label: cleanText(direct.label, "방향 미확인", 40)
      });
    }
    const combined = firstText([
      rawValue,
      asObject(analysisValue).view,
      asObject(analysisValue).signal
    ], "").toLowerCase();
    if (/(상방|매수|롱|buy|long|strong_buy)/i.test(combined)) return Object.freeze({ key: "long", label: "상방 우위" });
    if (/(하방|매도|숏|sell|short|strong_sell)/i.test(combined)) return Object.freeze({ key: "short", label: "하방 우위" });
    if (/(중립|관망|대기|hold|wait|neutral|no.?trade)/i.test(combined)) return Object.freeze({ key: "neutral", label: "중립·관찰" });
    return Object.freeze({ key: "unknown", label: "방향 미확인" });
  }

  function normalizeRisk(rawValue) {
    const raw = asObject(rawValue);
    const verdict = firstText([raw.verdict, raw.status, rawValue], "UNAVAILABLE").toUpperCase();
    return Object.freeze({
      verdict: verdict,
      warnings: Object.freeze(asArray(raw.warnings).map((item) => cleanText(item, "", 240)).filter(Boolean)),
      hardBlocks: Object.freeze(asArray(raw.hard_blocks || raw.hardBlocks).map((item) => cleanText(item, "", 240)).filter(Boolean))
    });
  }

  function normalizeMarket(rawValue) {
    const raw = asObject(rawValue);
    return Object.freeze({
      symbol: normalizeSymbol(raw),
      pair: normalizePair(raw),
      price: finiteNumber(raw.price, raw.close, asObject(raw.indicators).close),
      updatedAt: firstText([raw.updated_at, raw.last_update, raw.timestamp], ""),
      indicators: Object.freeze({ ...asObject(raw.indicators) }),
      charts: Object.freeze({ ...asObject(raw.charts) })
    });
  }

  function normalizeTradMarket(rawValue) {
    const raw = asObject(rawValue);
    return Object.freeze({
      price: finiteNumber(raw.price, raw.value),
      changePercent: finiteNumber(raw.chg_pct, raw.change_pct, raw.changePercent)
    });
  }

  function normalizeMacro(rawValue) {
    const raw = asObject(rawValue);
    const trad = Object.prototype.hasOwnProperty.call(raw, "_trad_markets")
      ? asObject(raw._trad_markets)
      : asObject(raw.trad_markets);
    const ibit = asObject(raw.IBIT_PX);
    const tradMarkets = {};
    ["SPX", "NDX", "VIX", "GOLD"].forEach((ticker) => {
      tradMarkets[ticker] = normalizeTradMarket(trad[ticker]);
    });
    return Object.freeze({
      tradMarkets: Object.freeze(tradMarkets),
      ibit: Object.freeze({
        value: finiteNumber(ibit.value, ibit.price),
        change24h: finiteNumber(ibit.change24h, ibit.change_24h)
      })
    });
  }

  function normalizePosition(rawValue) {
    const raw = asObject(rawValue);
    return Object.freeze({
      symbol: normalizeSymbol(raw),
      side: cleanText(raw.side, "", 20),
      entryPrice: finiteNumber(raw.entry_price, raw.entry),
      markPrice: finiteNumber(raw.mark_price, raw.mark),
      unrealizedPnl: finiteNumber(raw.unrealized_pnl, raw.pnl),
      leverage: finiteNumber(raw.leverage),
      liquidationPrice: finiteNumber(raw.liquidation_price, raw.liquidation),
      notional: finiteNumber(raw.notional),
      marginType: cleanText(raw.margin_type, "", 20),
      takeProfit: finiteNumber(raw.tp_price, raw.take_profit),
      stopLoss: finiteNumber(raw.sl_price, raw.stop_loss)
    });
  }

  function normalizeAccount(rawValue) {
    const raw = asObject(rawValue);
    return Object.freeze({
      updatedAt: firstText([raw.updated_at, raw.timestamp], ""),
      equity: finiteNumber(raw.account_equity, raw.equity),
      walletBalance: finiteNumber(raw.wallet_balance),
      availableBalance: finiteNumber(raw.available_balance),
      todayPnl: finiteNumber(raw.today_total_pnl, raw.today_pnl),
      todayPnlPercent: finiteNumber(raw.today_pnl_pct),
      openPositionCount: finiteNumber(raw.open_position_count),
      openPositionNotional: finiteNumber(raw.open_position_notional),
      effectiveLeverage: finiteNumber(raw.effective_leverage),
      configuredLeverage: finiteNumber(raw.configured_leverage),
      leverageDisplay: cleanText(raw.leverage_display, "", 120),
      positions: Object.freeze(asArray(raw.open_positions || raw.positions).map(normalizePosition))
    });
  }

  function normalizeScenario(rawValue, index) {
    const raw = asObject(rawValue);
    const analysisJson = Object.keys(asObject(raw.analysisJson)).length ? asObject(raw.analysisJson) : asObject(raw.analysis_json);
    const trade = asObject(analysisJson.trade);
    const tradeLevels = asObject(raw.trade_levels);
    const riskGuard = Object.keys(asObject(raw.riskGuard)).length ? asObject(raw.riskGuard) : asObject(raw.risk_guard);
    const risk = Object.keys(asObject(raw.risk)).length ? normalizeRisk(raw.risk) : normalizeRisk(riskGuard);
    const scoreValue = finiteNumber(raw.score, raw.quality, analysisJson.confidence, raw.confidence);
    const score = scoreValue === null ? null : Math.min(100, Math.max(0, scoreValue));
    const direction = normalizeDirection(
      Object.keys(asObject(raw.direction)).length ? raw.direction : firstText([raw.signal, analysisJson.signal], ""),
      analysisJson
    );
    const entry = finiteNumber(raw.entry, trade.entry, tradeLevels.entry, riskGuard.entry_price);
    const stop = finiteNumber(raw.stop, trade.stop, tradeLevels.stop, riskGuard.stop_price);
    const target = finiteNumber(raw.target, trade.target, tradeLevels.target, riskGuard.target_price);
    const leverage = finiteNumber(raw.leverage, trade.leverage, raw.claude_leverage, riskGuard.leverage);
    const facts = asArray(raw.facts).length ? asArray(raw.facts) : asArray(analysisJson.key_facts);
    const inferences = asArray(raw.inferences).length ? asArray(raw.inferences) : asArray(analysisJson.inferences);
    const counterEvidence = asArray(raw.counterScenario).length ? asArray(raw.counterScenario) : asArray(analysisJson.counter_scenario);
    const actions = asObject(analysisJson.actions);
    return Object.freeze({
      id: firstText([raw.id, raw.scenario_id], "scenario-" + String(index || 0)),
      symbol: normalizeSymbol(raw),
      pair: normalizePair(raw),
      timeframe: firstText([raw.timeframe, raw.tf], ""),
      direction: direction,
      grade: firstText([raw.grade], gradeForScore(score)),
      quality: score,
      risk: risk,
      status: firstText([raw.status, analysisJson.status], dataStates.unavailable),
      timestamp: firstText([asObject(raw.timestamp).raw, raw.timestamp, raw.created_at], ""),
      plan: Object.freeze({
        entry: entry,
        stop: stop,
        target: target,
        riskReward: finiteNumber(raw.rr, riskGuard.risk_reward_ratio, trade.risk_reward_ratio, tradeLevels.risk_reward_ratio),
        leverage: leverage,
        riskPercent: finiteNumber(riskGuard.risk_percent, trade.risk_percent),
        invalidation: firstText([raw.invalidation, analysisJson.invalidation], "")
      }),
      evidence: Object.freeze({
        supporting: Object.freeze(facts.map((item) => cleanText(item, "", 260)).filter(Boolean)),
        interpretation: Object.freeze(inferences.map((item) => cleanText(item, "", 260)).filter(Boolean)),
        counter: Object.freeze(counterEvidence.map((item) => cleanText(item, "", 260)).filter(Boolean))
      }),
      actions: Object.freeze({
        aggressive: firstText([asObject(raw.actions).aggressive, actions.aggressive], ""),
        conservative: firstText([asObject(raw.actions).conservative, actions.conservative], "")
      })
    });
  }

  function normalizeSourceStates(rawValue) {
    const raw = asObject(rawValue);
    const normalized = {};
    Object.keys(raw).forEach((key) => {
      normalized[key] = normalizeDataState(raw[key]);
    });
    return Object.freeze(normalized);
  }

  function normalizeStreamStates(rawValue) {
    const raw = asObject(rawValue);
    return Object.freeze({
      market: normalizeDataState(raw.market),
      account: normalizeDataState(raw.account)
    });
  }

  function getSectionScope(section) {
    const name = cleanText(section, "", 40).toLowerCase();
    if (sectionContracts.private.includes(name)) return "private";
    if (sectionContracts.public.includes(name)) return "public";
    return "unknown";
  }

  function buildViewModel(inputValue) {
    const input = asObject(inputValue);
    const scenarios = asArray(input.candidates).map((item, index) => normalizeScenario(item, index));
    const analysis = isMissing(input.analysis) ? null : normalizeScenario(input.analysis, 0);
    const streamStates = normalizeStreamStates(input.streamState);
    return Object.freeze({
      schemaVersion: architectureVersion,
      mode: input.demo === true ? dataStates.demo : "live",
      public: Object.freeze({
        sections: sectionContracts.public,
        market: normalizeMarket(input.market),
        macro: normalizeMacro(input.macro),
        analysis: analysis,
        setups: Object.freeze(scenarios),
        context: Object.freeze({
          activeSymbol: cleanText(input.activeSymbol, "", 30),
          currentTimeframe: cleanText(input.currentTf, "1h", 10),
          sources: normalizeSourceStates(input.sources),
          marketStream: streamStates.market
        })
      }),
      private: Object.freeze({
        sections: sectionContracts.private,
        account: normalizeAccount(input.account),
        context: Object.freeze({
          accountStream: streamStates.account
        })
      })
    });
  }

  const architecture = Object.freeze({
    version: architectureVersion,
    sections: sectionContracts,
    normalizeDataState,
    normalizeMarket,
    normalizeMacro,
    normalizeAccount,
    normalizeScenario,
    normalizeSourceStates,
    normalizeStreamStates,
    buildViewModel,
    getSectionScope,
    isMissing
  });

  const foundation = Object.freeze({
    product,
    capabilities,
    dataStates,
    valueSemantics,
    architecture: Object.freeze({
      version: architectureVersion,
      sections: sectionContracts
    })
  });

  function applyProductIdentity() {
    document.title = product.name + " 시나리오 커맨드 센터";

    document.querySelectorAll("[data-product-name]").forEach((element) => {
      element.textContent = product.name;
    });
    document.querySelectorAll("[data-product-short-name]").forEach((element) => {
      element.textContent = product.shortName;
    });
    document.querySelectorAll("[data-product-tagline]").forEach((element) => {
      element.textContent = product.tagline;
    });
    document.querySelectorAll("[data-product-description]").forEach((element) => {
      element.textContent = product.description;
    });
    document.querySelectorAll("[data-product-home-label]").forEach((element) => {
      element.setAttribute("aria-label", product.name + " 시나리오 홈");
    });
  }

  window.MobilePreviewFoundation = foundation;
  window.MobilePreviewArchitecture = architecture;
  applyProductIdentity();
})();