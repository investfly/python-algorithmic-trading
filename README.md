# Python Algorithmic Trading with the Investfly SDK

Build a Python trading strategy, check it locally, then backtest and paper trade it on [Investfly](https://www.investfly.com/python-algorithmic-trading.html?source=github).
This repository contains a complete **EMA crossover strategy** from the public `investfly-sdk` sample catalog, with hourly trend confirmation, volatility-based position sizing, and managed exits.

**[Read the strategy](examples/StrategyStarterTemplate.py) · [SDK documentation](https://www.investfly.com/apidocs/investfly.html) · [Start Free Trial](https://app.investfly.com/register?source=github)**

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

## How the example works

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
3. From the activated project environment, run:

   ```bash
   python uploadStrategy.py
   ```

4. Enter your **Investfly** username and password at the prompts. The password input is hidden; the script does not write credentials to files or put them in command-line arguments.
5. Open the strategy URL printed by the script.

The helper creates a **private, undeployed draft** and logs out afterward. It does not connect a broker, start a backtest, deploy a strategy, or place orders. Running it again requests another creation; edit the existing strategy in the app if you only want to revise its code.

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

Use the [SDK API reference](https://www.investfly.com/apidocs/investfly.html) to inspect supported types and interfaces. For additional examples, launch `investfly-cli` and enter `copysamples` at its interactive prompt; this copies the SDK's bundled strategies and indicators locally.

## Files

```text
examples/StrategyStarterTemplate.py  Original SDK EMA strategy example
examples/__init__.py                 Example package
uploadStrategy.py                   Credential-prompting private draft upload
pyproject.toml                      Python version and public SDK dependency
LICENSE                            MIT license from the SDK
```

## Limits and support

This is educational example code, not investment advice or a performance claim. No performance result is claimed by this repository. Backtests and virtual portfolios are hypothetical; actual execution, fees, slippage, liquidity, and market data can differ. Stops and other configured controls do not guarantee execution or prevent losses. Review the full code and use simulation before considering live trading.

For example-code questions, open a GitHub issue without including passwords, API tokens, account details, or private trading records. For account help, contact [Investfly support](mailto:support@investfly.com).

## License

[MIT](LICENSE). Original example copyright belongs to Investfly, Finverse LLC, as stated in the SDK license. The open-source example license does not include hosted Investfly services.
