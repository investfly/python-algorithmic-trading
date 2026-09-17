from typing import Dict, List, Optional

from investfly.models import *


class MgcTrendPullbackPyramidStrategy(TradingStrategy):
    """Multi-timeframe MGC pullback continuation with one fixed-contract pyramid step."""

    SECURITY = Security("MGC", SecurityType.FUTURE)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromFutureProduct(FutureProduct.MGC)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -80.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.R_MULTIPLE,
                [
                    ProfitTargetTier(1.0, 40.0),
                    ProfitTargetTier(2.0, 30.0),
                    ProfitTargetTier(3.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="mgc-target-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="mgc-time-tighten",
                    trigger=ProtectionTrigger(
                        ProtectionTriggerType.TIME_ELAPSED,
                        timeElapsed=StrategyDuration(75, StrategyDurationUnit.MINUTES),
                    ),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -30.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="mgc-profit-trail",
                    trigger=ProtectionTrigger(
                        ProtectionTriggerType.PROFIT_REACHED,
                        profitLevel=ProfitLevelCondition(ProfitThresholdType.R_MULTIPLE, 1.5),
                    ),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.TICKS, 50.0))
                    ),
                ),
            ],
            maxHold=StrategyDuration(300, StrategyDurationUnit.MINUTES),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:45"),
        )
        scaling = ScalingPlan(
            addToWinner=WinnerScaleRule(
                favorableMove=MoveCondition(MoveUnit.TICKS, threshold=50.0),
                addPlan=ScaleAddPlan(
                    maxSteps=1,
                    minIntervalBetweenAdds=StrategyDuration(30, StrategyDurationUnit.MINUTES),
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
                future=FutureLifecycleRules(FutureRollMode.ROLL_TO_NEXT, daysBeforeExpiry=8)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=25.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0, maxQuantity=2.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 8 * 60 or currentMinutes > 14 * 60 + 30:
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        tradedSessions: Dict[str, str] = dict(self.state.get("tradedSessions", {}))
        if sessionDay in tradedSessions or self.getPortfolio().openPositions:
            return None

        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.FIFTEEN_MINUTE, 25)
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 25)
            dailyBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_DAY, 41)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        hourlyCloses = [float(bar["close"]) for bar in hourlyBars]
        dailyCloses = [float(bar["close"]) for bar in dailyBars]
        currentPrice = closes[-1]
        priorPrice = closes[-2]
        currentPullbackAverage = sum(closes[-6:]) / 6.0
        priorPullbackAverage = sum(closes[-7:-1]) / 6.0
        hourlyFast = sum(hourlyCloses[-6:]) / 6.0
        hourlySlow = sum(hourlyCloses[-24:]) / 24.0
        dailyFast = sum(dailyCloses[-10:]) / 10.0
        dailySlow = sum(dailyCloses[-40:]) / 40.0
        side = None
        if dailyFast > dailySlow and hourlyFast > hourlySlow and priorPrice <= priorPullbackAverage and currentPrice > currentPullbackAverage:
            side = PositionType.LONG
        elif dailyFast < dailySlow and hourlyFast < hourlySlow and priorPrice >= priorPullbackAverage and currentPrice < currentPullbackAverage:
            side = PositionType.SHORT
        if side is None:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(),
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
            tradedSessions[sessionDay] = side.value
            self.state["tradedSessions"] = tradedSessions
            self.state["entryTrend"] = {
                "dailyFast": dailyFast,
                "dailySlow": dailySlow,
                "hourlyFast": hourlyFast,
                "hourlySlow": hourlySlow,
            }
        return orders or None
