# Third-Party Dependency Audit

Audit date: 2026-07-26

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

## Transitive packages flagged for manual review

Observed by the expanded production virtual-environment inventory:

- certifi 2026.5.20 — MPL-2.0; installed distribution includes `certifi-2026.5.20.dist-info/licenses/LICENSE`
- tqdm 4.67.3 — package metadata reports `MPL-2.0 AND MIT`; the installed distribution inventory did not list a LICENSE/COPYING/NOTICE file
- peewee 4.0.6 — installed package metadata reports UNKNOWN, but the installed distribution includes `peewee-4.0.6.dist-info/licenses/LICENSE`

Version-specific upstream verification for Peewee 4.0.6 confirmed that its tagged LICENSE is an MIT-form permission notice with a Charles Leifer copyright notice. The installed UNKNOWN metadata is therefore treated as incomplete package metadata, not evidence of a proprietary or unknown license.

MPL-2.0 is treated here as a review trigger, not as evidence that unrelated downstream application files inherit MPL. Obligations depend on the covered files, modifications, and the artifacts actually distributed.

## Browser dependencies referenced by the legacy root page

- Apache ECharts — Apache-2.0 — loaded from jsDelivr
- Marked — MIT — loaded from jsDelivr
- Inter — SIL Open Font License 1.1 — loaded through Google Fonts

## Repository-local media

Repository-local icons and images are not third-party dependencies merely because they are static files. Their ownership/provenance is audited separately in `STATIC_ASSET_AUDIT.md`.

The current audit found eight such files, all first captured in the same repository snapshot commit. Repository evidence did not establish their creator, source, or license, so they remain UNKNOWN until provenance is established or the assets are replaced.

## External services and hosts

The page also references Binance API hosts, jsDelivr, Google Fonts, and bitswipe.xyz. Service terms and privacy requirements are separate from open-source license inventory and should be reviewed before public launch.

## Remaining work

- determine whether deployment or download artifacts will redistribute third-party packages rather than only run them server-side
- prepare complete license texts and required notices for components actually redistributed
- recover or independently verify the original upstream project's exact copyright/license notice before choosing a downstream root LICENSE
- establish provenance or replace UNKNOWN repository-local media assets
- keep upstream project provenance, dependency licenses, and downstream media ownership separate
