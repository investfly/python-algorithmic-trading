from typing import List, Optional

from investfly.models import *


class WheelLifecycleOptionStrategy(TradingStrategy):
    """Runs a scheduled cash-secured-put to covered-call Wheel lifecycle."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        coveredCall = OptionLifecycleStructure(
            id="wheel-covered-call",
            structure=CoveredCallSpec(
                callSelector=OptionContractSelector(
                    14,
                    StrikeSelectionMode.TARGET_DELTA,
                    targetDelta=0.30,
                    minimumStrikeRule=MinimumStrikeRule.AT_OR_ABOVE_COST_BASIS,
                ),
                buyWrite=False,
            ),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
        )
        lifecycle = OptionLifecycleRules(
            structures=[coveredCall],
            rules=[
                OptionLifecycleRule(
                    id="wheel-put-assigned",
                    trigger=OptionOutcomeTrigger(
                        outcome=OptionLifecycleOutcome.ASSIGNED,
                        legId="shortPut",
                        timing=OptionOutcomeTiming.ANY,
                    ),
                    actions=[OptionOpenStructureAction(structureId=coveredCall.id)],
                ),
                OptionLifecycleRule(
                    id="wheel-put-expired",
                    trigger=OptionOutcomeTrigger(
                        outcome=OptionLifecycleOutcome.EXPIRED,
                        legId="shortPut",
                        timing=OptionOutcomeTiming.EXPIRATION,
                    ),
                    actions=[OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE)],
                ),
                OptionLifecycleRule(
                    id="wheel-call-assigned",
                    structureRef=coveredCall.id,
                    trigger=OptionOutcomeTrigger(
                        outcome=OptionLifecycleOutcome.ASSIGNED,
                        legId="shortCall",
                        timing=OptionOutcomeTiming.ANY,
                    ),
                    actions=[
                        OptionLiquidateUnderlyingAction(
                            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY)
                        ),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE),
                    ],
                ),
                OptionLifecycleRule(
                    id="wheel-call-expired",
                    structureRef=coveredCall.id,
                    trigger=OptionOutcomeTrigger(
                        outcome=OptionLifecycleOutcome.EXPIRED,
                        legId="shortCall",
                        timing=OptionOutcomeTiming.EXPIRATION,
                    ),
                    actions=[OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE)],
                ),
            ],
        )
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 35.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 80.0),
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules(groupRisk=groupRisk)),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=35.0, maxGroupNotional=35000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities:
            return None
        try:
            closes = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 32)]
            dailyCloses = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 10)]
        except NoDataException:
            self.state["wheelEntryReady"] = False
            return None
        self.state["wheelEntryReady"] = closes[-1] >= sum(closes[-24:]) / 24 and dailyCloses[-1] >= sum(dailyCloses) / len(dailyCloses)
        return None

    @scheduled(TriggerSchedule.weekly(Weekday.MON, "10:00"))
    def sellWeeklyPut(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        if self.getPortfolio().openPositions or not self.state.get("wheelEntryReady", False):
            return None
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildCashSecuredPut()),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastWheelEntry"] = event.actualTime.isoformat()
        return orders or None

    def buildCashSecuredPut(self) -> OptionStructureSpec:
        return CashSecuredPutSpec(
            OptionContractSelector(14, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.25)
        )

    def buildCoveredCall(self) -> OptionStructureSpec:
        return CoveredCallSpec(
            OptionContractSelector(
                14,
                StrikeSelectionMode.TARGET_DELTA,
                targetDelta=0.30,
                minimumStrikeRule=MinimumStrikeRule.AT_OR_ABOVE_COST_BASIS,
            ),
            buyWrite=False,
        )
