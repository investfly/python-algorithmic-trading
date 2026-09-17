from typing import Dict, List, Optional

from investfly.models import *


class VolatilityAdaptiveDcaGridCryptoStrategy(TradingStrategy):
    """BTC accumulation with scheduled contributions and volatility-spaced safety orders."""

    SECURITY = Security("BTC-USD", SecurityType.CRYPTO)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, [self.SECURITY.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -12.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(3.0, 40.0),
                    ProfitTargetTier(6.0, 30.0),
                    ProfitTargetTier(10.0, 30.0),
                ],
            ),
            maxHold=StrategyDuration(30, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            SecurityType.CRYPTO,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=30.0),
            ),
        )

    @scheduled(
        TriggerSchedule.everyFifteenMinutes(
            days=ScheduleDayMode.EVERYDAY,
            window=ScheduleWindow("00:00", "23:59"),
        )
    )
    def scheduledContribution(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        now = self.context.currentTime
        contributionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.hour != 0 or now.minute != 0 or self.state.get("lastContributionDay") == contributionDay:
            return None
        if not self._cryptoRegimeAllowsAccumulation():
            self.state["lastGuardDecision"] = "BTC below its 50-day average"
            return None

        orders = self._planAccumulationOrder()
        if orders:
            self.state["lastContributionDay"] = contributionDay
            self.state["lastGuardDecision"] = "passed"
        return orders or None

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not any(security.symbol == self.SECURITY.symbol for security in updatedSecurities):
            return None
        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.FIVE_MINUTE, 49)
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 25)
        except NoDataException:
            return None
        if len(bars) < 49 or len(hourlyBars) < 25:
            return None

        closes = [float(bar["close"]) for bar in bars]
        trueRanges = [
            max(
                float(bars[index]["high"]) - float(bars[index]["low"]),
                abs(float(bars[index]["high"]) - float(bars[index - 1]["close"])),
                abs(float(bars[index]["low"]) - float(bars[index - 1]["close"])),
            )
            for index in range(1, len(bars))
        ]
        averageTrueRangePct = sum(trueRanges[-24:]) / 24.0 / max(closes[-1], 0.0001)
        gridSpacingPct = min(2.5, max(0.35, averageTrueRangePct * 100.0 * 2.0))
        lastAddPrice = float(self.state.get("lastAddPrice", closes[-1]))
        hourlyCloses = [float(bar["close"]) for bar in hourlyBars]
        hourlyAverage = sum(hourlyCloses[-24:]) / 24.0
        recentReturn = closes[-1] / closes[-7] - 1.0
        oversold = closes[-1] <= lastAddPrice * (1.0 - gridSpacingPct / 100.0)
        stabilizing = recentReturn > -0.015 and closes[-1] >= min(closes[-3:])
        regimeHealthy = closes[-1] >= hourlyAverage * 0.94
        if not oversold or not stabilizing or not regimeHealthy:
            return None

        orders = self._planAccumulationOrder()
        if orders:
            gridState: Dict[str, float] = dict(self.state.get("grid", {}))
            gridState["spacingPct"] = gridSpacingPct
            gridState["lastPrice"] = closes[-1]
            self.state["grid"] = gridState
            self.state["lastAddPrice"] = closes[-1]
        return orders or None

    def _planAccumulationOrder(self) -> List[TradeOrder]:
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_NOTIONAL, 1000.0),
            orderSpec=OrderSpec(),
            instrumentSelection=DirectInstrumentSelection(),
        )
        existingPolicy = EntryExistingPositionPolicy(
            sameTarget=ExistingTargetPolicy(
                ExistingTargetAction.ADD_TO_EXISTING,
                RepeatSignalAddRule(
                    onlyIfWinning=False,
                    addPlan=ScaleAddPlan(
                        maxSteps=4,
                        minIntervalBetweenAdds=StrategyDuration(15, StrategyDurationUnit.MINUTES),
                        firstStepPctOfBase=35.0,
                        stepSizeMultiplier=1.0,
                    ),
                ),
            ),
            oppositeSide=EntryConflictPolicy(EntryConflictAction.IGNORE),
        )
        return self.services.planOpenOrders(
            OpenOrderRequest(self.SECURITY, execution, existingPositionPolicy=existingPolicy)
        )

    def _cryptoRegimeAllowsAccumulation(self) -> bool:
        try:
            dailyBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_DAY, 51)
        except NoDataException:
            return False
        if len(dailyBars) < 51:
            return False
        policy = GuardPolicy([
            CryptoMarketRegimeGuard(
                referenceSecurity=self.SECURITY,
                rule=MarketRegimeRule.PRICE_ABOVE_SMA,
                lookbackPeriods=50,
            )
        ])
        decisions = self.services.evaluateGuards(policy, GuardScope.SCHEDULED_JOB, "btc-dca")
        return all(decision.passed for decision in decisions)
