#!/usr/bin/env python3
"""Local dev-window grader for the make.fun BTC race entry.

An exact integer port of the kit harness's market model (agent-fun/bars-replay/v1,
sim/bars/engine.ts in the kit's harness source): orders decided on one batch fill at
the next bar's open, paying a 4 bps fee, 2 bps of modelled slippage and constant-product
price impact against that bar's open depth; anything still held at the end is flattened
at the last close against its close depth. It drives the bot (main.py) as a subprocess
over the real newline-JSON wire protocol, with the same runtime flags the grader uses
(python3 -P -s, PYTHONHASHSEED=0), and reports net PnL, trades, fees, max drawdown and
per-hour block PnL. It also runs the bot a second time and checks the two reply streams
are byte-identical, and re-grades at 2x fees with the official cost-sensitivity clamp.

Usage:
    python3 grade_dev.py --kit <kit dir> [--bot <repo dir or main.py>] [--json]

The kit dir is the downloaded competition kit (it holds dataset/bars/CBBTC.ndjson.gz).
Standard library only.
"""

import argparse
import gzip
import json
import math
import os
import subprocess
import sys

BPS = 10_000
FEE_BPS = 4
SLIPPAGE_BPS = 2
MIN_NOTIONAL = 1_000_000          # 1 USDC in micro-USDC
START_CASH = 1_000_000_000        # 1,000 USDC in micro-USDC
MAX_ORDERS = 16
MAX_WEIGHT_BPS = 10_000
QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
SYMBOL = "CBBTC"
MINT = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij"
STEP_MS = 60_000


def value(qty, price):
    """Quote units a quantity is worth at a price (truncating), the protocol's value()."""
    return qty * price * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


class Pool:
    """The constant-product pool one bar's fills walk, as an exact fraction.

    Mirrors BarsPool in the harness: hops in series trade as one pool of depth
    D = 1 / (1/D1 + 1/D2); its quote side y starts at D and every fill moves it
    along x*y = D^2. Amounts and depths are scaled exactly as the harness scales
    them (depths by 10^quote_scale, amounts by 10^6), so the arithmetic matches
    bit for bit.
    """

    __slots__ = ("yn", "yd", "kn", "kd", "unit")

    def __init__(self, yn, yd, kn, kd, unit):
        g = math.gcd(yn, yd)
        self.yn, self.yd, self.kn, self.kd, self.unit = yn // g, yd // g, kn, kd, unit

    @classmethod
    def of(cls, depths):
        d = [x * 10 ** QUOTE_SCALE for x in depths]
        prod = 1
        for x in d:
            prod *= x
        total = 0
        for i in range(len(d)):
            other = 1
            for j, x in enumerate(d):
                if j != i:
                    other *= x
            total += other
        g = math.gcd(prod, total)
        p, s = prod // g, total // g
        return cls(p, s, p * p, s * s, 10 ** 6)

    def buy(self, spend):
        """(num, den, pool_after): the factor the average buy price is over the open."""
        yn2 = self.yn + spend * self.unit * self.yd
        return self.yn * yn2 * self.kd, self.yd * self.yd * self.kn, Pool(yn2, self.yd, self.kn, self.kd, self.unit)

    def sell(self, val):
        """(num, den, pool_after): the factor the open is over the average sell price."""
        t = self.kn * self.yd + val * self.unit * self.yn * self.kd
        return t * self.yd, self.kd * self.yn * self.yn, Pool(self.kn * self.yn, t, self.kn, self.kd, self.unit)


def buy_fill(notional, open_price, pool, fee_bps):
    spend = notional * (BPS - fee_bps) // BPS
    num, den, after = pool.buy(spend)
    price = open_price * (BPS + SLIPPAGE_BPS) * num // (BPS * den)
    qty = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10 ** QUOTE_SCALE) if price > 0 else 0
    return price, spend, notional - spend, qty, after


def sell_fill(qty, open_price, pool, fee_bps):
    num, den, after = pool.sell(value(qty, open_price))
    slipped = open_price * (BPS - SLIPPAGE_BPS) * den // (BPS * num)
    price = slipped if slipped >= 1 else 1
    gross = value(qty, price)
    proceeds = gross * (BPS - fee_bps) // BPS
    return price, gross, gross - proceeds, proceeds, after


