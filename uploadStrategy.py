from argparse import ArgumentParser
from getpass import getpass
from pathlib import Path

from investfly.api.InvestflyApiClient import InvestflyApiClient
from investfly.models.common.Visibility import Visibility
from investfly.models.strategy.TradingStrategyModel import TradingStrategyModel


def main() -> None:
    examplesPath = Path(__file__).parent / "examples"
    strategyNames = sorted(path.stem for path in examplesPath.glob("*.py") if path.stem != "__init__")
    parser = ArgumentParser(description="Upload a bundled sample as a private, undeployed Investfly strategy.")
    parser.add_argument("strategy", nargs="?", default="StrategyStarterTemplate", choices=strategyNames, help="Sample class name; defaults to StrategyStarterTemplate.")
    args = parser.parse_args()
    strategyPath = examplesPath / f"{args.strategy}.py"
    strategyCode = strategyPath.read_text(encoding="utf-8")
    print(f"Uploading {strategyPath.name} as a private, undeployed draft.")
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
                strategyName=args.strategy,
                strategyDesc=f"Educational Investfly SDK sample: {args.strategy}.",
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
