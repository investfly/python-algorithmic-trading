from typing import Dict, List, Optional

from investfly.models import *


class CryptoBreakoutPyramidStrategy(TradingStrategy):
    """One-minute BTC/ETH compression breakouts with managed winner pyramids."""

    SYMBOLS = ["BTC-USD", "ETH-USD"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -1.8))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(0.8, 35.0),
                    ProfitTargetTier(1.5, 35.0),
                    ProfitTargetTier(3.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="crypto-breakout-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="crypto-breakout-trailing",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=2),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.PERCENT, 0.75))
                    ),
                ),
            ],
            maxHold=StrategyDuration(30, StrategyDurationUnit.HOURS),
        )
        scaling = ScalingPlan(
            addToWinner=WinnerScaleRule(
                favorableMove=MoveCondition(MoveUnit.PERCENT, threshold=0.35),
                addPlan=ScaleAddPlan(
                    maxSteps=2,
                    minIntervalBetweenAdds=StrategyDuration(15, StrategyDurationUnit.MINUTES),
                    firstStepPctOfBase=40.0,
                    stepSizeMultiplier=0.5,
                ),
            )
        )
        return CustomStrategyPolicy(
            SecurityType.CRYPTO,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
                positionScaling=scaling,
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=42.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        tradedSessions: Dict[str, bool] = dict(self.state.get("tradedSessions", {}))
        lastEntryDays: Dict[str, int] = dict(self.state.get("lastEntryDays", {}))
        orders: List[TradeOrder] = []
        portfolio = self.getPortfolio()
        now = self.context.currentTime
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        dayNumber = now.year * 372 + now.month * 31 + now.day
        for security in updatedSecurities:
            if security.symbol not in self.SYMBOLS:
                continue
            stateKey = f"{sessionDay}:{security.symbol}"
            if tradedSessions.get(stateKey):
                continue
            if dayNumber - int(lastEntryDays.get(security.symbol, -1000)) < 3:
                continue
            if portfolio.findPosition(security, PositionType.LONG) is not None:
                continue
            try:
                minuteBars = self.dataService.getBars(security, BarInterval.ONE_MINUTE, 91)
                fifteenMinuteBars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 33)
                hourlyBars = self.dataService.getBars(security, BarInterval.SIXTY_MINUTE, 25)
            except NoDataException:
                continue
            if len(minuteBars) < 91 or len(fifteenMinuteBars) < 33 or len(hourlyBars) < 25:
                continue

            closes = [float(bar["close"]) for bar in minuteBars]
            priorHigh = max(float(bar["high"]) for bar in minuteBars[-61:-1])
            priorLow = min(float(bar["low"]) for bar in minuteBars[-61:-1])
            compressionPct = (priorHigh - priorLow) / max(closes[-2], 0.0001) * 100.0
            recentVolume = sum(float(bar["volume"]) for bar in minuteBars[-5:]) / 5.0
            baselineVolume = sum(float(bar["volume"]) for bar in minuteBars[-65:-5]) / 60.0
            fifteenCloses = [float(bar["close"]) for bar in fifteenMinuteBars]
            hourlyCloses = [float(bar["close"]) for bar in hourlyBars]
            fifteenTrend = sum(fifteenCloses[-8:]) / 8.0 > sum(fifteenCloses[-32:]) / 32.0
            fifteenMinuteBreakout = closes[-1] > max(float(bar["high"]) for bar in fifteenMinuteBars[-17:-1]) * 1.0001
            hourlyTrend = hourlyCloses[-1] > sum(hourlyCloses[-24:]) / 24.0
            breakout = closes[-1] > priorHigh * 1.0002 and closes[-2] <= priorHigh
            liquidBreakout = recentVolume >= baselineVolume * 0.85
            if compressionPct > 1.8 or not breakout or not liquidBreakout or not fifteenTrend or not fifteenMinuteBreakout or not hourlyTrend:
                continue

            execution = OpenExecutionSettings(
                positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 25.0),
                orderSpec=OrderSpec(
                    orderType=StrategyOrderType.LIMIT_ORDER,
                    orderDuration=OrderDuration.GTC,
                    limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.PERCENT, 0.05),
                    unfilledTimeout=StrategyDuration(3, StrategyDurationUnit.MINUTES),
                ),
                instrumentSelection=DirectInstrumentSelection(),
            )
            planned = self.services.planOpenOrders(OpenOrderRequest(security, execution))
            if planned:
                orders.extend(planned)
                tradedSessions[stateKey] = True
                lastEntryDays[security.symbol] = dayNumber

        self.state["tradedSessions"] = tradedSessions
        self.state["lastEntryDays"] = lastEntryDays
        return orders or None
