from AlgorithmImports import *
import numpy as np
import pandas as pd


class SemiconductorRegimeLongShort(QCAlgorithm):
    """
    Regime-aware semiconductor long/short strategy.

    Idea:
      - Use a few slow regime filters (trend, relative strength, curve proxy) to decide posture.
      - Rank a fixed semiconductor basket using risk-adjusted momentum.
      - Scale total gross exposure using realized SOXX volatility as a proxy.
    """

    def Initialize(self):
        # Fixed backtest window for reproducibility.
        self.SetStartDate(2025, 9, 1)
        self.SetEndDate(2026, 2, 20)
        self.SetCash(100000)

        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        self.SetBenchmark("SPY")

        # --------- Parameters ----------
        self.rebalance_mode = "monthly"         # "daily" / "weekly" / "monthly"
        self.mom_lookback = 126                 # ~6 months
        self.vol_score_window = 60              # realized vol for scoring
        self.vol_target_window = 20             # realized vol for targeting gross
        self.regime_ma = 200                    # regime MA length

        self.target_annual_vol = 0.10           # gross scaling target
        self.max_gross = 1.30
        self.min_gross = 0.20

        self.min_names_each_side = 4            # semis basket is smaller
        self.max_weight_per_name = 0.12         # avoid concentration

        # Regime gross presets (before vol scaling)
        self.gross_bull = 1.10     # regime=3
        self.gross_mixed = 1.00    # regime=2
        self.gross_neutral = 0.80  # regime=1
        self.gross_bear = 0.90     # regime=0

        # --------- Symbols (signals only; not held) ----------
        self.soxx = self.AddEquity("SOXX", Resolution.Daily).Symbol
        self.spy = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.ief = self.AddEquity("IEF", Resolution.Daily).Symbol
        self.shy = self.AddEquity("SHY", Resolution.Daily).Symbol

        # Fixed semiconductor basket (liquid, diverse)
        tickers = [
            "NVDA", "AMD", "AVGO", "QCOM", "TXN", "INTC", "MU", "ADI",
            "ON", "MCHP", "NXPI", "MRVL", "KLAC", "LRCX", "AMAT", "TER"
        ]
        self.semis = [self.AddEquity(t, Resolution.Daily).Symbol for t in tickers]

        # Warmup ensures MAs and lookbacks exist
        warm = max(self.regime_ma, self.mom_lookback, self.vol_score_window, self.vol_target_window) + 5
        self.SetWarmup(warm, Resolution.Daily)

        # Schedule rebalance
        self._last_rebalance_date = None
        self._schedule_rebalance()

    # ---------------- Scheduling ----------------

    def _schedule_rebalance(self):
        """Attach the Rebalance handler to the selected schedule."""
        if self.rebalance_mode == "weekly":
            self.Schedule.On(
                self.DateRules.WeekStart(self.spy),
                self.TimeRules.AfterMarketOpen(self.spy, 30),
                self.Rebalance
            )
        elif self.rebalance_mode == "daily":
            self.Schedule.On(
                self.DateRules.EveryDay(self.spy),
                self.TimeRules.AfterMarketOpen(self.spy, 30),
                self.Rebalance
            )
        else:
            self.Schedule.On(
                self.DateRules.MonthStart(self.spy),
                self.TimeRules.AfterMarketOpen(self.spy, 30),
                self.Rebalance
            )

    # ---------------- Helpers ----------------

    def _is_tradeable(self, sym):
        if sym not in self.Securities:
            return False
        sec = self.Securities[sym]
        return sec.IsTradable and sec.HasData and sec.Price is not None and sec.Price > 0

    def _zscore(self, x):
        mu = x.mean()
        sd = x.std()
        if sd is None or np.isnan(sd) or sd < 1e-12:
            return x * 0.0
        return (x - mu) / sd

    def _compute_regime_score(self, close):
        """
        Regime score 0..3:
          +1 if SOXX > MA200
          +1 if (SOXX/SPY) > MA200 of ratio (semis leading)
          +1 if (IEF/SHY) > MA200 of ratio (curve proxy rising)
        """
        needed = [self.soxx, self.spy, self.ief, self.shy]
        if any(sym not in close.columns for sym in needed):
            return 1  # default neutral-ish

        soxx = close[self.soxx]
        spy = close[self.spy]
        ief = close[self.ief]
        shy = close[self.shy]

        soxx_ma = soxx.rolling(self.regime_ma).mean().iloc[-1]
        c1 = 1 if soxx.iloc[-1] > soxx_ma else 0

        rs = (soxx / spy).replace([np.inf, -np.inf], np.nan).dropna()
        rs_ma = rs.rolling(self.regime_ma).mean().iloc[-1] if len(rs) > self.regime_ma else np.nan
        c2 = 1 if (not np.isnan(rs_ma) and rs.iloc[-1] > rs_ma) else 0

        curve = (ief / shy).replace([np.inf, -np.inf], np.nan).dropna()
        curve_ma = curve.rolling(self.regime_ma).mean().iloc[-1] if len(curve) > self.regime_ma else np.nan
        c3 = 1 if (not np.isnan(curve_ma) and curve.iloc[-1] > curve_ma) else 0

        return int(c1 + c2 + c3)

    def _vol_target_scale(self, soxx_close):
        r = soxx_close.pct_change()
        vol = r.rolling(self.vol_target_window).std().iloc[-1]
        if vol is None or np.isnan(vol) or vol < 1e-8:
            return 1.0
        ann = float(vol * np.sqrt(252))
        scale = float(self.target_annual_vol / max(ann, 1e-3))
        return float(np.clip(scale, self.min_gross, self.max_gross))

    def _alpha_scores(self, eq_close):
        """
        Risk-adjusted momentum:
          score = z(mom_lookback return) - 0.5 * z(realized vol over vol_score_window)
        """
        mom = eq_close.pct_change(self.mom_lookback).iloc[-1]
        r1 = eq_close.pct_change()
        vol = r1.rolling(self.vol_score_window).std().iloc[-1]

        mom_z = self._zscore(mom)
        vol_z = self._zscore(vol)

        score = mom_z - 0.5 * vol_z
        return score.replace([np.inf, -np.inf], np.nan).dropna()

    def _build_targets(self, score, regime, gross):
        """
        Turn regime into target portfolio weights.

        Regime mapping:
          3: net long (long top ~40%)
          2: long/short (top/bottom ~30%) ~ dollar-neutral
          1: defensive market-neutral (top/bottom ~25%) with lower gross preset
          0: net short (short bottom ~50%)

        Returns:
          Dictionary mapping Symbol to weight (negative = short).
        """
        if len(score) < 2 * self.min_names_each_side:
            return {}

        # Only allocate into symbols we can actually trade today.
        score = score[[sym for sym in score.index if self._is_tradeable(sym)]]
        if len(score) < 2 * self.min_names_each_side:
            return {}

        n = len(score)
        target = {}

        if regime == 3:
            k = max(self.min_names_each_side, int(0.40 * n))
            longs = score.nlargest(k).index.tolist()

            w = float(np.clip(gross / len(longs), 0, self.max_weight_per_name))
            for s in longs:
                target[s] = w

        elif regime == 2:
            k = max(self.min_names_each_side, int(0.30 * n))
            longs = score.nlargest(k).index.tolist()
            shorts = score.nsmallest(k).index.tolist()

            long_g = gross * 0.50
            short_g = gross * 0.50

            lw = float(np.clip(long_g / len(longs), 0, self.max_weight_per_name))
            sw = float(np.clip(-short_g / len(shorts), -self.max_weight_per_name, 0))

            for s in longs:
                target[s] = lw
            for s in shorts:
                target[s] = sw

        elif regime == 1:
            k = max(self.min_names_each_side, int(0.25 * n))
            longs = score.nlargest(k).index.tolist()
            shorts = score.nsmallest(k).index.tolist()

            long_g = gross * 0.50
            short_g = gross * 0.50

            lw = float(np.clip(long_g / len(longs), 0, self.max_weight_per_name))
            sw = float(np.clip(-short_g / len(shorts), -self.max_weight_per_name, 0))

            for s in longs:
                target[s] = lw
            for s in shorts:
                target[s] = sw

        else:
            k = max(self.min_names_each_side, int(0.50 * n))
            shorts = score.nsmallest(k).index.tolist()

            sw = float(np.clip(-gross / len(shorts), -self.max_weight_per_name, 0))
            for s in shorts:
                target[s] = sw

        return target

    # ---------------- Rebalance ----------------

    def Rebalance(self):
        if self.IsWarmingUp:
            return

        if self._last_rebalance_date == self.Time.date():
            return
        self._last_rebalance_date = self.Time.date()

        symbols = list(set(self.semis + [self.soxx, self.spy, self.ief, self.shy]))
        lb = max(self.regime_ma, self.mom_lookback, self.vol_score_window, self.vol_target_window) + 5

        hist = self.History(symbols, lb, Resolution.Daily)
        if hist.empty:
            self.Log("No history.")
            return

        try:
            close = hist["close"].unstack(level=0).sort_index().ffill().bfill()
        except Exception as e:
            self.Log("Reshape failed: {}".format(e))
            return

        regime = self._compute_regime_score(close)
        gross_scale = self._vol_target_scale(close[self.soxx])

        if regime == 3:
            base_gross = self.gross_bull
        elif regime == 2:
            base_gross = self.gross_mixed
        elif regime == 1:
            base_gross = self.gross_neutral
        else:
            base_gross = self.gross_bear

        gross = float(np.clip(base_gross * gross_scale, 0.0, self.max_gross))

        eq_close = close[[s for s in self.semis if s in close.columns]]
        score = self._alpha_scores(eq_close)
        target = self._build_targets(score, regime, gross)

        target_syms = set(target.keys())
        for sym in list(self.Portfolio.Keys):
            if self.Portfolio[sym].Invested and sym not in target_syms:
                self.Liquidate(sym)

        for sym, w in target.items():
            if self._is_tradeable(sym):
                self.SetHoldings(sym, float(w))

        self.Log("Rebalance: regime={} gross={:.2f} names={}".format(regime, gross, len(target)))

    def OnData(self, data):
        pass


