# SemiconductorRegimeLongShort

Regime-aware **semiconductor long/short** strategy for QuantConnect
(Lean).

This algorithm trades a fixed basket of liquid semiconductor equities
using:

1.  **Slow regime filters** to determine posture (bullish, neutral,
    bearish)
2.  **Risk-adjusted momentum ranking** to select long and short
    candidates
3.  **Volatility-based gross scaling** using SOXX as a sector risk proxy

------------------------------------------------------------------------

## Universe

### Signal instruments (not traded)

-   SOXX --- semiconductor sector proxy (trend + volatility targeting)
-   SPY --- market benchmark (relative strength comparison)
-   IEF, SHY --- curve proxy via IEF/SHY ratio

### Tradable semiconductor basket

NVDA, AMD, AVGO, QCOM, TXN, INTC, MU, ADI,\
ON, MCHP, NXPI, MRVL, KLAC, LRCX, AMAT, TER

The basket is fixed to ensure liquidity, stability, and reproducibility.

------------------------------------------------------------------------

## Strategy Pipeline

Regime Detection → Alpha Ranking → Gross Scaling → Portfolio
Construction

------------------------------------------------------------------------

## 1. Regime Detection

A slow additive regime score from three filters:

**Trend** SOXX \> MA(200)

**Relative Strength** SOXX / SPY \> MA200(SOXX/SPY)

**Curve Proxy** IEF / SHY \> MA200(IEF/SHY)

Final score:

regime = trend + relative_strength + curve

    Score Interpretation
  ------- -----------------------
        3 Strong bullish regime
        2 Mixed / constructive
        1 Defensive / neutral
        0 Weak / bearish

------------------------------------------------------------------------

## 2. Alpha Model --- Risk‑Adjusted Momentum

For each semiconductor stock:

-   Momentum = return over `mom_lookback` (default 126 trading days ≈ 6
    months)
-   Volatility = realized daily volatility over `vol_score_window`
    (default 60 days)

Final score:

score = z(momentum) − 0.5 × z(volatility)

This favors persistent winners with smoother trends.

------------------------------------------------------------------------

## 3. Portfolio Construction

### Regime 3 --- Net Long

-   Long top \~40% of ranked names
-   No shorts
-   Highest base gross

### Regime 2 --- Dollar Neutral

-   Long top \~30%
-   Short bottom \~30%
-   \~50/50 gross split

### Regime 1 --- Defensive Neutral

-   Long top \~25%
-   Short bottom \~25%
-   Lower base gross

### Regime 0 --- Net Short

-   Short bottom \~50%
-   No longs

------------------------------------------------------------------------

## Safeguards

-   Requires at least `2 × min_names_each_side` valid symbols (default
    8)
-   Each position capped at `max_weight_per_name` (default 12%)
-   Non‑target holdings are liquidated
-   Symbols must be tradeable with valid data

------------------------------------------------------------------------

## 4. Volatility Targeting

Sector volatility estimated from SOXX returns over `vol_target_window`
(default 20).

Annualized volatility:

ann_vol = vol × sqrt(252)

Scaling factor:

scale = target_annual_vol / ann_vol

Clipped to:

\[min_gross, max_gross\]

Base gross presets:

    Regime   Base Gross
  -------- ------------
         3         1.10
         2         1.00
         1         0.80
         0         0.90

Final gross:

final_gross = clip(base_gross × scale, 0, max_gross)

------------------------------------------------------------------------

## Rebalancing

Controlled by:

rebalance_mode = "monthly"

Options: - daily - weekly - monthly (default)

Rebalance occurs **30 minutes after market open**.

------------------------------------------------------------------------

## Key Parameters

  Parameter               Default Description
  --------------------- --------- ------------------------------
  mom_lookback                126 Momentum horizon
  vol_score_window             60 Vol window for scoring
  vol_target_window            20 Vol window for gross scaling
  regime_ma                   200 MA length for regime filters
  target_annual_vol          0.10 Volatility target
  max_gross                  1.30 Max gross exposure
  min_gross                  0.20 Min gross exposure
  min_names_each_side           4 Minimum breadth per side
  max_weight_per_name        0.12 Position cap

Warmup automatically spans the largest lookback.

------------------------------------------------------------------------

## Execution

-   Uses daily resolution data
-   Weights set via `SetHoldings`
-   Positions not in the target set are liquidated
-   Tradeability and valid price required before orders

Rebalance log example:

Rebalance: regime=2 gross=0.95 names=12

------------------------------------------------------------------------

## Default Backtest

Start: 2025‑09‑01\
End: 2026‑02‑20\
Initial Cash: \$100,000\
Brokerage: Interactive Brokers (Margin)\
Benchmark: SPY

------------------------------------------------------------------------

## Limitations

-   Single‑sector strategy; sector drawdowns affect all positions
-   Curve proxy is simplified
-   Vol targeting uses SOXX, not position‑level risk
-   Position caps may limit achievable gross exposure

------------------------------------------------------------------------

## Disclaimer

For research and educational use only.\
No guarantee of profitability or risk control.
# semiconductorLS
