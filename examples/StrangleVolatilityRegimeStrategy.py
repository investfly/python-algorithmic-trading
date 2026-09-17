from typing import List, Optional

from investfly.models import *


class StrangleVolatilityRegimeStrategy(TradingStrategy):
    """Chooses long or short OTM strangles from range expansion and skew."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 15.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 30.0),
        )
        rollOrder = OrderSpec(
            orderType=StrategyOrderType.LIMIT_ORDER,
            orderDuration=OrderDuration.DAY,
            unfilledTimeout=StrategyDuration(3, StrategyDurationUnit.BARS, BarInterval.FIFTEEN_MINUTE),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="strangle-long-call-delta-roll",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.55,
                        legId="longCall",
                    ),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.ALLOW_NET_DEBIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="strangle-short-call-delta-roll",
                    trigger=OptionMetricTrigger(
                        metric=OptionLifecycleMetric.LEG_DELTA,
                        comparator=ComparisonOperator.GREATER_OR_EQUAL,
                        value=0.40,
                        legId="shortCall",
                    ),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.MUST_BE_NET_CREDIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="strangle-dte-close",
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=9000.0, maxContracts=1),
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
            bars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 28)
            dailyBars = self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 15)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        dailyCloses = [float(bar["close"]) for bar in dailyBars]
        recentRange = max(closes[-8:]) / min(closes[-8:]) - 1.0
        baselineRange = max(closes[-28:-8]) / min(closes[-28:-8]) - 1.0
        dailyMove = abs(dailyCloses[-1] / dailyCloses[-5] - 1.0)
        expanding = recentRange >= baselineRange * 0.75 and dailyMove >= 0.015
        compressing = recentRange <= baselineRange * 0.35 and dailyMove <= 0.01
        if not expanding and not compressing:
            return None
        if compressing:
            decisions = self.services.evaluateGuards(
                GuardPolicy([PortfolioMarginGuard(minBuyingPowerPct=88.0, minMarginBufferPct=92.0, maxLeverage=1.03)]),
                GuardScope.ENTRY_RULE,
                "short-strangle",
            )
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
            self.state["lastSkew"] = (closes[-1] - sum(closes[-8:]) / 8) / closes[-1]
        return orders or None

    def buildStructure(self, expanding: bool) -> OptionStructureSpec:
        putSelector = OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25)
        callSelector = OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25)
        return LongStrangleSpec(putSelector, callSelector) if expanding else ShortStrangleSpec(putSelector, callSelector)
