# Python Algorithmic Trading with the Investfly SDK

Build a Python trading strategy, check it locally, then backtest and paper trade it on [Investfly](https://www.investfly.com/python-algorithmic-trading.html?source=github).
This repository contains **all 37 strategies** from the public `investfly-sdk` sample catalog: **8 stock/ETF, 5 forex, 5 crypto, 5 futures, and 14 options examples**. Explore momentum, mean reversion, rotation, pairs trading, scaling, hedging, and option lifecycle management.

**[Browse all strategies](#sample-strategy-catalog) · [SDK documentation](https://www.investfly.com/apidocs/investfly.html) · [Start Free Trial](https://app.investfly.com/register?source=github)**

## What runs where?

| On your computer | On Investfly |
| --- | --- |
| Edit the Python strategy, inspect its rules, and run static type checks | Run supported historical backtests and virtual portfolio simulations |
| Install the public SDK and upload your strategy | Supply market data, indicators, portfolio context, order planning, and monitoring |

The SDK is an authoring and API toolkit, not a standalone local backtesting engine. Running the strategy file directly does not start trading: Investfly injects its runtime services and invokes the callbacks. Local installation and type checking need no account. Hosted testing and deployment require an Investfly account and an eligible trial or plan; see [current pricing](https://www.investfly.com/pricing.html).

## Quickstart

Requires **Python 3.11 or later** and Git.

```bash
git clone https://github.com/investfly/python-algorithmic-trading.git
cd python-algorithmic-trading
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m mypy --check-untyped-defs examples uploadStrategy.py
```

On Windows, create the environment with `py -3 -m venv .venv` and activate it using `.\.venv\Scripts\Activate.ps1` in PowerShell. Then run the same `python -m pip` and `python -m mypy` commands.

The project pins `investfly-sdk==3.1.2` in [pyproject.toml](pyproject.toml). Its base installation includes mypy. A passing type check validates SDK usage; it does not validate profitability, data availability, fills, or hosted execution.

## Sample strategy catalog

Every strategy below is copied unchanged from the SDK's `investfly/samples/strategies/` directory. Each file defines a standalone sample class; the local `examples/__init__.py` is only a package marker. Descriptions summarize example rules, not expected performance. Asset, data, plan, and broker requirements vary by strategy.

### Stocks and ETFs

| Strategy source | Example behavior |
| --- | --- |
| [StrategyStarterTemplate](examples/StrategyStarterTemplate.py) | A compact intraday EMA starter with runtime-managed risk. |
| [AdaptiveBreadthMomentumStrategy](examples/AdaptiveBreadthMomentumStrategy.py) | Selects technology leaders only when broad-market participation is healthy. |
| [FundamentalMomentumRotationStrategy](examples/FundamentalMomentumRotationStrategy.py) | Rotates weekly into SP-100 stocks with the strongest blended value, quality, momentum, and stability scores. |
| [OpeningRangeBreakoutScaleStrategy](examples/OpeningRangeBreakoutScaleStrategy.py) | Trades confirmed opening-range breakouts with managed pyramiding and an intraday exit ladder. |
| [VwapMeanReversionStrategy](examples/VwapMeanReversionStrategy.py) | Fades statistically unusual intraday VWAP deviations only when the higher-timeframe tape is range-bound. |
| [PairsZScoreStatArbStrategy](examples/PairsZScoreStatArbStrategy.py) | Trades temporary AAPL/MSFT spread dislocations as a coordinated dollar-balanced long/short pair. |
| [VolatilityTargetSectorRotationStrategy](examples/VolatilityTargetSectorRotationStrategy.py) | Rebalances monthly into positively trending sector ETFs using capped covariance-aware inverse-volatility weights. |
| [EventAwareGapFadeStrategy](examples/EventAwareGapFadeStrategy.py) | Fades confirmed opening-gap reversals while excluding news-driven repricing events. |

### Forex

| Strategy source | Example behavior |
| --- | --- |
| [ForexRsiHedgedRangeStrategy](examples/ForexRsiHedgedRangeStrategy.py) | Fades extreme EUR/USD one-minute RSI readings inside a higher-timeframe range and permits broker-supported hedged exposure. |
| [UsdJpyMonthlyMomentumStrategy](examples/UsdJpyMonthlyMomentumStrategy.py) | Takes a monthly USD/JPY position at the London open after daily and hourly regime checks. |
| [LondonBreakoutRetestStrategy](examples/LondonBreakoutRetestStrategy.py) | Trades GBP/USD only after an Asian-range breakout retests the boundary in the trend direction. |
| [ForexCrossSectionMomentumStrategy](examples/ForexCrossSectionMomentumStrategy.py) | Ranks liquid USD currency pairs weekly, holding the strongest long and weakest short. |
| [MultiTimeframePullbackPyramidForexStrategy](examples/MultiTimeframePullbackPyramidForexStrategy.py) | Trades USD/JPY pullback recoveries only when hourly and daily trends agree. |

### Crypto

| Strategy source | Example behavior |
| --- | --- |
| [VolatilityAdaptiveDcaGridCryptoStrategy](examples/VolatilityAdaptiveDcaGridCryptoStrategy.py) | Accumulates BTC on a fixed schedule and at volatility-spaced safety levels with strict caps. |
| [CryptoRelativeStrengthRotationStrategy](examples/CryptoRelativeStrengthRotationStrategy.py) | Rotates weekly into the strongest liquid USD crypto assets while monitoring intraday drawdowns. |
| [CryptoBreakoutPyramidStrategy](examples/CryptoBreakoutPyramidStrategy.py) | Trades liquid BTC and ETH breakouts from intraday compression and pyramids only after confirmation. |
| [CryptoPairsRatioReversionStrategy](examples/CryptoPairsRatioReversionStrategy.py) | Rotates long-only capital toward the relatively undervalued side of the BTC/ETH ratio. |
| [WeekendLiquidityMeanReversionStrategy](examples/WeekendLiquidityMeanReversionStrategy.py) | Fades weekend BTC and ETH liquidity dislocations after a one-minute reversal confirms. |

### Futures

| Strategy source | Example behavior |
| --- | --- |
| [FutureBreakoutStrategy](examples/FutureBreakoutStrategy.py) | Trades adaptive MNQ five-minute channel breaks in the hourly trend direction. |
| [MesOpeningRangeBreakoutStrategy](examples/MesOpeningRangeBreakoutStrategy.py) | Trades one confirmed MES opening-range break per session and adds one contract only after favorable movement. |
| [MclVwapMeanReversionStrategy](examples/MclVwapMeanReversionStrategy.py) | Fades confirmed MCL session-VWAP extremes only inside a bounded hourly volatility regime. |
| [MgcTrendPullbackPyramidStrategy](examples/MgcTrendPullbackPyramidStrategy.py) | Trades MGC pullback recoveries only when fifteen-minute, hourly, and daily trends agree. |
| [M2kRsiRangeScalperStrategy](examples/M2kRsiRangeScalperStrategy.py) | Fades one-minute M2K RSI extremes only while the fifteen-minute market remains range-bound. |

### Options

| Strategy source | Example behavior |
| --- | --- |
| [DirectionalLongOptionRegimeStrategy](examples/DirectionalLongOptionRegimeStrategy.py) | Selects long calls or puts from a five-minute, hourly, and daily directional regime. |
| [AdaptiveDebitSpreadOptionStrategy](examples/AdaptiveDebitSpreadOptionStrategy.py) | Selects bull-call or bear-put debit spreads only when five-minute and hourly trends agree. |
| [AdaptiveCreditSpreadOptionStrategy](examples/AdaptiveCreditSpreadOptionStrategy.py) | Sells a defined-risk credit spread on the side opposite the aligned daily and intraday trend. |
| [IronCondorVolatilityContractionStrategy](examples/IronCondorVolatilityContractionStrategy.py) | Opens a weekly SPY iron condor only after intraday range contraction and bounded hourly drift. |
| [DirectionalButterflyPinStrategy](examples/DirectionalButterflyPinStrategy.py) | Projects a short-horizon AAPL pin and centers a call or put butterfly near that target. |
| [WheelLifecycleOptionStrategy](examples/WheelLifecycleOptionStrategy.py) | Runs a weekly AAPL Wheel from screened put sale through assignment, covered-call income, and completion. |
| [StraddleVolatilityRegimeStrategy](examples/StraddleVolatilityRegimeStrategy.py) | Buys an ATM straddle during realized-volatility expansion and sells one only in guarded compression. |
| [StrangleVolatilityRegimeStrategy](examples/StrangleVolatilityRegimeStrategy.py) | Uses intraday range expansion and daily movement to choose long or guarded short OTM strangles. |
| [CalendarTermStructureStrategy](examples/CalendarTermStructureStrategy.py) | Chooses call or put calendars from aligned intraday/daily direction and inspects the live expiration surface. |
| [DiagonalIncomeTrendStrategy](examples/DiagonalIncomeTrendStrategy.py) | Selects call or put diagonals in aligned intraday/hourly trends and rolls named near/far leg pairs. |
| [ProtectiveHedgeTransitionStrategy](examples/ProtectiveHedgeTransitionStrategy.py) | Buys AAPL with a protective put during a recoverable drawdown, transitions to a collar, then liquidates cleanly. |
| [IronButterflyPinRiskStrategy](examples/IronButterflyPinRiskStrategy.py) | Schedules a SPY iron butterfly only when price remains pinned near session VWAP in a quiet hourly regime. |
| [LongCallCondorTargetStrategy](examples/LongCallCondorTargetStrategy.py) | Centers a long call condor around a moderate AAPL upside forecast derived from intraday and daily context. |
| [CustomRatioBackspreadStrategy](examples/CustomRatioBackspreadStrategy.py) | Opens an explicit one-by-two AAPL call ratio backspread only during confirmed upside acceleration. |

## How the EMA starter works

The source is copied unchanged from the SDK's `investfly/samples/strategies/StrategyStarterTemplate.py`. The symbols and thresholds below are illustrative inputs, not recommendations.

| Part | Example rule |
| --- | --- |
| Universe | AAPL and MSFT stocks |
| Evaluation | 15-minute bar updates |
| Entry | 9-period EMA crosses above 21-period EMA on 15-minute bars, while hourly EMA(20) exceeds EMA(50) |
| Direction | Long positions |
| Sizing | ATR(14) on 15-minute bars determines an allocation between 4% and 15% of equity |
| Signal exit | Fast EMA crosses below slow EMA, or the hourly trend confirmation fails |
| Initial stop | 4% below entry |
| Staged targets | Configured partial exits at 4% and 8% gains, with 35% close inputs for each tier |
| Stop adjustments | Move the stop to entry after target one; use a 2% trailing stop after target two |
| Maximum hold | 10 days |
| Portfolio limits | Two open positions, one per symbol, and a 20% per-position exposure cap |

Three SDK concepts connect the strategy:

- `getSecurityUniverseSelector()` declares which securities the strategy observes.
- `getStrategyPolicy()` describes position management and portfolio limits enforced by the hosted runtime.
- `onMarketData()` reads indicators and requests open/close order plans through `self.services`.

## Upload a private draft

1. [Create your Investfly account](https://app.investfly.com/register?source=github).
2. Read the example and choose the rules you want to test.
3. From the activated project environment, choose a sample by its class name (the filename without `.py`):

   ```bash
   python uploadStrategy.py VwapMeanReversionStrategy

   # Omit the name to upload StrategyStarterTemplate.
   python uploadStrategy.py

   # List every available sample and command usage.
   python uploadStrategy.py --help
   ```

4. Enter your **Investfly** username and password at the prompts. The password input is hidden; the script does not write credentials to files or put them in command-line arguments.
5. Open the strategy URL printed by the script.

The helper uses the selected file's contents and class name to create a **private, undeployed draft**, then logs out afterward. It does not connect a broker, start a backtest, deploy a strategy, or place orders. Running it again requests another creation; edit the existing strategy in the app if you only want to revise its code.

The SDK also includes the interactive `investfly-cli`; enter `-h` at its prompt to explore its commands. See the [hosted Python strategy guide](https://www.investfly.com/help/pythonHostedTradingBot.html).

## Backtest and paper trade

1. Open the saved strategy in Investfly and inspect its code and settings.
2. Open the Backtest tab and choose a supported historical period. Review trades, drawdown, assumptions, and data limitations, not just the headline return.
3. Create a compatible Investfly virtual portfolio. In the strategy's Deployment tab, select that virtual portfolio and review compatibility before authorizing the simulation.
4. Observe orders, positions, and automation logs. Compare the behavior with your intended rules before making further changes.

See [backtesting](https://www.investfly.com/help/backtestTradingStrategy.html) and [working with a strategy](https://www.investfly.com/help/workingWithAutomatedStrategy.html). Broker-connected live use is a separate decision and depends on plan, provider, account, instrument, and permissions.

## Make it your own

- Change the symbols in `getSecurityUniverseSelector()`.
- Modify EMA periods and the confirmation timeframe in `onMarketData()`.
- Review the ATR allocation calculation and portfolio exposure limits together.
- Adjust stops, targets, protection changes, and maximum hold in `getStrategyPolicy()`.
- Re-run the type check after editing, then review the revised strategy in hosted testing.

Use the [SDK API reference](https://www.investfly.com/apidocs/investfly.html) to inspect supported types and interfaces. To also copy the SDK's bundled indicator examples, launch `investfly-cli` and enter `copysamples` at its interactive prompt.

## Files

```text
examples/*.py        All 37 original SDK sample strategies
examples/__init__.py  Example package marker
uploadStrategy.py    Upload any selected sample as a private draft
pyproject.toml       Python version and public SDK dependency
LICENSE              MIT license from the SDK
```

## Limits and support

This is educational example code, not investment advice or a performance claim. No performance result is claimed by this repository. Backtests and virtual portfolios are hypothetical; actual execution, fees, slippage, liquidity, and market data can differ. Stops and other configured controls do not guarantee execution or prevent losses. Review the full code and use simulation before considering live trading.

For example-code questions, open a GitHub issue without including passwords, API tokens, account details, or private trading records. For account help, contact [Investfly support](mailto:support@investfly.com).

## License

[MIT](LICENSE). Original example copyright belongs to Investfly, Finverse LLC, as stated in the SDK license. The open-source example license does not include hosted Investfly services.
