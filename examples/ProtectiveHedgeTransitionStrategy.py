from typing import List, Optional

from investfly.models import *


class ProtectiveHedgeTransitionStrategy(TradingStrategy):
    """Buys stock with a protective put, then transitions the hedge to a collar."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        collar = OptionLifecycleStructure(
            id="income-collar",
            structure=self.buildCollar(),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
        )
        lifecycle = OptionLifecycleRules(
            structures=[collar],
            rules=[
                OptionLifecycleRule(
                    id="protective-put-to-collar",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LOSS_PERCENT,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=25.0,
                    ),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionOpenStructureAction(structureId=collar.id),
                    ],
                ),
                OptionLifecycleRule(
                    id="protective-put-dte-transition",
                    trigger=OptionDteTrigger(atOrBelow=4),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionOpenStructureAction(structureId=collar.id),
                    ],
                ),
                OptionLifecycleRule(
                    id="collar-completion",
                    structureRef=collar.id,
                    trigger=OptionDteTrigger(atOrBelow=3),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionLiquidateUnderlyingAction(
                            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY)
                        ),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.DO_NOT_REENTER),
                    ],
                ),
            ]
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules()),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=8.0, maxGroupNotional=40000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions or self.state.get("hedgeOpened"):
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or minutes < 10 * 60 or minutes > 11 * 60:
            return None
        try:
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 20)
            dailyBars = self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 15)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        dailyCloses = [float(bar["close"]) for bar in dailyBars]
        drawdown = dailyCloses[-1] / max(dailyCloses) - 1.0
        intradayRecovery = closes[-1] > sum(closes[-8:]) / 8
        if drawdown > -0.08 or drawdown < -0.18 or not intradayRecovery:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildProtectivePut()),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["hedgeOpened"] = True
            self.state["entryDrawdown"] = drawdown
        return orders or None

    def buildProtectivePut(self) -> OptionStructureSpec:
        return ProtectivePutSpec(
            OptionContractSelector(30, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.30),
            buyStock=True,
        )

    def buildCollar(self) -> OptionStructureSpec:
        return CollarSpec(
            putSelector=OptionContractSelector(30, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25),
            callSelector=OptionContractSelector(30, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25),
            buyStock=False,
        )
