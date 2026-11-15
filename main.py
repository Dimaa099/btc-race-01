#!/usr/bin/env python3
"""BTC race bot for agent-fun/bars-replay/v1 (make.fun btc-race-24h).

Why this bot trades the way it does: cbBTC changes hands on one thin DEX pool,
and every fill pays a 4 bps fee, 2 bps of modelled slippage and constant-product
price impact that grows with order size, so a round trip costs roughly 20-45 bps
depending on size and pool depth. One-minute price noise (median move ~4.5 bps)
can never pay that, so the bot trades rarely, only for moves that dwarf the
costs, and spends most of its time in cash.

Two entries, both long-only, at most one position at a time:

- Reversal: the close has fallen at least REVERSAL_DD_BPS below its rolling
  300-bar high, has already bounced at least BOUNCE_BPS off its 60-bar low, and
  the fast EMA has turned up with the close above it. The bounce requirement is
  the lesson of the dev window: the first uptick in a grind down is usually a
  trap, a confirmed bounce is a trade. Reversal entries are counter-trend
  gambles, so they are sized smaller (REVERSAL_SIZE_FRACTION of cash).
- Trend: the close is above a rising slow EMA, the fast EMA is above the slow
  one, the 60-bar return is at least TREND_RET_BPS and the price is still near
  its rolling high. Trend entries ride strength and are sized at SIZE_FRACTION.

Exits, all of them full-position: a hard stop under the average cost, a
trailing stop that arms once the trade is in profit, a take-profit, and a time
stop that frees capital from trades that go nowhere. After any exit a cooldown
keeps the bot out of the chop that usually follows.

Everything is a deterministic function of the bars seen so far: no clock, no
randomness, no files, no network, standard library only. Two runs over the same
window produce byte-identical replies.

Protocol: newline-delimited JSON on stdin/stdout. hello once, one batch per
minute, then done; exactly one reply per batch echoing its seq. Amounts are
integers at the scales hello declares.
"""

import json
import sys

# --------------------------------------------------------------------------
# Strategy parameters. Chosen on plateaus, not peaks: each was varied around
# the value below and the neighbours behaved alike, while structurally
# different choices (no hard stop, much deeper reversal thresholds, a wider
# trailing stop) were clearly worse either on the dev window or on
# bootstrapped up/down-drift stress paths. See NOTES.md and dev-results.md.
# --------------------------------------------------------------------------
FAST_SPAN = 10            # fast EMA over closes, bars
SLOW_SPAN = 50            # slow EMA over closes, bars
HIGH_LOOKBACK = 300       # rolling high window for the drawdown-from-high
LOW_LOOKBACK = 60         # rolling low window for the bounce check
MIN_HISTORY = 320         # bars of history before the first entry is allowed
REVERSAL_DD_BPS = 220     # reversal entry: at least this far below the rolling high
BOUNCE_BPS = 70           # reversal entry: close at least this far above the 60-bar low
TREND_RET_BPS = 120       # trend entry: at least this 60-bar return
TREND_DD_MAX_BPS = 150    # trend entry: at most this far below the rolling high
SIZE_FRACTION = 0.85      # share of cash a trend entry spends
REVERSAL_SIZE_FRACTION = 0.50  # share of cash a reversal entry spends
HARD_STOP_BPS = 160       # exit: close this far under the average cost price
TRAIL_ARM_BPS = 100       # trailing stop arms once this far in profit
TRAIL_STOP_BPS = 140      # exit: close this far under the peak close since entry
TAKE_PROFIT_BPS = 700     # exit: bank it at this gain
TIME_STOP_BARS = 180      # exit: held this long and not above cost
COOLDOWN_BARS = 20        # bars to sit out after an exit
MAX_SLIPPAGE_BPS = 300    # per-order slippage cap vs the latest close


