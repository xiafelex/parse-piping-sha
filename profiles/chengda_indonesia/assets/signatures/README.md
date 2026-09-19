# Optional signature assets

Signature images are deliberately not versioned. If a drawing profile needs
them, place locally authorized transparent PNG files in this directory. The
file stem must match the personnel code read from the SHA revision record, for
example `ABC.png`; an optional `ABC-manual.png` takes precedence.

If no matching image exists, the renderer simply omits the signature image.