def load_bars(kit):
    path = os.path.join(kit, "dataset", "bars", "CBBTC.ndjson.gz")
    bars = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            bars.append({
                "t": r["t"],
                "o": int(r["o"]), "h": int(r["h"]), "l": int(r["l"]), "c": int(r["c"]),
                "o_depth": [int(x) for x in r["o_depth"]],
                "c_depth": [int(x) for x in r["c_depth"]],
                "wire": {"symbol": SYMBOL, "t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"],
                         "c": r["c"], "v": None, "o_depth": r["o_depth"], "c_depth": r["c_depth"]},
            })
    bars.sort(key=lambda b: b["t"])
    return bars


def hello_msg(first_close_ms):
    return {
        "type": "hello", "protocol": "agent-fun/bars-replay/v1", "seed": "0",
        "symbols": [{"symbol": SYMBOL, "mint": MINT, "price_scale": PRICE_SCALE, "qty_scale": QTY_SCALE}],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE, "quote_units": str(START_CASH)},
        "bar_interval": "1m", "first_close_ms": first_close_ms,
        "caps": {"max_open_positions": 1, "max_orders_per_bar": MAX_ORDERS,
                 "max_weight_bps": MAX_WEIGHT_BPS, "shorts": False, "leverage": False},
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIPPAGE_BPS,
                 "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
    }


