# BTC Race Bot — cbBTC/USDC, 1-minute bars

A long-only trading bot for the make.fun BTC race (protocol
`agent-fun/bars-replay/v1`). It trades Coinbase Wrapped BTC (cbBTC) against
USDC on replayed 1-minute bars with 1,000 USDC of paper money, starting flat
and staying in cash unless a setup that can pay the trading costs appears.

## Why it trades so rarely

Every fill in this replay pays a 4 bps fee and 2 bps of modelled slippage, plus
constant-product price impact against the pool's depth at the fill bar's open —
roughly 20-45 bps per round trip depending on order size and depth. The median
one-minute close-to-close move is about 4.5 bps, so minute-noise scalping is a
guaranteed bleed (the kit's own EMA-cross baseline loses ~$135 of its $1,000 on
the dev window, $23.72 of it in fees alone). This bot therefore:

- trades only for moves that dwarf the round-trip cost,
- sizes counter-trend entries smaller than trend entries, because size is
  impact and impact is cost,
- and sits in cash the rest of the time. Cash is a position.

## The strategy

Indicators over the closes seen so far: a fast EMA (10 bars), a slow EMA
(50 bars), the rolling 300-bar high and the rolling 60-bar low.

**Reversal entry** (buy the confirmed turn after a fall), sized at 50% of cash:

- close at least 220 bps below the rolling 300-bar high,
- close already bounced at least 70 bps off the 60-bar low — the first uptick
  in a grind down is usually a trap; a confirmed bounce is a trade,
- close above the fast EMA, the fast EMA rising, and the close not down on the
  previous bar.

**Trend entry** (ride confirmed strength), sized at 85% of cash:

- close above the slow EMA with the fast EMA above the slow one,
- 60-bar return at least +120 bps,
- close within 150 bps of the rolling high.

**Exits** (always the whole position, whichever comes first):

- hard stop: close 160 bps under the position's average all-in cost,
- trailing stop: once the best close since entry is 100 bps in profit, exit
  when the close falls 140 bps under that peak,
- take-profit at +700 bps over cost,
- time stop: held 180 bars and not above cost, free the capital.

After any exit, a 20-bar cooldown blocks re-entry. Every order carries a
300 bps `max_slippage_bps` cap against the latest close. Entries need 320 bars
of history first, so the bot is silent for roughly the first five hours of a
window rather than trading blind.

Parameters were chosen on plateaus, not peaks: each was varied around its
final value and the neighbours behaved alike, while structurally different
choices (no hard stop, far deeper reversal thresholds, a wider trailing stop)
graded clearly worse on the dev window, on block-bootstrapped down-drift
paths, or on mirrored up-drift paths. The honest expectation is regime-shaped:
it participates in uptrends, harvests sharp V-recoveries, and loses slowly and
small in grinding downtrends — which is also the profile that stays inside the
race's 25% max-drawdown guardrail with a wide margin.

## Dev window result

On the kit's published dev window (1,440 bars; BTC fell 4.0% start to end,
with a −7.6% capitulation and a +3.9% recovery into the close):

- **net PnL +4.136224 USDC** after every fee, slippage and impact — the dev
  gate asks for anything above $0,
- 6 trades (3 round trips), 0 rejected orders, fees 1.197261 USDC,
- max drawdown 1.710%, net PnL at 2x fees +2.932726 USDC,
- two executions produce byte-identical replies.

Every trading baseline in the kit loses money on the same window
(buy-and-hold −44.01, momentum −40.92, RSI-Bollinger −57.37, ADX-momentum
−86.60, EMA-cross −134.95 USDC). Full detail in `dev-results.md`.

## Running it

The bot is `main.py` (Python 3, standard library only). It speaks
newline-delimited JSON on stdin/stdout exactly as the harness runs it:

```text
python3 -P -s main.py
```

It reads a `hello`, answers every `batch` with one `reply` echoing the batch's
`seq`, and stops at `done`. It uses no network, no wall clock, no randomness
and no files, so two runs over the same window are byte-identical.

To grade it locally on the dev window, point the included grader at a
downloaded competition kit (it re-derives nothing; it replays the kit's frozen
bars through an exact integer port of the harness's fill model, validated to
reproduce the kit's own buy-and-hold grade to the micro-USDC):

```text
python3 grade_dev.py --kit <path to the btc-race kit>
```

`grade_dev.py` prints net PnL, trades, fees, max drawdown, the 2x-fee figure,
hourly block PnL and every fill, and verifies determinism by running the bot
twice and comparing the reply streams byte for byte.

## License

MIT — see `LICENSE`.
