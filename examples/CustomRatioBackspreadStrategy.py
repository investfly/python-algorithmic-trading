from typing import List, Optional

from investfly.models import *


class CustomRatioBackspreadStrategy(TradingStrategy):
    """Builds an explicit one-by-two call backspread during upside acceleration."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 18.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 35.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="ratio-short-call-threat",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.70,
                        legId="shortCall",
                    ),
                    actions=[
                        OptionCloseLegsAction(
                            scope=OptionActionScope.LEG_IDS,
                            legIds=["shortCall"],
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="ratio-backspread-dte-close",
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=10.0, maxGroupNotional=20000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 12 * 60 + 30 or minutes > 13 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            fastBars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 6)
            slowBars = self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 10)
        except NoDataException:
            return None
        fast = [float(bar["close"]) for bar in fastBars]
        slow = [float(bar["close"]) for bar in slowBars]
        recentMomentum = fast[-1] / fast[-3] - 1.0
        priorMomentum = fast[-3] / fast[-6] - 1.0
        hourlyTrend = slow[-1] / (sum(slow[-10:]) / 10) - 1.0
        acceleration = recentMomentum - priorMomentum
        if recentMomentum < 0.001 or acceleration < -0.003 or hourlyTrend < -0.02:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure()),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["lastAcceleration"] = acceleration
        return orders or None

    def buildStructure(self) -> OptionStructureSpec:
        shortSelector = OptionContractSelector(28, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.55)
        return CustomComboSpec(
            [
                OptionLegSpec(
                    legId="shortCall",
                    action=OptionLegAction.SELL_TO_OPEN,
                    optionRight=OptionType.CALL,
                    quantityRatio=1,
                    contractSelector=shortSelector,
                ),
                OptionLegSpec(
                    legId="longCalls",
                    action=OptionLegAction.BUY_TO_OPEN,
                    optionRight=OptionType.CALL,
                    quantityRatio=2,
                    linkedToLegId="shortCall",
                    widthFromLinked=5.0,
                ),
            ]
        )