class Bot:
    def __init__(self, hello):
        self.hello = hello
        self.symbols = {s["symbol"]: s for s in hello["symbols"]}
        self.symbol = "CBBTC" if "CBBTC" in self.symbols else hello["symbols"][0]["symbol"]
        spec = self.symbols[self.symbol]
        self.price_scale = spec["price_scale"]
        self.qty_scale = spec["qty_scale"]
        self.quote_scale = hello["capital"]["quote_scale"]
        self.warmup_bars = hello.get("warmup_bars", 0)
        self.min_notional = int(hello["fees"]["min_notional_quote_units"])

        self.closes = []            # closes of our symbol, oldest first (ints)
        self.ema_fast = None
        self.ema_slow = None
        self.prev_ema_fast = None
        self.prev_close = None

        self.order_seq = 0
        self.peak_close = None      # highest close since the position opened
        self.bars_held = 0
        self.was_holding = False
        self.cooldown_until = -1    # batch seq through which entries are blocked

    # -- indicators ---------------------------------------------------------
    def _on_close(self, close):
        self.prev_close = self.closes[-1] if self.closes else None
        self.prev_ema_fast = self.ema_fast
        self.closes.append(close)
        af = 2.0 / (FAST_SPAN + 1)
        aslow = 2.0 / (SLOW_SPAN + 1)
        self.ema_fast = close if self.ema_fast is None else self.ema_fast + af * (close - self.ema_fast)
        self.ema_slow = close if self.ema_slow is None else self.ema_slow + aslow * (close - self.ema_slow)

    # -- order helpers --------------------------------------------------------
    def _buy(self, cash, fraction):
        self.order_seq += 1
        notional = int(cash * fraction)
        if notional < self.min_notional:
            return None
        return {"side": "buy", "client_id": f"e{self.order_seq}", "symbol": self.symbol,
                "notional_quote_units": str(notional), "max_slippage_bps": MAX_SLIPPAGE_BPS}

    def _sell_all(self, qty):
        self.order_seq += 1
        return {"side": "sell", "client_id": f"e{self.order_seq}", "symbol": self.symbol,
                "qty": str(qty), "max_slippage_bps": MAX_SLIPPAGE_BPS}

    # -- the decision ---------------------------------------------------------
    def on_batch(self, batch):
        seq = batch["seq"]
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == self.symbol:
                bar = b
        account = batch["account"]
        cash = int(account["cash_quote_units"])
        qty = 0
        cost = 0
        for p in account["positions"]:
            if p["symbol"] == self.symbol:
                qty = int(p["qty"])
                cost = int(p["cost_quote_units"])
        holding = qty > 0

        if bar is None:
            return []                      # no bar for our symbol: judge nothing
        close = int(bar["c"])
        self._on_close(close)

        if holding:
            if not self.was_holding:
                self.peak_close = close
                self.bars_held = 0
            self.peak_close = max(self.peak_close, close)
            self.bars_held += 1
        else:
            if self.was_holding:
                self.cooldown_until = seq + COOLDOWN_BARS
            self.peak_close = None
            self.bars_held = 0
        self.was_holding = holding

        if seq < self.warmup_bars:
            return []

        if holding:
            # Average all-in cost price (buy fee included), in price units.
            avg_cost = cost * 10 ** (self.qty_scale + self.price_scale) / (qty * 10 ** self.quote_scale)
            if close <= avg_cost * (1 - HARD_STOP_BPS / 10_000):
                return [self._sell_all(qty)]
            if (self.peak_close >= avg_cost * (1 + TRAIL_ARM_BPS / 10_000)
                    and close <= self.peak_close * (1 - TRAIL_STOP_BPS / 10_000)):
                return [self._sell_all(qty)]
            if close >= avg_cost * (1 + TAKE_PROFIT_BPS / 10_000):
                return [self._sell_all(qty)]
            if self.bars_held >= TIME_STOP_BARS and close <= avg_cost:
                return [self._sell_all(qty)]
            return []

        # Flat: look for an entry.
        if seq <= self.cooldown_until:
            return []
        if len(self.closes) < MIN_HISTORY or self.prev_close is None:
            return []
        high = max(self.closes[-HIGH_LOOKBACK:])
        if high <= 0:
            return []
        dd_bps = (high - close) * 10_000 / high
        low = min(self.closes[-LOW_LOOKBACK:])
        bounce_bps = (close - low) * 10_000 / low if low > 0 else 0.0
        ret60 = (close - self.closes[-61]) * 10_000 / self.closes[-61]

        reversal = (
            dd_bps >= REVERSAL_DD_BPS
            and bounce_bps >= BOUNCE_BPS
            and close > self.ema_fast
            and self.prev_ema_fast is not None and self.ema_fast > self.prev_ema_fast
            and close >= self.prev_close
        )
        trend = (
            close > self.ema_slow
            and self.ema_fast > self.ema_slow
            and ret60 >= TREND_RET_BPS
            and dd_bps <= TREND_DD_MAX_BPS
        )
        if reversal:
            order = self._buy(cash, REVERSAL_SIZE_FRACTION)
            return [order] if order else []
        if trend:
            order = self._buy(cash, SIZE_FRACTION)
            return [order] if order else []
        return []


def main():
    out = sys.stdout
    bot = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            bot = Bot(msg)
        elif kind == "batch":
            orders = bot.on_batch(msg)
            out.write(json.dumps({"type": "reply", "seq": msg["seq"], "orders": orders},
                                 separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            break


if __name__ == "__main__":
    main()
