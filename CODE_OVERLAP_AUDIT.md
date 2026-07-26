# Code Overlap Audit

Audit date: 2026-07-26

Compared repositories:

- downstream: `cheezcake123/bitswipe-8000` at `9ba492260b79695cb0a66390ad766d542bb409d1`
- upstream: `likegyu/bitcoin-trading-manager` current default branch shallow clone as observed on 2026-07-26

## Method

A read-only same-path line-overlap scan compared `.py`, `.html`, `.css`, `.js`, and `.sh` files present at the same relative path in both repositories.

Each file was normalized by trimming whitespace, collapsing repeated whitespace, and dropping empty lines. Duplicate normalized lines were counted with multiplicity. The reported overlap is:

`common normalized lines / max(downstream normalized lines, upstream normalized lines)`

This metric is an engineering similarity signal only. It is not a legal test for copyright infringement, substantial similarity, authorship, originality, or license compliance. It also does not detect moved/renamed files or semantically equivalent rewritten code.

## High-overlap files (>= 70%)

### 100.0%

- `time_utils.py`
- `tests/test_signal_processing.py`
- `tests/test_reflection.py`
- `tests/test_market_context.py`
- `tests/test_macro_history.py`
- `tests/test_judge.py`
- `tests/test_indicators_rv.py`
- `tests/test_consistency_check.py`
- `tests/test_analysis_context.py`
- `tests/test_account_history.py`
- `static/guide.html`
- `macro_history.py`
- `indicators.py`
- `data_fetcher.py`
- `analysis_context.py`
- `agents/situation_digest.py`
- `agents/signal_processing.py`
- `agents/risk_prompts.py`
- `agents/prompts.py`
- `agents/pipeline.py`
- `agents/memory.py`
- `agents/delta_context.py`
- `agents/__init__.py`
- `account_history.py`

### 70.0%–99.9%

| Overlap | Path | Downstream lines | Upstream lines | Common normalized lines |
| ---: | --- | ---: | ---: | ---: |
| 99.2% | `macro_fetcher.py` | 639 | 634 | 634 |
| 98.9% | `account_context.py` | 552 | 548 | 546 |
| 98.7% | `agents/reflection.py` | 373 | 370 | 368 |
| 98.3% | `agents/consistency_check.py` | 299 | 296 | 294 |
| 97.7% | `market_context.py` | 514 | 511 | 502 |
| 93.5% | `run.sh` | 31 | 31 | 29 |
| 89.3% | `agents/judge.py` | 247 | 262 | 234 |
| 87.6% | `agents/risk_triad.py` | 218 | 234 | 205 |
| 86.2% | `agents/debate.py` | 209 | 225 | 194 |
| 79.6% | `server.py` | 2389 | 1939 | 1901 |
| 73.3% | `analyzer.py` | 1944 | 1449 | 1425 |

## Additional same-path overlap observed

| Overlap | Path |
| ---: | --- |
| 67.9% | `static/index.html` |
| 67.0% | `tests/test_analyzer_structured.py` |
| 57.5% | `config.py` |
| 32.0% | `deploy/lightsail/bootstrap_ubuntu.sh` |

## Engineering conclusion

The downstream project has substantial implementation continuity with the upstream repository. Many core modules are unchanged at the normalized-line level, and several central runtime files remain highly similar.

Accordingly:

- do not describe the present codebase as independently rewritten from scratch
- visual redesign, product renaming, or new features do not by themselves remove provenance/license obligations for retained upstream code
- future independent reimplementation should be tracked file-by-file and validated against behavior/tests rather than treated as a cosmetic rename
- upstream attribution/license clearance remains separate from the downstream project's future brand and own-license decision

## Recommended transformation order

For reducing implementation dependence while preserving service stability, use small behavior-preserving slices rather than a mass rewrite:

1. replace UNKNOWN local icons/images with documented original assets
2. rewrite low-coupling utility/data modules behind tests (`time_utils.py`, `data_fetcher.py`, selected indicator/history helpers)
3. replace agent prompt/memory/pipeline internals behind stable interfaces and regression tests
4. refactor `analyzer.py` into new decision-domain modules with explicit contracts
5. refactor `server.py` last, routing public/private surfaces through the newer product architecture
6. rerun the overlap audit after each tranche

A lower overlap score alone is not the objective. The objective is independently designed code with preserved behavior, explicit provenance, tests, and clear ownership of new implementation.

This is an engineering inventory/provenance audit, not legal advice or a legal conclusion.
