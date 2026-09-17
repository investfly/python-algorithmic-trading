from typing import Any, Dict, List, Optional

from investfly.models import *


class MesOpeningRangeBreakoutStrategy(TradingStrategy):
    """Stateful MES opening-range breakout with fixed-contract winner scaling."""

    SECURITY = Security("MES", SecurityType.FUTURE)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromFutureProduct(FutureProduct.MES)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -36.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.TICKS,
                [
                    ProfitTargetTier(24.0, 40.0),
                    ProfitTargetTier(48.0, 30.0),
                    ProfitTargetTier(96.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="mes-orb-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="mes-orb-profit-trail",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=2),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.TICKS, 24.0))
                    ),
                ),
            ],
            maxHold=StrategyDuration(240, StrategyDurationUnit.MINUTES),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:45"),
        )
        scaling = ScalingPlan(
            addToWinner=WinnerScaleRule(
                favorableMove=MoveCondition(MoveUnit.TICKS, threshold=16.0),
                addPlan=ScaleAddPlan(
                    maxSteps=1,
                    minIntervalBetweenAdds=StrategyDuration(10, StrategyDurationUnit.MINUTES),
                    scalingSizeMode=ScalingSizeMode.FIXED_QUANTITY,
                    quantity=1.0,
                ),
            )
        )
        return CustomStrategyPolicy(
            SecurityType.FUTURE,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
                positionScaling=scaling,
            ),
            assetLifecycle=AssetLifecycleRules(
                future=FutureLifecycleRules(FutureRollMode.ROLL_TO_NEXT, daysBeforeExpiry=5)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=25.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0, maxQuantity=2.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 9 * 60 + 30 or currentMinutes > 12 * 60:
            return None

        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        sessionState: Dict[str, Dict[str, Any]] = dict(self.state.get("sessions", {}))
        rangeState: Dict[str, Any] = dict(sessionState.get(sessionDay, {}))
        try:
            minuteBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_MINUTE, 1)
        except NoDataException:
            return None
        currentBar = minuteBars[-1]
        price = float(currentBar["close"])
        if currentMinutes <= 9 * 60 + 44:
            rangeState["high"] = max(float(rangeState.get("high", currentBar["high"])), float(currentBar["high"]))
            rangeState["low"] = min(float(rangeState.get("low", currentBar["low"])), float(currentBar["low"]))
            rangeState["volume"] = float(rangeState.get("volume", 0.0)) + float(currentBar["volume"])
            sessionState[sessionDay] = rangeState
            self.state["sessions"] = sessionState
            return None
        if not rangeState or bool(rangeState.get("traded", False)):
            return None

        try:
            fiveMinuteBars = self.dataService.getBars(self.SECURITY, BarInterval.FIVE_MINUTE, 13)
            fifteenMinuteBars = self.dataService.getBars(self.SECURITY, BarInterval.FIFTEEN_MINUTE, 9)
        except NoDataException:
            return None
        openingHigh = float(rangeState["high"])
        openingLow = float(rangeState["low"])
        recentVolume = float(fiveMinuteBars[-1]["volume"])
        averageVolume = sum(float(bar["volume"]) for bar in fiveMinuteBars[:-1]) / 12.0
        fifteenMinuteAverage = sum(float(bar["close"]) for bar in fifteenMinuteBars[-8:]) / 8.0
        side = None
        if price > openingHigh + 0.25 and price > fifteenMinuteAverage and recentVolume >= averageVolume * 0.45:
            side = PositionType.LONG
        if side is None:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.LIMIT_ORDER,
                orderDuration=OrderDuration.DAY,
                limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.TICKS, 1.0),
                unfilledTimeout=StrategyDuration(2, StrategyDurationUnit.BARS, BarInterval.ONE_MINUTE),
            ),
            instrumentSelection=FutureContractSelection(FutureContractSelector(0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.SECURITY, execution, side=side))
        if orders:
            rangeState["traded"] = True
            rangeState["side"] = side.value
            rangeState["breakoutPrice"] = price
            sessionState[sessionDay] = rangeState
            self.state["sessions"] = sessionState
        return orders or None
