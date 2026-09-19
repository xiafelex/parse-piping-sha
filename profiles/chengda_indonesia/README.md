# Chengda Indonesia SHA profile

This directory freezes the Chengda Indonesia variant of the SHA renderer. It
is isolated from the repository's generic renderer and from the Bluestar
project profile so project-specific layout rules can evolve independently.

## Included capabilities

- SHA-to-SVG/PDF reconstruction with Chengda title-block, revision, watermark,
  material-table, cut-pipe-table, symbol, cloud and signature-placement rules.
- PCF/SHA weld association using UCI, endpoint coordinates and topology.
- Construction-weld numbering for the `100200` profile and EP3D weld numbering
  for the `000200fw` profile.
- Blue management callouts for supports, valves and flanges.
- Batch audit output listing unmatched identifiers and identifiers resolved by
  the unique-remaining rule.

Project drawings, PCF files, spreadsheets, PDFs, generated output and personal
signature images are intentionally excluded from Git.

## Installation

From this directory:

```powershell
python -m pip install -r requirements.txt
npm install
npx playwright install chromium
```

## Plain SHA-to-PDF conversion

```powershell
python .\convert_chengda_sha_to_pdf.py `
  --input-dir D:\path\to\sha `
  --output-dir D:\path\to\pdf `
  --force
```

## Numbered drawing conversion

EP3D numbering (`000200fw`):

```powershell
python .\convert_chengda_weld_sha_to_pdf.py `
  --profile 000200fw `
  --sha-dir D:\path\to\sha `
  --pcf-dir D:\path\to\pcf `
  --support-table D:\path\to\support.csv `
  --valve-table D:\path\to\valve.csv `
  --flange-table D:\path\to\flange.csv `
  --output-dir D:\path\to\pdf `
  --audit-dir D:\path\to\audit `
  --workers 2 --force
```

Construction numbering (`100200`) additionally supplies the weld workbook:

```powershell
python .\convert_chengda_weld_sha_to_pdf.py `
  --profile 100200 `
  --sha-dir D:\path\to\sha `
  --pcf-dir D:\path\to\pcf `
  --weld-table D:\path\to\welds.xlsx `
  --weld-number-column 施工焊口号 `
  --support-table D:\path\to\support.csv `
  --valve-table D:\path\to\valve.csv `
  --flange-table D:\path\to\flange.csv `
  --output-dir D:\path\to\pdf `
  --audit-dir D:\path\to\audit `
  --workers 2 --force
```

Use `--plan-only` to inspect discovered work without producing PDFs and
`--include` with comma-separated single-sheet drawing numbers for a bounded
sample run. The renderer never modifies source SHA or PCF files.

## Optional signature images

See [`assets/signatures/README.md`](assets/signatures/README.md). Only locally
authorized images should be placed there; all PNG files in that directory are
ignored by Git.

## Verification

```powershell
python -m compileall -q .
python -m pytest -q tests
```
