# Single-page matching contract

## Minimum records

### IDF 100 / 120

Store the stable review ID, record type, original line span, raw fields, coordinates if present, bore, cut length if present, and predecessor/successor typed records. Assign IDs in source order before scoring.

### DXF typed graph

Store source filename, all source handles, vector anchor/endpoint coordinates, semantic class, endpoint roles, incident component classes, bore if available, and evidence image path. Use source+handle as identity; a handle alone is not globally unique.

## Score order

Use lexicographic topology evidence before scalar length:

1. Exact endpoint/component signature.
2. Degree and branch-order preservation.
3. Turn and elbow sequence.
4. Bore transition.
5. Relative-length ratio within the same matched neighbourhood.

Do not use title-block position, annotation text position, materials table, cut-pipe table, or IDF-to-DXF absolute-coordinate projection as a score feature.

## Review images

The full IDF image must show every `I###` and `W###`. The full DXF overlay must show its matched IDs at vector anchors, without covering the original geometry. A local pair must show the same candidate relation on both sides and state the confidence plus the decisive topology evidence.

## Extension boundary

Multiple DXF sheets, continuation marks, same-line ISO splits, and a single IDF whose graph spans more than one page are out of scope for this version. First partition and prove page membership; then apply this single-page contract independently to each page.
