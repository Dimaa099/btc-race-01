# Dev-window grading results

Competition kit `btc-race-24h-20261115-0828`, dev window: 1,440 one-minute
CBBTC bars, no gaps. Start 1,000 USDC paper money. All figures are net of the
4 bps fee, 2 bps slippage and constant-product price impact the harness
charges, computed by `grade_dev.py` in this repository.

## This bot (main.py)

```text
bars 1440  deterministic(two runs identical): True
net PnL  +4.136224 USDC   trades 6   rejected 0
fees     1.197261 USDC   max drawdown 1.710%
at 2x fees: net PnL +2.932726 USDC, trades 6
hourly block PnL (USDC): +0.00 +0.00 +0.00 +0.00 +0.00 +0.00 +0.00 -2.25 +1.73
  +1.54 -2.23 +0.00 +0.00 +1.95 -4.39 +2.16 -8.90 +13.83 +5.51 -1.26 -0.76
  -2.81 +0.00 +0.00
```

Fills (bar timestamps are the fill bar's close time, ms):

| fill bar | side | price (USDC) | notional (USDC) | fee (USDC) |
|---|---|---|---|---|
| 1794670980000 | buy  | 65,606.85 | 500.00 | 0.2000 |
| 1794682080000 | sell | 65,500.86 | 498.79 | 0.1996 |
| 1794694500000 | buy  | 63,938.09 | 499.40 | 0.1998 |
| 1794704700000 | sell | 62,814.00 | 490.22 | 0.1962 |
| 1794707520000 | buy  | 62,614.95 | 494.81 | 0.1979 |
| 1794722220000 | sell | 64,503.53 | 509.33 | 0.2038 |

Three round trips: a scratch (−$1.21), a hard-stopped dip that kept falling
(−$9.18), and the capitulation reversal bought at 62,614.95 near the window's
62,082 low and trailed out at 64,503.53 (+$14.52). The dev gate (net PnL > 0)
is passed with the drawdown guardrail (25%) met by a wide margin (1.710%).

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
| **this bot** | **+4.136224** | **6** | **1.197261** | **1.710%** |

## Robustness notes (not scores)

Block-bootstrapped synthetic windows (60-bar blocks of the dev window's own
returns, depths drawn from real bars; 12 paths per regime) give the shape of
the strategy rather than a forecast: on down-drift paths the median outcome
was −15.86 USDC with worst max drawdown 7.08%, and on mirrored up-drift paths
the median was +21.10 USDC, positive in 8 of 12. A long-only bot with ~25 bps
round-trip costs cannot beat a falling tape; it can only lose less than the
field while keeping its upside participation, which is what the sizing,
confirmation and stop rules are for.
