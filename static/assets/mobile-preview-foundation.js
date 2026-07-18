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

  const foundation = Object.freeze({
    product,
    capabilities,
    dataStates,
    valueSemantics
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
  applyProductIdentity();
})();
