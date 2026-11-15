# Dev-window grading results

Competition kit `btc-race-24h-20261115-0828`, dev window: 1,440 one-minute
CBBTC bars, no gaps. Start 1,000 USDC paper money. All figures are net of the
4 bps fee, 2 bps slippage and constant-product price impact the harness
charges, computed by `grade_dev.py` in this repository.

## This bot (main.py)

```text
bars 1440  deterministic(two runs identical): True
net PnL  +13.313524 USDC   trades 6   rejected 0
fees     1.205017 USDC   max drawdown 0.882%
at 2x fees: net PnL +12.785953 USDC, trades 6
hourly block PnL (USDC): +0.00 +0.00 +0.00 +0.00 +0.00 +0.00 +0.00 -2.25 +1.73
  +0.46 +0.00 +0.00 +0.00 +1.96 -4.39 +2.17 -1.00 +13.96 +5.56 -1.27 -0.77
  -2.84 +0.00 +0.00
```

Fills (bar timestamps are the fill bar's close time, ms):

| fill bar | side | price (USDC) | notional (USDC) | fee (USDC) |
|---|---|---|---|---|
| 1794670980000 | buy  | 65,606.85 | 500.00 | 0.2000 |
| 1794678900000 | sell | 65,652.19 | 499.94 | 0.2001 |
| 1794694500000 | buy  | 63,938.17 | 499.97 | 0.2000 |
| 1794702540000 | sell | 63,826.64 | 498.70 | 0.1996 |
| 1794707520000 | buy  | 62,615.57 | 499.34 | 0.1997 |
| 1794722220000 | sell | 64,503.10 | 513.98 | 0.2057 |

Three round trips: a scratch (−$0.06), a dip cut by the 120-bar time stop
before it could grind into a hard stop (−$1.27), and the capitulation
reversal bought at 62,615.57 near the window's 62,082 low and trailed out
at 64,503.10 (+$14.64). The dev gate (net PnL > 0) is passed with the
drawdown guardrail (25%) met by a wide margin (0.882%).

This is the v2 revision of the bot (see NOTES-V2.md): the first submitted
version, identical except for a 180-bar time stop and a 20-bar cooldown,
graded +4.136224 USDC here (fees 1.197261, max drawdown 1.710%, +2.932726
at 2x fees), with its third trade hard-stopped for −$9.18. The shorter time
stop and longer cooldown replaced it after beating it on the dev window and
on paired block-bootstrap stress paths in both drift regimes.

## How the grader was validated

`grade_dev.py` is an exact integer port of the fill and decision model in the
kit's harness source (`sim/bars/engine.ts`). Driven with a Python
buy-and-hold bot (all-in on the first batch, hold to the flatten), it
reproduces the kit's own `grade-local` result for the `buy-and-hold`
baseline exactly:

| figure | kit grade-local | grade_dev.py |
|---|---|---|
| net PnL | −44.009846 USDC | −44.009846 USDC |
| fees | 0.782550 USDC | 0.782550 USDC |
| trades | 1 | 1 |
| max drawdown | 0.079247326 | 0.079247 (7.925%) |
| net PnL at 2x fees | −44.773526 USDC | −44.773526 USDC |

(The kit's `grade-local --driver docker` is the server's route for Python
bots; Docker is unavailable on the build machine and the process driver runs
Node bots only, so the official command could not be run on this Python entry
directly. The port's bit-exact match on the baseline above is the check that
stands in for it.)

## Kit baselines on the same dev window (kit grade-local, official)

| baseline | net PnL (USDC) | trades | fees (USDC) | max drawdown |
|---|---|---|---|---|
| buy-and-hold | −44.009846 | 1 | 0.782550 | 7.925% |
| momentum | −40.924874 | 1 | 0.783784 | 7.925% |
| rsi-bollinger | −57.365539 | 12 | 4.660305 | 6.775% |
| adx-momentum | −86.598664 | 35 | 13.632702 | 8.820% |
| ema-cross | −134.953038 | 63 | 23.716842 | 13.653% |
| hold-cash | 0.000000 | 0 | 0.000000 | 0.000% (fails the dev gate: not > 0, and no trades) |
| **this bot (v2)** | **+13.313524** | **6** | **1.205017** | **0.882%** |

## Robustness notes (not scores)

Block-bootstrapped synthetic windows (60-bar blocks of the dev window's own
returns, depths drawn from real bars) give the shape of the strategy rather
than a forecast. Graded in pairs on identical synthetic paths (48 per
regime), the v2 revision's median outcome was −5.91 USDC on down-drift
paths (worst max drawdown 5.93%) and +12.20 USDC on mirrored up-drift paths,
against the first version's −6.49 and +11.72 on the same paths; on a
24-path subset the pair read −7.28 / +9.26 against −11.85 / +8.91. (The
first version's build notes quote −15.86 / +21.10 from its own 12-path
harness, which was not saved; two re-implementations of the described
method agree with each other and give the first version the milder figures
above, so the paired comparison is the like-for-like one.) A long-only bot
with ~25 bps round-trip costs cannot beat a falling tape; it can only lose
less than the field while keeping its upside participation, which is what
the sizing, confirmation and stop rules are for.
