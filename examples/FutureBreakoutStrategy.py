from typing import Dict, List, Optional

from investfly.models import *


class FutureBreakoutStrategy(TradingStrategy):
    """Adaptive intraday MNQ channel breakout with expiry-safe contract rolling."""

    SECURITY = Security("MNQ", SecurityType.FUTURE)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromFutureProduct(FutureProduct.MNQ)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -80.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.TICKS,
                [
                    ProfitTargetTier(80.0, 40.0),
                    ProfitTargetTier(160.0, 30.0),
                    ProfitTargetTier(280.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="mnq-breakout-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="mnq-breakout-trailing",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=2),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.TICKS, 60.0))
                    ),
                ),
            ],
            maxHold=StrategyDuration(8, StrategyDurationUnit.HOURS),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:50"),
        )
        exits = ExitRules(protectiveExits=protectiveExits)
        lifecycle = AssetLifecycleRules(future=FutureLifecycleRules(FutureRollMode.ROLL_TO_NEXT, daysBeforeExpiry=5))
        return CustomStrategyPolicy(
            SecurityType.FUTURE,
            positionManagement=PositionManagementRules(positionExits=exits),
            assetLifecycle=lifecycle,
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=30.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0, maxQuantity=2.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 9 * 60 + 35 or currentMinutes > 15 * 60:
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        tradedSessions: Dict[str, bool] = dict(self.state.get("tradedSessions", {}))
        if tradedSessions.get(sessionDay):
            return None
        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.FIVE_MINUTE, 61)
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 41)
        except NoDataException:
            return None
        if len(bars) < 61 or len(hourlyBars) < 41:
            return None

        hourlyCloses = [float(bar["close"]) for bar in hourlyBars]
        hourlyReturns = [
            abs(hourlyCloses[index] / hourlyCloses[index - 1] - 1.0)
            for index in range(1, len(hourlyCloses))
            if hourlyCloses[index - 1] > 0
        ]
        hourlyVolatility = sum(hourlyReturns[-20:]) / 20.0
        channelLength = 18 if hourlyVolatility >= 0.003 else 30
        channelBars = bars[-channelLength - 1:-1]
        close = float(bars[-1]["close"])
        priorClose = float(bars[-2]["close"])
        priorHigh = max(float(bar["high"]) for bar in channelBars)
        priorLow = min(float(bar["low"]) for bar in channelBars)
        hourlyFast = sum(hourlyCloses[-10:]) / 10.0
        hourlySlow = sum(hourlyCloses[-40:]) / 40.0
        side = None
        if close > priorHigh + 0.25 and priorClose <= priorHigh and hourlyFast > hourlySlow:
            side = PositionType.LONG
        elif close < priorLow - 0.25 and priorClose >= priorLow and hourlyFast < hourlySlow:
            side = PositionType.SHORT
        if side is None:
            return None

        marginGuard = GuardPolicy([
            PortfolioMarginGuard(minBuyingPowerPct=70.0, minMarginBufferPct=70.0, maxLeverage=1.5)
        ])
        decisions = self.services.evaluateGuards(marginGuard, GuardScope.ENTRY_RULE, "mnq-breakout")
        if any(not decision.passed for decision in decisions):
            self.state["lastGuardDecisions"] = [decision.toDict() for decision in decisions]
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.LIMIT_ORDER,
                orderDuration=OrderDuration.DAY,
                limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.TICKS, 1.0),
                unfilledTimeout=StrategyDuration(2, StrategyDurationUnit.BARS, BarInterval.FIVE_MINUTE),
            ),
            instrumentSelection=FutureContractSelection(FutureContractSelector(0)),
        )
        existingPolicy = EntryExistingPositionPolicy(
            sameTarget=ExistingTargetPolicy(ExistingTargetAction.IGNORE_NEW_ENTRY),
            oppositeSide=EntryConflictPolicy(EntryConflictAction.CLOSE_OPPOSITE_THEN_OPEN),
        )
        orders = self.services.planOpenOrders(
            OpenOrderRequest(self.SECURITY, execution, side=side, existingPositionPolicy=existingPolicy)
        )
        if orders:
            tradedSessions[sessionDay] = True
            self.state["tradedSessions"] = tradedSessions
            self.state["lastChannelLength"] = channelLength
        return orders or None
