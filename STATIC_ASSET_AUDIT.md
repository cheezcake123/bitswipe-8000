# Static Asset Provenance Audit

Audit date: 2026-07-26

This is an inventory/provenance audit, not legal advice or a legal conclusion.

## Scope

The deployed repository was scanned read-only for image, icon, font, and other static media files. The scan recorded path, extension, byte size, SHA-256, first repository commit when available, and nearby URL/reference evidence.

## Repository assets found

All eight discovered media assets first appear in commit `1540ab4dbda206535a477623c9f39310d2ba2007`, whose commit message is `chore: capture current deployed BitSwipe 8000 state`.

| Asset | Bytes | SHA-256 | Provenance status |
| --- | ---: | --- | --- |
| `static/apple-touch-icon.png` | 1250 | `30a6b812a2983bc15105deec3595f1c288a4fa8c51cafdd14502e813865fed47` | UNKNOWN |
| `static/favicon-16x16.png` | 225 | `48c9e59a5b85840b42cd763e6fe056c40a5fd3c7dc90ffb38a3f5cc944cf653f` | UNKNOWN |
| `static/favicon-32x32.png` | 309 | `a6b031cf0e3429f255d4ea350e97bb75adca8e3d5372f2a2c774adb1ab7f81ff` | UNKNOWN |
| `static/favicon.ico` | 989 | `6c3eafbf8f7e668bff5b3e5cdc0d90966dc5f03003c55472945b4e643fbacf1b` | UNKNOWN |
| `static/favicon.svg` | 1525 | `f849dda45d7809f677d17f367c21c4c212bc11241e2e79b2f06c4cf3368cbb42` | UNKNOWN |
| `static/icon-192.png` | 1390 | `80b2ef17fc315333cd92b8630c91b2322e2bd5acc34cf992ea96383f5753fca6` | UNKNOWN |
| `static/icon-512.png` | 4184 | `395829787f0d509295f0d16a96b6f0bef5cc273fc7fea2791f1719bb9c7aa7a5` | UNKNOWN |
| `static/og-image.png` | 36959 | `f717bff14adfbf7a73bd3f0deff0ee075ff93235a2b7857de73c4752f98477ca` | UNKNOWN |

## Evidence and interpretation

The introduction commit is a snapshot/capture commit, not a provenance record. It does not establish who created the assets, where they came from, or what license applies to them.

`static/favicon.svg` is repository-local vector markup containing a dark rounded-square background, simple candlestick-style shapes, and a Bitcoin symbol. The file contains Korean descriptive comments but no author, source URL, copyright notice, or license notice. That structure is evidence about the file contents only; it is not evidence of ownership.

The PNG/ICO files were introduced in the same snapshot commit. Repository-local evidence found by the scan does not establish whether they were generated from `favicon.svg`, created independently, or copied from another source. They therefore remain `UNKNOWN` rather than being labeled project-created.

The `bitswipe.xyz` URLs found near these files are runtime/site references to the deployed assets and are not provenance or licensing evidence.

## Browser dependencies

The legacy root page references these externally hosted browser dependencies:

- Apache ECharts via jsDelivr — Apache-2.0
- Marked via jsDelivr — MIT
- Inter via Google Fonts — SIL Open Font License 1.1

These are dependency/source records and are separate from the ownership status of the eight repository-local media assets above.

## Upstream project provenance

The current project README references `https://github.com/likegyu/bitcoin-trading-manager` and states `MIT` in its License section. During this audit, an exact upstream copyright-and-permission notice was still not recovered from a standalone root license file. No downstream root `LICENSE` should be invented from the README label alone.

## Public-release blockers remaining

1. Recover or independently verify the original upstream project's exact copyright/license notice before choosing a downstream root license or redistributing substantial upstream code.
2. Establish provenance or replace the eight `UNKNOWN` repository-local media assets with newly created assets whose authorship and license are documented.
3. If downloadable/deployment artifacts redistribute third-party packages, prepare the license texts and notices required for the actual redistributed artifacts.
4. Keep dependency licenses, upstream-code provenance, and downstream branding/media ownership as separate records.

## Tooling

`scripts/static_asset_inventory.py` performs the read-only local inventory used for this audit. It uses the Python standard library, makes no network calls, does not mutate files, and does not read `.env` contents.
