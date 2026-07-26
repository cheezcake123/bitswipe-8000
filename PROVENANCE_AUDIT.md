# Provenance Audit

Audit date: 2026-07-26

Upstream repository referenced by the current project README:

https://github.com/likegyu/bitcoin-trading-manager

## Observed upstream state

- the current project README points to the upstream repository above
- that README contains a License section naming MIT
- a standalone upstream root LICENSE file with the exact copyright-and-permission notice was not recovered during this audit
- no upstream copyright holder, year, or permission notice has been inferred from the `MIT` label alone

## Current downstream state

- deployment branch has no root LICENSE file
- deployment branch has no root NOTICE file
- direct deployed Python dependencies were inventoried from the service virtual environment
- manually flagged transitive packages were reviewed separately in `THIRD_PARTY_AUDIT.md`
- browser dependencies identified in the legacy root page include Apache ECharts, Marked, and Inter via external CDN/font hosting
- repository-local media assets were inventoried in `STATIC_ASSET_AUDIT.md`
- all eight discovered repository-local media assets first appear in snapshot commit `1540ab4dbda206535a477623c9f39310d2ba2007`
- that snapshot commit does not establish authorship, source, or license for those assets, so their provenance remains UNKNOWN

## Conservative release gate

Do not invent an upstream copyright holder, year, or MIT notice. Before public source redistribution, recover the upstream notice from repository history or the maintainer, or obtain clarification from the upstream maintainer.

Do not assume the repository-local icons and images are project-owned merely because they are present in the repository. Establish their provenance or replace them with newly created assets whose authorship and licensing are documented.

## Remaining checks before public release

1. recover or independently verify the original upstream project's exact copyright/license notice
2. establish provenance or replace the eight UNKNOWN media assets listed in `STATIC_ASSET_AUDIT.md`
3. decide whether deployment/download artifacts redistribute third-party packages or only run them server-side
4. prepare the required third-party notices/license texts for artifacts actually redistributed
5. update README provenance before public release
6. decide the downstream project's own license separately from upstream attribution obligations and media ownership

This file records an engineering audit and is not legal advice.
