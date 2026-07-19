# Third-Party Dependency Audit

Audit date: 2026-07-19

This is an inventory aid, not a complete license bundle or legal conclusion.

## Direct deployed Python dependencies

Observed from the production service virtual environment:

- anthropic 0.105.2 — MIT
- openai 2.38.0 — Apache-2.0
- pandas 3.0.3 — BSD License
- numpy 2.4.6 — multi-license metadata including BSD-3-Clause, 0BSD, MIT, Zlib, and CC0-1.0
- requests 2.34.2 — Apache-2.0; installed distribution includes LICENSE and NOTICE files
- plotly 6.7.0 — MIT
- fastapi 0.136.3 — MIT
- uvicorn 0.48.0 — BSD-3-Clause
- python-dotenv 1.2.2 — BSD-3-Clause
- websockets 16.0 — BSD-3-Clause
- rank-bm25 0.2.2 — Apache 2.0 metadata
- yfinance 1.4.1 — Apache metadata

## Browser dependencies referenced by the legacy root page

- Apache ECharts — Apache-2.0 — loaded from jsDelivr
- Marked — MIT — loaded from jsDelivr
- Inter — SIL Open Font License 1.1 — loaded through Google Fonts

## External services and hosts

The page also references Binance API hosts, jsDelivr, Google Fonts, and bitswipe.xyz. Service terms and privacy requirements are separate from open-source license inventory and should be reviewed before public launch.

## Remaining work

- review transitive Python packages flagged by the expanded inventory script
- review static images, icons, screenshots, and copied frontend assets
- determine whether deployment artifacts will redistribute third-party binaries or only reference/install them
- prepare complete license texts and required notices for any redistributed components
- keep upstream project provenance separate from the downstream project's own license decision
