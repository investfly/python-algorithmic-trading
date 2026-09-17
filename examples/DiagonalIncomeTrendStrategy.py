from typing import List, Optional

from investfly.models import *


class DiagonalIncomeTrendStrategy(TradingStrategy):
    """Selects call or put diagonals and rolls their named legs as a pair."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 12.0),
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
                    id="call-diagonal-combined-roll",
                    trigger=OptionAllOfTrigger(triggers=[
                        OptionDteTrigger(atOrBelow=4, legId="nearShortCall"),
                        OptionMetricTrigger(
                            metric=OptionLifecycleMetric.LEG_DELTA,
                            comparator=ComparisonOperator.GREATER_OR_EQUAL,
                            value=0.45,
                            legId="nearShortCall",
                        ),
                    ]),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.LEG_IDS,
                            legIds=["nearShortCall", "farLongCall"],
                            creditRequirement=OptionRollCreditRequirement.ALLOW_NET_DEBIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="put-diagonal-combined-roll",
                    trigger=OptionAllOfTrigger(triggers=[
                        OptionDteTrigger(atOrBelow=4, legId="nearShortPut"),
                        OptionMetricTrigger(
                            metric=OptionLifecycleMetric.LEG_DELTA,
                            comparator=ComparisonOperator.GREATER_OR_EQUAL,
                            value=0.45,
                            legId="nearShortPut",
                        ),
                    ]),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.LEG_IDS,
                            legIds=["nearShortPut", "farLongPut"],
                            creditRequirement=OptionRollCreditRequirement.ALLOW_NET_DEBIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="call-diagonal-far-close",
                    trigger=OptionDteTrigger(atOrBelow=5, legId="farLongCall"),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.WAIT_UNTIL_ORIGINAL_EXPIRATION),
                    ],
                ),
                OptionLifecycleRule(
                    id="put-diagonal-far-close",
                    trigger=OptionDteTrigger(atOrBelow=5, legId="farLongPut"),
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=10000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 10 * 60 or minutes > 11 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            intraday = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 20)]
            hourly = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 12)]
        except NoDataException:
            return None
        intradayTrend = intraday[-1] / (sum(intraday[-12:]) / 12) - 1.0
        hourlyTrend = hourly[-1] / (sum(hourly[-10:]) / 10) - 1.0
        if intradayTrend * hourlyTrend <= 0 or abs(intradayTrend) < 0.001:
            return None
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(intradayTrend > 0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["diagonalDirection"] = "call" if intradayTrend > 0 else "put"
        return orders or None

    def buildStructure(self, bullish: bool) -> OptionStructureSpec:
        near = OptionContractSelector(14, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.30)
        far = OptionContractSelector(45, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.60)
        return LongCallDiagonalSpreadSpec(near, far) if bullish else LongPutDiagonalSpreadSpec(near, far)
