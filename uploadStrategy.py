from getpass import getpass
from pathlib import Path

from investfly.api.InvestflyApiClient import InvestflyApiClient
from investfly.models.common.Visibility import Visibility
from investfly.models.strategy.TradingStrategyModel import TradingStrategyModel


def main() -> None:
    strategyPath = Path(__file__).parent / "examples" / "StrategyStarterTemplate.py"
    strategyCode = strategyPath.read_text(encoding="utf-8")
    username = input("Investfly username: ").strip()
    if not username:
        raise SystemExit("An Investfly username is required.")
    password = getpass("Investfly password: ")
    if not password:
        raise SystemExit("An Investfly password is required.")

    api = InvestflyApiClient()
    try:
        api.login(username, password)
        strategy = api.strategyApi.createStrategy(
            TradingStrategyModel(
                strategyName="StrategyStarterTemplate",
                strategyDesc="Educational EMA crossover example with hourly trend confirmation and managed exits.",
                pythonCode=strategyCode,
                visibility=Visibility.PRIVATE,
            )
        )
        print(f"Created private, undeployed strategy: https://app.investfly.com/strategy/{strategy.strategyId}")
        print("Review the rules, backtest, and choose a virtual portfolio in Investfly before any live use.")
    finally:
        if api.isLoggedIn():
            api.logout()


if __name__ == "__main__":
    main()
