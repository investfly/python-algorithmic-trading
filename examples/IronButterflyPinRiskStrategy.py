from typing import List, Optional

from investfly.models import *


class IronButterflyPinRiskStrategy(TradingStrategy):
    """Schedules a centered iron butterfly when price is pinned near session VWAP."""

    UNDERLYING = Security("SPY", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, [self.UNDERLYING.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 15.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 30.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="iron-fly-loss-flatten",
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
                    id="iron-fly-dte-close",
                    trigger=OptionDteTrigger(atOrBelow=3),
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=4.0, maxGroupNotional=10000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities:
            return None
        try:
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 20)
            hourly = self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 10)
        except NoDataException:
            return None
        typicalPrices = [(float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0 for bar in bars]
        volumes = [max(float(bar.get("volume", 0.0)), 1.0) for bar in bars]
        sessionVwap = sum(price * volume for price, volume in zip(typicalPrices, volumes)) / sum(volumes)
        hourlyCloses = [float(bar["close"]) for bar in hourly]
        distance = abs(typicalPrices[-1] / sessionVwap - 1.0)
        hourlyDrift = abs(hourlyCloses[-1] / hourlyCloses[0] - 1.0)
        self.state["pinReady"] = distance < 0.006 and hourlyDrift < 0.035
        self.state["pinDistance"] = distance
        return None

    @scheduled(TriggerSchedule.weekly(Weekday.WED, "11:00"))
    def openScheduledIronFly(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        if self.getPortfolio().openPositions or not self.state.get("pinReady", False):
            return None
        structure = IronButterflySpec(
            bodySelector=OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.50),
            wingWidth=10.0,
        )
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(structure),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastIronFlyEntry"] = event.actualTime.isoformat()
        return orders or None
