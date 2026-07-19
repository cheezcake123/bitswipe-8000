# Provenance Audit

Audit date: 2026-07-19

Upstream repository referenced by the current project README:

https://github.com/likegyu/bitcoin-trading-manager

## Observed upstream state

- the upstream README contains a License section naming MIT
- the upstream repository root currently does not show a standalone LICENSE file
- an exact upstream copyright and permission notice has not yet been recovered

## Current downstream state

- deployment branch has no root LICENSE file
- deployment branch has no root NOTICE file
- direct deployed Python dependencies were inventoried from the service virtual environment
- direct dependency metadata observed only permissive license families: MIT, Apache variants, BSD variants, plus NumPy's multi-license metadata
- browser dependencies identified in the legacy root page include Apache ECharts, Marked, and Inter via external CDNs/font hosting
- exact transitive Python dependency review is still pending
- static images/icons and other copied asset provenance still need review

## Conservative release gate

Do not invent an upstream copyright holder, year, or MIT notice. Before public source redistribution, recover the upstream notice from repository history or the maintainer, or obtain clarification from the upstream maintainer.

## Next checks

1. run the expanded inventory script against the deployed service virtual environment and review all flagged transitive packages
2. finish browser dependency and static asset provenance inventory
3. prepare a third-party notice/compliance bundle appropriate to the actual distribution model
4. update README provenance before public release
5. decide the downstream project's own license separately from the upstream attribution obligations

This file records an engineering audit and is not legal advice.
