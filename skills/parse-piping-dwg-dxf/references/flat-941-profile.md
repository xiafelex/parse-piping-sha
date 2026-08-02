# Flat 0.6 DXF profile

Apply this profile only after inventory confirms two-point 0.6-wide pipe `POLYLINE` entities in the drawing area.

## Evidence and constraints

| Semantic class | Required vector evidence | Topology action | Mandatory exclusion |
| --- | --- | --- | --- |
| Raw pipe | Two-point 0.6-wide polyline | Candidate skeleton edge | Never emit directly as final straight |
| Weld | Compact closed body contacting the pipe; six-sided hatch weld also has local crossing fills | Boundary node | Owned hatch body cannot be flange/reducer/valve |
| Flow arrow | Open wedge bridges two collinear pipe vectors | Contract bridge | Never split a run |
| Support | Opposite paired short pipe-parallel strokes at a join, or symmetric terminal pair at an unsplit endpoint | Hard split | Reject text/leader/one-sided tick |
| Elbow | Two weld boundaries joined by continuous turning raw-pipe group | Remove group from final straight graph | Do not truncate it at a short internal vector |
| Branch outlet | Closed 8V body, two distinct weld-axis/edge-midpoint contacts, branch topology | Component node and pipe split | Do not use 8V count or body centre alone |
| Flat flange | Plate edge/weld-axis coincidence | Component node and pipe split | Reject taper and owned hatch weld |
| Long-neck flange | Plate physically contacts trapezoid neck; neck/weld relation | One flange component | Reject centroid-nearest far-side plate |
| Reducer | Full taper with unequal pipe interfaces and outer parallel pair | Component node and pipe split | Reject local parallelogram-only test |
| Valve | Remaining inline body after adjacent flange groups removed | Component node and pipe split | Do not rank before flange/reducer exclusion |
| Tee | Three weld-empty runs share one empty junction; all other ends weld | Tee node | Reject two-leg bend/untyped junction |

## Calibrated tolerances

- Branch body edge midpoint to weld-axis centre: local ≤0.82 drawing units. The tolerance only applies after the closed-8V, two-distinct-weld, symmetric-edge gates pass.
- Flat flange plate edge midpoint to weld-axis centre: local ≤0.8 drawing units after hatch-weld and taper exclusions.
- Terminal support: short strokes must be pipe-parallel, opposite normal sides, symmetric around the endpoint; observed normal offset 1–3.5 and along offset ≤1.2 drawing units.

## Validation state

The profile has forward-validated positives for raw pipe, both weld families, arrows, elbows, supports (including terminal form), branch outlets, flat and long-neck flanges, reducers, valves, and weld-star tees. It is a project/export profile, not a claim of universal CAD symbol conventions. Continue to report unclassified bodies and untyped pipe endpoints separately from final counts.
