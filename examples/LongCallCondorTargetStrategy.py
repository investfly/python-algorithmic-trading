from typing import List, Optional

from investfly.models import *


class LongCallCondorTargetStrategy(TradingStrategy):
    """Centers a long call condor around a moderate upside forecast."""

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
                    id="condor-upper-short-threat",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.65,
                        legId="upperShortCall",
                    ),
                    actions=[
                        OptionCloseLegsAction(
                            scope=OptionActionScope.LEG_IDS,
                            legIds=["upperShortCall", "upperLongCall"],
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="condor-dte-close",
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=2.5, maxGroupNotional=8000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() != 2 or minutes < 11 * 60 or minutes >= 12 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            intraday = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 24)]
            daily = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 15)]
        except NoDataException:
            return None
        intradayForecast = intraday[-1] / (sum(intraday[-12:]) / 12) - 1.0
        dailyTrend = daily[-1] / (sum(daily[-10:]) / 10) - 1.0
        intradayRange = max(intraday[-12:]) / min(intraday[-12:]) - 1.0
        if intradayForecast < 0.003 or intradayForecast > 0.012 or dailyTrend < 0.0 or intradayRange > 0.04:
            return None

        projectedMovePct = min(max(intradayForecast * 2.0, 0.5), 3.0)
        structure = self.buildStructure(projectedMovePct)
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(structure),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["forecastMovePct"] = projectedMovePct
        return orders or None

    def buildStructure(self, projectedMovePct: float) -> OptionStructureSpec:
        lowerSelector = OptionContractSelector(
            28,
            StrikeSelectionMode.PERCENT_OFFSET,
            strikeOffsetPct=max(0.5, projectedMovePct),
        )
        return LongCallCondorSpec(lowerSelector, 10.0)
