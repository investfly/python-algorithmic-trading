from typing import List, Optional

from investfly.models import *


class StraddleVolatilityRegimeStrategy(TradingStrategy):
    """Switches between long and short ATM straddles as volatility expands or compresses."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 12.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 25.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="straddle-dte-close",
                    trigger=OptionDteTrigger(atOrBelow=2),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.WAIT_UNTIL_ORIGINAL_EXPIRATION),
                    ],
                )
            ]
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules(groupRisk=groupRisk)),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=8000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 10 * 60 or minutes > 11 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 30)
            dailyBars = self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 15)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        dailyCloses = [float(bar["close"]) for bar in dailyBars]
        recentRange = max(closes[-8:]) / min(closes[-8:]) - 1.0
        baselineRange = max(closes[-30:-8]) / min(closes[-30:-8]) - 1.0
        dailyRealized = sum(abs(dailyCloses[index] / dailyCloses[index - 1] - 1.0) for index in range(1, len(dailyCloses))) / (len(dailyCloses) - 1)
        expanding = recentRange > baselineRange * 0.9 and recentRange > dailyRealized * 0.35
        compressing = recentRange < baselineRange * 0.45 and recentRange < dailyRealized * 0.20
        if not expanding and not compressing:
            return None
        if compressing:
            guards = GuardPolicy([
                PortfolioMarginGuard(minBuyingPowerPct=85.0, minMarginBufferPct=90.0, maxLeverage=1.05)
            ])
            decisions = self.services.evaluateGuards(guards, GuardScope.ENTRY_RULE, "short-straddle")
            if any(not decision.passed for decision in decisions):
                return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(expanding)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["lastVolatilityRegime"] = "expanding" if expanding else "compressing"
        return orders or None

    def buildStructure(self, expanding: bool) -> OptionStructureSpec:
        selector = OptionContractSelector(14, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.50)
        return LongStraddleSpec(selector) if expanding else ShortStraddleSpec(selector)
