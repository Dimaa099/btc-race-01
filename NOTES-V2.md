# V2 iteration notes — btc-race-24h-20261115-0828

## VERDICT: V2 QUALIFIES

main.py in this repo is now the v2 revision. It differs from the submitted
v1 ("Patient Reversal", commit 6825f45b) in exactly two parameters:

- TIME_STOP_BARS 180 -> 120 (exit a position held 120 bars that is not
  above its average cost)
- COOLDOWN_BARS 20 -> 35 (bars sat out after any exit)

Everything else — entry logic, sizing, hard stop, trailing stop,
take-profit, slippage cap — is byte-identical to v1. dev-results.md and
README.md were updated to the v2 figures; NOTES.md remains the v1 record.

### Final validation of the shipped v2 (this session, grade_dev.py + stress)

| figure | v1 (submitted) | v2 (this repo) | criterion |
|---|---|---|---|
| dev-window net PnL | +4.136224 | **+13.313524** | > +4.136224 ✓ |
| dev trades | 6 | 6 | >= 3 ✓ |
| dev max drawdown | 1.710% | 0.882% | <= 3% ✓ |
| dev net PnL at 2x fees | +2.932726 | +12.785953 | > 0 ✓ |
| dev fees | 1.197261 | 1.205017 | — |
| deterministic (2 runs) | yes | yes (grade_dev.py check) | ✓ |
| stress DOWN median, 24 paired paths | -11.85 | **-7.28** | >= v1 ✓ |
| stress UP median, 24 paired paths | +8.91 | **+9.26** | >= v1 ✓ |
| stress DOWN median, 48 paired paths | -6.49 | **-5.91** | >= v1 ✓ |
| stress UP median, 48 paired paths | +11.72 | **+12.20** | >= v1 ✓ |
| worst stress max DD (48 paths) | 6.98% | 5.93% | (bar: 25%) |

Round trips on the dev window, v1 -> v2: scratch -1.21 -> -0.06; the dip
that ground into a -160 bps hard stop for -9.18 is now cut by the 120-bar
time stop at -1.27 (sold 63,826.64 instead of riding to 62,814.00); the
capitulation reversal +14.52 -> +14.64. The -$9.18 pattern — the second
reversal entry sitting through a two-hour grind — is exactly what the
shorter time stop removes, and the longer cooldown stops the immediate
re-entry into the same chop that ate the up-drift paths in testing.

### The one criterion that needs an honest footnote (criterion 2)

