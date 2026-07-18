(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function firstDefinedNumber(values) {
    for (const value of values) {
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (typeof value === "string" && value.trim() !== "") {
        const parsed = Number(value.replace(/,/g, ""));
        if (Number.isFinite(parsed)) return parsed;
      }
    }
    return null;
  }

  function normalizeMacroPayload(rawValue) {
    const raw = asObject(rawValue);
    const normalized = { ...raw };

    if (Object.prototype.hasOwnProperty.call(raw, "_trad_markets")) {
      normalized.trad_markets = asObject(raw._trad_markets);
    } else {
      normalized.trad_markets = asObject(raw.trad_markets);
    }

    const ibit = asObject(raw.IBIT_PX);
    const normalizedIbit = { ...ibit };
    const confirmedChange24h = firstDefinedNumber([ibit.change24h]);
    if (confirmedChange24h !== null && normalizedIbit.change_24h === undefined) {
      normalizedIbit.change_24h = confirmedChange24h;
    }
    normalized.IBIT_PX = normalizedIbit;

    return normalized;
  }

  window.fetch = async function bitswipeMacroCompatibleFetch(input, init) {
    const requestUrl = input instanceof Request ? input.url : String(input);
    const url = new URL(requestUrl, window.location.origin);
    const response = await nativeFetch(input, init);

    if (url.origin !== window.location.origin || url.pathname !== "/api/macro" || !response.ok) {
      return response;
    }

    let payload;
    try {
      payload = await response.clone().json();
    } catch (_error) {
      return response;
    }

    const normalized = normalizeMacroPayload(payload);
    const headers = new Headers(response.headers);
    headers.set("content-type", "application/json; charset=utf-8");
    headers.delete("content-length");

    return new Response(JSON.stringify(normalized), {
      status: response.status,
      statusText: response.statusText,
      headers
    });
  };
})();
