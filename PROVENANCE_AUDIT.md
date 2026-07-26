# Provenance Audit

Audit date: 2026-07-26

Upstream repository referenced by the current project README:

https://github.com/likegyu/bitcoin-trading-manager

## Verified upstream Git-history evidence

A full local clone/history scan of the upstream repository was run on 2026-07-26.

- upstream root commit: `7026e7b3cd8f22456bc0f4abae26ca223821f0eb`
- root commit author/committer recorded by Git: `gyu <gyu@gyuui-MacBookAir.local>`
- root commit timestamp: `2026-03-30T11:02:09+09:00`
- root commit subject: `feat: BTC signal analyzer with macro indicators`
- README introduction commit: `98f9210a17fe9f75819bd91de3a080c4920cb585`
- README timestamp: `2026-03-30T11:16:07+09:00`
- that first README already contained both a `license-MIT` badge and a `## License` section whose only license text was `MIT`
- scanning every revision found no path named `LICENSE`, `COPYING`, or `NOTICE`
- scanning all revisions found no repository text containing `Copyright`, `Permission is hereby granted`, or `MIT License`

The Git author name/email above is commit metadata only. This audit does not treat it as proof of the legal copyright holder or as a substitute for a copyright-and-permission notice.

## What the history does and does not establish

The repository history positively establishes that the upstream project described itself as `MIT` from the first README commit.

The repository history does **not** provide the complete MIT permission text, a copyright line, a copyright year, or a standalone license/notice file. Therefore this project must not invent those missing fields or manufacture an upstream license notice from assumptions.

## Current downstream state

- repository `cheezcake123/bitswipe-8000` is currently public on GitHub
- deployment branch has no root LICENSE file
- deployment branch has no root NOTICE file
- direct deployed Python dependencies were inventoried from the service virtual environment
- manually flagged transitive packages were reviewed separately in `THIRD_PARTY_AUDIT.md`
- browser dependencies identified in the legacy root page include Apache ECharts, Marked, and Inter via external CDN/font hosting
- repository-local media assets were inventoried in `STATIC_ASSET_AUDIT.md`
- all eight discovered repository-local media assets first appear in snapshot commit `1540ab4dbda206535a477623c9f39310d2ba2007`
- that snapshot commit does not establish authorship, source, or license for those assets, so their provenance remains UNKNOWN

## Conservative release gate

Do not invent an upstream copyright holder, year, or full MIT notice. The next reliable path is to obtain the exact intended license/copyright notice directly from the upstream maintainer or another verifiable upstream artifact that contains it.

Do not assume the repository-local icons and images are project-owned merely because they are present in the repository. Establish their provenance or replace them with newly created assets whose authorship and licensing are documented.

Because the downstream GitHub repository is already public, these are current provenance/compliance issues rather than only future release checks.

## Remaining checks

1. obtain or independently verify the upstream project's exact copyright/license notice
2. establish provenance or replace the eight UNKNOWN media assets listed in `STATIC_ASSET_AUDIT.md`
3. decide whether deployment/download artifacts redistribute third-party packages or only run them server-side
4. prepare the required third-party notices/license texts for artifacts actually redistributed
5. update README provenance
6. decide the downstream project's own license separately from upstream attribution obligations and media ownership

This file records an engineering inventory/provenance audit and is not legal advice or a legal conclusion.
