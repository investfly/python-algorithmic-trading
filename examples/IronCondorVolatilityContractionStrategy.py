from typing import List, Optional

from investfly.models import *


class IronCondorVolatilityContractionStrategy(TradingStrategy):
    """Schedules a weekly SPY iron condor only after volatility contracts."""

    UNDERLYING = Security("SPY", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, [self.UNDERLYING.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 15.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 35.0),
        )
        threatenedRoll = OrderSpec(
            orderType=StrategyOrderType.LIMIT_ORDER,
            orderDuration=OrderDuration.DAY,
            unfilledTimeout=StrategyDuration(4, StrategyDurationUnit.BARS, BarInterval.FIFTEEN_MINUTE),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="condor-call-wing-roll",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.35,
                        legId="shortCall",
                    ),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.MUST_BE_NET_CREDIT,
                            orderSpec=threatenedRoll,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="condor-put-wing-roll",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.35,
                        legId="shortPut",
                    ),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.MUST_BE_NET_CREDIT,
                            orderSpec=threatenedRoll,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="condor-dte-close",
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

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities:
            return None
        try:
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 24)
            hourly = self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 12)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        hourlyCloses = [float(bar["close"]) for bar in hourly]
        recentRange = max(closes[-8:]) / min(closes[-8:]) - 1.0
        priorRange = max(closes[-24:-8]) / min(closes[-24:-8]) - 1.0
        hourlyDrift = abs(hourlyCloses[-1] / hourlyCloses[0] - 1.0)
        self.state["contractionReady"] = recentRange <= priorRange * 1.15 and recentRange < 0.025 and hourlyDrift < 0.04
        self.state["recentRange"] = recentRange
        return None

    @scheduled(TriggerSchedule.weekly(Weekday.MON, "10:00"))
    def openWeeklyCondor(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        if self.getPortfolio().openPositions or not self.state.get("contractionReady", True):
            return None
        structure = IronCondorSpec(
            shortPutSelector=OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.18),
            shortCallSelector=OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.18),
            putWingWidth=5.0,
            callWingWidth=5.0,
        )
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(structure),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastCondorEntry"] = event.actualTime.isoformat()
        return orders or None
