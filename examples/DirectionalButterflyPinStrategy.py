from typing import List, Optional

from investfly.models import *


class DirectionalButterflyPinStrategy(TradingStrategy):
    """Projects a short-horizon pin and selects a call or put butterfly."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 10.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 30.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="butterfly-loss-flatten",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LOSS_PERCENT,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=30.0,
                    ),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE),
                    ],
                ),
                OptionLifecycleRule(
                    id="butterfly-dte-close",
                    trigger=OptionDteTrigger(atOrBelow=4),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.WAIT_UNTIL_ORIGINAL_EXPIRATION),
                    ],
                ),
            ]
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules(groupRisk=groupRisk)),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=2.5, maxGroupNotional=6000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 11 * 60 or minutes > 12 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 24)
            hourly = self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 10)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        hourlyCloses = [float(bar["close"]) for bar in hourly]
        slope = self._linearSlope(closes[-12:])
        hourlySlope = self._linearSlope(hourlyCloses[-8:])
        if slope * hourlySlope <= 0:
            return None
        projectedPrice = closes[-1] + slope * 12
        projectedMovePct = projectedPrice / closes[-1] - 1.0
        hourlyProjectedMovePct = hourlySlope * 8 / closes[-1]
        if abs(projectedMovePct) < 0.0015 or abs(projectedMovePct) > 0.003:
            return None
        if projectedMovePct >= 0 or hourlyProjectedMovePct < -0.024 or hourlyProjectedMovePct > -0.018:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(projectedMovePct > 0, projectedMovePct)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["projectedPrice"] = projectedPrice
        return orders or None

    def buildStructure(self, bullish: bool, projectedMovePct: float = 0.0) -> OptionStructureSpec:
        offsetPct = min(max(abs(projectedMovePct) * 100.0, 0.5), 4.0)
        selector = OptionContractSelector(21, StrikeSelectionMode.PERCENT_OFFSET, strikeOffsetPct=offsetPct)
        return CallButterflySpec(selector, 10.0) if bullish else PutButterflySpec(selector, 10.0)

    def _linearSlope(self, values: List[float]) -> float:
        center = (len(values) - 1) / 2.0
        denominator = sum((index - center) ** 2 for index in range(len(values)))
        return sum((index - center) * value for index, value in enumerate(values)) / denominator
