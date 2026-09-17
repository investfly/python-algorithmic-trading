from typing import List, Optional

from investfly.models import *


class AdaptiveCreditSpreadOptionStrategy(TradingStrategy):
    """Sells a bull-put or bear-call spread away from the dominant daily range."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 12.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 30.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="credit-spread-dte-close",
                    trigger=OptionDteTrigger(atOrBelow=4),
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=7000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 10 * 60 or minutes > 12 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            intraday = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 20)]
            daily = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 15)]
        except NoDataException:
            return None
        intradayMean = sum(intraday[-12:]) / 12
        dailyMean = sum(daily[-10:]) / 10
        bullish = intraday[-1] >= intradayMean and daily[-1] >= dailyMean
        bearish = intraday[-1] <= intradayMean and daily[-1] <= dailyMean
        if not bullish and not bearish:
            return None

        guards = GuardPolicy([
            PortfolioMarginGuard(minBuyingPowerPct=70.0, minMarginBufferPct=70.0, maxLeverage=1.25)
        ])
        decisions = self.services.evaluateGuards(guards, GuardScope.ENTRY_RULE, "adaptive-credit-spread")
        if any(not decision.passed for decision in decisions):
            self.state["lastGuardDecisions"] = [decision.toDict() for decision in decisions]
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(bullish)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["soldAwayFrom"] = min(intraday[-12:]) if bullish else max(intraday[-12:])
        return orders or None

    def buildStructure(self, bullish: bool) -> OptionStructureSpec:
        selector = OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25)
        return BullPutCreditSpreadSpec(selector, 5.0) if bullish else BearCallCreditSpreadSpec(selector, 5.0)