The pre-registered bar quoted v1's recorded stress medians (DOWN -15.86,
UP +21.10). Those came from a 12-path harness the v1 session did not save.
This session re-implemented the described method (60-bar blocks of the dev
window's own close returns, depths from real bars, mirrored up-drift) TWICE
independently (return-factor paths with opens = prev close; raw rebased
blocks with opens preserved). The two implementations agree closely, and
both grade V1 ITSELF at DOWN -11.85 / UP +8.91 (24 paths) — i.e. v1 does
not reach its own recorded +21.10 UP median under the re-implemented
method, so that number is not reproducible like-for-like (12-path medians
are also simply noisy). The comparison that can actually be run — v1 and
v2 on IDENTICAL synthetic paths, same seeds — has v2 ahead on both
regimes at both 24 and 48 paths (table above), and v2's DOWN median also
clears the recorded -15.86 bar on every path set. The parent should read
criterion 2 as: satisfied on the paired like-for-like evidence, with the
recorded UP figure documented here as not reproducible.

## Ship criteria (pre-registered)

1. Dev-window net PnL > +4.136224 (v1's figure, recomputed with grade_dev.py).
2. Stress medians at least as good as v1 on BOTH path families (DOWN median
   >= -15.86, UP median >= +21.10), same stress method as v1, compared
   like-for-like.
3. Dev-window max drawdown <= 3%.
4. At least 3 trades on the dev window; net PnL at 2x fees > 0.
5. Deterministic: two runs byte-identical. Stdlib only, no network /
   wall-clock / files outside repo.

## Baseline verification (this session)

- grade_dev.py on unmodified v1 main.py reproduced the v1 record
  bit-exactly: dev net PnL +4.136224, trades 6, fees 1.197261, max DD
  1.710%, 2x fees +2.932726, deterministic True.

## Candidates tried (paired batch grading; dev + identical 24-path stress sets)

Single changes from v1 (dev PnL / DOWN med / UP med):

- hard stop 200: +2.489 / -13.40 / +8.65 — worse. Stop 130: +5.363 / -10.28
  / +6.80 — dev and DOWN up, UP down. Neither dominates.
- trail 110: +5.598 / -10.18 / +5.44. Trail 180: +1.861 / -8.00 / +8.38.
  Tightening or loosening the trail trades one regime against the other.
- reversal DD 260: dev -12.564 (fails). Bounce 100: dev -14.615 (fails).
  Deeper/stricter reversal entries shift the whole trade set and break
  the dev gate, as in the v1 session.
- cooldown 35 alone: +4.136 / -11.99 / +9.26 (nearly v1; UP slightly up).
- time stop 120 alone: +13.314 / -7.78 / +7.35 — dev and DOWN jump, UP
  slips below v1. Time stop 90: dev -4.886 (fails; churns, 10 trades).
  Time stop 150: +11.264 / -9.71 / +8.72.
- reversal size 60%: +4.257 / -12.60 / +9.04. Trend size 95%: +4.136 /
  -12.08 / +9.38. Sizing alone does not dominate.

Combinations:

- **time stop 120 + cooldown 35: +13.314 / -7.28 / +9.26 — beats v1 on
  every paired metric. ADOPTED.** The cooldown repairs the UP slip of the
  bare time-stop change (early exits were being recycled into the same
  chop) and improves DOWN further.
- time stop 120 + trail 110: dev +14.788 but UP -2.28 (fails). + stop 130:
  DOWN -13.53 (fails). + trail 180: +11.017 / -8.92 / +6.63 (fails UP).
  + "flat" time-stop condition (exit if < +50 bps at 120 bars, 8 trades):
  +11.640 / -7.98 / +0.61 (fails UP).
- Plateau around the winner (all with cooldown 35 unless noted):
  ts135 +12.029 / -10.13 / +9.26; ts150 +11.264 / -9.47 / +8.72;
  ts105 +6.605 / -8.00 / +5.98; ts120 + cd28: +13.314 / -7.78 / +9.26;
  ts120 + cd45: +13.314 / -7.78 / +9.13; ts120 + cd35 + trend size 95%:
  +13.314 / -8.02 / +8.56. The winner sits inside a flat region
  (time stop 120-150, cooldown 28-45), not on a spike.

Structural ideas tested on top of the winner:

- Volatility-scaled sizing (fraction x clamp(6.5 / mean|ret|90, 0.55,
  1.10)): dev +7.969, DOWN -4.09 (best DOWN of anything tried), worst
  stress DD 4.27% — but UP +3.24, a large participation sacrifice.
  Rejected: it does not dominate, and the race ranks by PnL.
- Reversal higher-high confirmation (close above the highest of the
  prior 3 closes): identical results to the winner on dev and all 48
  stress paths — the bounce + EMA conditions already imply it wherever
  it fired. Rejected as dead complexity.

## Method notes

- Stress harness (scratch, ~/workspace/makefun/btc-race-v2-scratch/):
  stress.py builds paths per the description above (seeded, 60-bar
  blocks, depths copied from the source bar at the same block position);
  batch.py grades many bots on one identical path set (paired). A service
  restart wiped the first /tmp scratch copy mid-session; the harness was
  recreated verbatim in the workspace scratch dir and reproduced every
  earlier figure exactly before work continued.
- Authoring: v2 was implemented in the repo main.py in three edits
  (time stop, cooldown, parameter-comment rationale), then validated with
  the repo's own grade_dev.py (dev grade + determinism + 2x fees) and a
  final 48-path stress run on the repo file itself.
