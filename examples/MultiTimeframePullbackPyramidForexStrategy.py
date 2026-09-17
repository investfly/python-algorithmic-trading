from typing import List, Optional

from investfly.models import *


class MultiTimeframePullbackPyramidForexStrategy(TradingStrategy):
    """USD/JPY pullback continuation with winner pyramids and evolving pip protection."""

    SECURITY = Security("USD-JPY", SecurityType.FOREX)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.FOREX, [self.SECURITY.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            executionPolicy=ProtectiveExecutionPolicy.INVESTFLY_MANAGED_ONLY,
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, -60.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PIPS,
                [
                    ProfitTargetTier(50.0, 30.0),
                    ProfitTargetTier(80.0, 30.0),
                    ProfitTargetTier(140.0, 40.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="profit-lock-after-40-pips",
                    trigger=ProtectionTrigger(
                        ProtectionTriggerType.PROFIT_REACHED,
                        profitLevel=ProfitLevelCondition(ProfitThresholdType.PIPS, 40.0),
                    ),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, 20.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="time-based-trailing-stop",
                    trigger=ProtectionTrigger(
                        ProtectionTriggerType.TIME_ELAPSED,
                        timeElapsed=StrategyDuration(36, StrategyDurationUnit.HOURS),
                    ),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.PIPS, 35.0))
                    ),
                ),
            ],
            maxHold=StrategyDuration(120, StrategyDurationUnit.HOURS),
        )
        scaling = ScalingPlan(
            addToWinner=WinnerScaleRule(
                favorableMove=MoveCondition(MoveUnit.PIPS, threshold=35.0),
                addPlan=ScaleAddPlan(
                    maxSteps=1,
                    minIntervalBetweenAdds=StrategyDuration(30, StrategyDurationUnit.MINUTES),
                    firstStepPctOfBase=50.0,
                    stepSizeMultiplier=0.5,
                ),
            )
        )
        return CustomStrategyPolicy(
            SecurityType.FOREX,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
                positionScaling=scaling,
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=35.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=22.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        try:
            pullbackBars = self.dataService.getBars(self.SECURITY, BarInterval.FIFTEEN_MINUTE, 35)
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 50)
            dailyBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_DAY, 35)
        except NoDataException:
            return None
        if len(pullbackBars) < 21 or len(hourlyBars) < 41 or len(dailyBars) < 31:
            return None

        pullbackCloses = [float(bar["close"]) for bar in pullbackBars]
        hourlyCloses = [float(bar["close"]) for bar in hourlyBars]
        dailyCloses = [float(bar["close"]) for bar in dailyBars]
        currentFast = sum(pullbackCloses[-5:]) / 5.0
        currentSlow = sum(pullbackCloses[-20:]) / 20.0
        priorFast = sum(pullbackCloses[-6:-1]) / 5.0
        priorSlow = sum(pullbackCloses[-21:-1]) / 20.0
        hourlyFast = sum(hourlyCloses[-12:]) / 12.0
        hourlySlow = sum(hourlyCloses[-40:]) / 40.0
        hourlyImpulse = hourlyCloses[-1] / hourlyCloses[-13] - 1.0
        dailyAverage = sum(dailyCloses[-30:]) / 30.0
        dailyFiveDayMomentum = dailyCloses[-1] / dailyCloses[-6] - 1.0
        dailyUp = dailyCloses[-1] > dailyAverage
        hourlyTrendStrength = hourlyFast / hourlySlow - 1.0

        side: PositionType | None = None
        bullishRecovery = hourlyImpulse > 0.0007 and (hourlyImpulse <= 0.0012 or dailyFiveDayMomentum < 0.0015)
        bearishRecovery = hourlyImpulse < -0.0007 and (hourlyImpulse >= -0.0012 or dailyFiveDayMomentum > -0.0015)
        if dailyUp and hourlyTrendStrength > 0.00025 and bullishRecovery and priorFast <= priorSlow and currentFast > currentSlow:
            side = PositionType.LONG
        elif not dailyUp and hourlyTrendStrength < -0.00025 and bearishRecovery and priorFast >= priorSlow and currentFast < currentSlow:
            side = PositionType.SHORT

        if side is None:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 12.0),
            orderSpec=OrderSpec(),
            instrumentSelection=DirectInstrumentSelection(),
        )
        existingPolicy = EntryExistingPositionPolicy(
            sameTarget=ExistingTargetPolicy(ExistingTargetAction.IGNORE_NEW_ENTRY),
            oppositeSide=EntryConflictPolicy(EntryConflictAction.CLOSE_OPPOSITE_THEN_OPEN),
        )
        orders = self.services.planOpenOrders(
            OpenOrderRequest(self.SECURITY, execution, side=side, existingPositionPolicy=existingPolicy)
        )
        return orders or None