class Run:
    """One full replay of the bot over the dev window, harness semantics throughout."""

    def __init__(self, bars, bot_cmd, fee_multiplier=1, clamp=frozenset()):
        self.bars = {b["t"]: b for b in bars}
        self.times = sorted(self.bars)
        self.bot_cmd = bot_cmd
        self.fee_bps = min(BPS, FEE_BPS * fee_multiplier)
        self.clamp = clamp
        self.cash = START_CASH
        self.qty = 0
        self.cost = 0
        self.close = None
        self.close_depth = None
        self.pending = []          # orders decided on the previous batch
        self.used_ids = set()
        self.trades = 0
        self.fees = 0
        self.rejected = 0
        self.equity_points = [START_CASH]
        self.hour_marks = []       # equity at each hour boundary (every 60 batches)
        self.replies = []          # raw reply lines, for the determinism check
        self.decision_accepted = set()
        self.fills_log = []

    def account(self):
        pos = []
        if self.qty > 0:
            pos.append({"symbol": SYMBOL, "qty": str(self.qty), "cost_quote_units": str(self.cost)})
        equity = self.cash + (value(self.qty, self.close) if self.qty > 0 and self.close else 0)
        return {"cash_quote_units": str(self.cash), "positions": pos, "equity_quote_units": str(equity)}

    # -- decision checks, in the harness's order -------------------------------
    def decide(self, seq, orders):
        is_last = seq == len(self.times) - 1
        equity = self.cash + (value(self.qty, self.close) if self.qty > 0 and self.close else 0)
        buys = 0
        bought = 0
        sold = 0
        open_held = self.qty > 0
        open_count = 1 if open_held else 0   # held symbols plus symbols bought earlier in this reply
        decided = []
        for index, order in enumerate(orders):
            cid = order["client_id"]
            side = order["side"]
            amount = int(order["notional_quote_units"]) if side == "buy" else int(order["qty"])
            written = amount
            clamp = f"{seq}:{index}" in self.clamp
            reject = None
            if index >= MAX_ORDERS:
                reject = "max_orders_per_bar"
            elif cid in self.used_ids:
                reject = "duplicate_client_id"
            elif order["symbol"] != SYMBOL:
                reject = "not_in_universe"
            elif written == 0:
                reject = "zero_amount"
            elif is_last:
                reject = "after_end"
            elif self.close is None:
                reject = "no_bar"
            elif side == "buy":
                cash_left = self.cash - buys
                held_value = value(self.qty, self.close) if self.qty > 0 else 0
                if clamp:
                    room = MAX_WEIGHT_BPS * equity // BPS - held_value - bought
                    bound = min(cash_left, room)
                    if amount > bound:
                        amount = bound if bound > 0 else 0
                if amount == 0:
                    reject = "insufficient_cash" if cash_left <= 0 else "max_weight"
                elif amount < MIN_NOTIONAL:
                    reject = "below_min_notional"
                elif amount > cash_left:
                    reject = "insufficient_cash"
                elif not open_held and open_count >= 1:
                    reject = "max_open_positions"
                elif (held_value + bought + amount) * BPS > MAX_WEIGHT_BPS * equity:
                    reject = "max_weight"
                if reject is None:
                    buys += amount
                    bought += amount
                    open_count = 1
            else:
                available = self.qty - sold
                if clamp and amount > available:
                    amount = available if available > 0 else 0
                if amount == 0 or amount > available:
                    reject = "insufficient_position"
                elif amount != available and value(amount, self.close) < MIN_NOTIONAL:
                    reject = "below_min_notional"
                if reject is None:
                    sold += amount
            self.used_ids.add(cid)
            if reject is None:
                self.decision_accepted.add(f"{seq}:{index}")
            decided.append({"order": order, "amount": amount, "reject": reject,
                            "close": self.close, "seq": seq, "index": index})
        self.pending = decided

    # -- fill resolution at the next bar's open --------------------------------
    def resolve(self, bar):
        fills = []
        pool = None
        for p in self.pending:
            order = p["order"]
            base = {"client_id": order["client_id"], "symbol": SYMBOL, "side": order["side"],
                    "status": "rejected", "reason": None, "decided_bar": self.times[p["seq"]],
                    "fill_bar": bar["t"] if bar else None, "price": "0", "qty": "0",
                    "notional_quote_units": "0", "fee_quote_units": "0"}
            if p["reject"] is not None:
                base["reason"] = p["reject"]
                self.rejected += 1
                fills.append(base)
                continue
            if bar is None:
                base["reason"] = "no_bar"
                self.rejected += 1
                fills.append(base)
                continue
            if pool is None:
                pool = Pool.of(bar["o_depth"])
            max_slip = int(order["max_slippage_bps"])
            if order["side"] == "buy":
                price, spend, fee, qty, after = buy_fill(p["amount"], bar["o"], pool, self.fee_bps)
                if price * BPS > p["close"] * (BPS + max_slip):
                    base["reason"] = "max_slippage"
                    self.rejected += 1
                    fills.append(base)
                    continue
                if qty == 0:
                    base["reason"] = "zero_amount"
                    self.rejected += 1
                    fills.append(base)
                    continue
                pool = after
                self.cash -= p["amount"]
                self.qty += qty
                self.cost += p["amount"]
                self.trades += 1
                self.fees += fee
                base.update(status="filled", price=str(price), qty=str(qty),
                            notional_quote_units=str(p["amount"]), fee_quote_units=str(fee))
            else:
                price, gross, fee, proceeds, after = sell_fill(p["amount"], bar["o"], pool, self.fee_bps)
                if price * BPS < p["close"] * (BPS - max_slip):
                    base["reason"] = "max_slippage"
                    self.rejected += 1
                    fills.append(base)
                    continue
                pool = after
                self.cash += proceeds
                self.cost = self.cost * (self.qty - p["amount"]) // self.qty
                self.qty -= p["amount"]
                self.trades += 1
                self.fees += fee
                base.update(status="filled", price=str(price), qty=str(p["amount"]),
                            notional_quote_units=str(proceeds), fee_quote_units=str(fee))
            fills.append(base)
            self.fills_log.append(base)
        self.pending = []
        return fills

    # -- the lockstep drive ------------------------------------------------------
    def execute(self):
        env = dict(os.environ, PYTHONHASHSEED="0")
        proc = subprocess.Popen(self.bot_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)

        def send(msg):
            proc.stdin.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
            proc.stdin.flush()

        send(hello_msg(self.times[0]))
        for seq, t in enumerate(self.times):
            bar = self.bars[t]
            fills = self.resolve(bar)
            self.close = bar["c"]
            self.close_depth = bar["c_depth"]
            acct = self.account()
            self.equity_points.append(int(acct["equity_quote_units"]))
            if (seq + 1) % 60 == 0:
                self.hour_marks.append(int(acct["equity_quote_units"]))
            send({"type": "batch", "seq": seq, "t": t, "bars": [bar["wire"]],
                  "fills": fills, "account": acct})
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"bot exited before replying to batch {seq}")
            self.replies.append(line)
            reply = json.loads(line)
            if reply.get("type") != "reply" or reply.get("seq") != seq:
                raise RuntimeError(f"batch {seq}: malformed reply {line[:120]!r}")
            orders = reply.get("orders", [])
            if len(orders) > 64:
                raise RuntimeError(f"batch {seq}: {len(orders)} orders is a protocol violation")
            self.decide(seq, orders)
        # End of window: the last batch's orders die with their decision reasons and
        # any position is flattened at the last close against its close depth.
        for p in self.pending:
            if p["reject"] is None:
                raise RuntimeError("an accepted order survived the last batch")
        self.pending = []
        if self.qty > 0:
            pool = Pool.of(self.close_depth)
            _price, _gross, fee, proceeds, _after = sell_fill(self.qty, self.close, pool, self.fee_bps)
            self.cash += proceeds
            self.fees += fee
            self.qty = 0
            self.cost = 0
        self.equity_points.append(self.cash)
        send({"type": "done", "seq": len(self.times), "reason": "end_of_data", "account": self.account()})
        try:
            proc.stdin.close()
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        return self

    # -- reporting ---------------------------------------------------------------
    def max_drawdown(self):
        peak = 0
        worst = 0.0
        for e in self.equity_points:
            peak = max(peak, e)
            if peak > 0:
                worst = max(worst, (peak - e) / peak)
        return worst

    def summary(self):
        net = self.cash - START_CASH
        blocks = []
        prev = START_CASH
        for mark in self.hour_marks:
            blocks.append((mark - prev) / 1e6)
            prev = mark
        return {
            "net_pnl_usdc": net / 1e6,
            "net_pnl_micro": net,
            "trades": self.trades,
            "rejected_orders": self.rejected,
            "fees_usdc": self.fees / 1e6,
            "max_drawdown": self.max_drawdown(),
            "hourly_block_pnl_usdc": blocks,
            "final_cash_usdc": self.cash / 1e6,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True)
    ap.add_argument("--bot", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    bot_path = args.bot
    if os.path.isdir(bot_path):
        bot_path = os.path.join(bot_path, "main.py")
    bars = load_bars(args.kit)
    cmd = [sys.executable, "-P", "-s", bot_path]

    first = Run(bars, cmd).execute()
    second = Run(bars, cmd).execute()
    deterministic = first.replies == second.replies
    stressed = Run(bars, cmd, fee_multiplier=2, clamp=first.decision_accepted).execute()

    out = {
        "bot": bot_path,
        "bars": len(bars),
        "deterministic": deterministic,
        "dev": first.summary(),
        "dev_2x_fees": stressed.summary(),
        "fills": [{"bar": f["fill_bar"], "side": f["side"], "status": f["status"],
                   "reason": f["reason"], "price": int(f["price"]) / 10 ** PRICE_SCALE,
                   "notional_usdc": int(f["notional_quote_units"]) / 1e6,
                   "fee_usdc": int(f["fee_quote_units"]) / 1e6} for f in first.fills_log],
    }
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        d = out["dev"]
        print(f"bars {out['bars']}  deterministic(two runs identical): {deterministic}")
        print(f"net PnL  {d['net_pnl_usdc']:+.6f} USDC   trades {d['trades']}   rejected {d['rejected_orders']}")
        print(f"fees     {d['fees_usdc']:.6f} USDC   max drawdown {d['max_drawdown'] * 100:.3f}%")
        print(f"at 2x fees: net PnL {out['dev_2x_fees']['net_pnl_usdc']:+.6f} USDC, trades {out['dev_2x_fees']['trades']}")
        print("hourly block PnL (USDC): " + " ".join(f"{x:+.2f}" for x in d["hourly_block_pnl_usdc"]))
        for f in out["fills"]:
            print(f"  fill bar {f['bar']} {f['side']:4s} @ {f['price']:.2f} notional {f['notional_usdc']:.2f} fee {f['fee_usdc']:.4f}")
    return 0 if deterministic else 1


if __name__ == "__main__":
    sys.exit(main())
