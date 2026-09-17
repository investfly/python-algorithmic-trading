from typing import Dict, List, Optional

from investfly.models import *


class VwapMeanReversionStrategy(TradingStrategy):
    """Intraday VWAP reversion with adaptive z-scores and hard-capped adverse scaling."""

    SYMBOLS = ["AAPL", "NVDA"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -2.2))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(1.0, 50.0),
                    ProfitTargetTier(2.0, 50.0),
                ],
            ),
            maxHold=StrategyDuration(180, StrategyDurationUnit.MINUTES),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:45"),
        )
        adverseScaling = ScalingPlan(
            addToLoser=LoserScaleRule(
                adverseMove=MoveCondition(MoveUnit.PERCENT, threshold=0.6),
                addPlan=ScaleAddPlan(
                    maxSteps=2,
                    minIntervalBetweenAdds=StrategyDuration(15, StrategyDurationUnit.MINUTES),
                    firstStepPctOfBase=40.0,
                    stepSizeMultiplier=0.5,
                ),
                limits=LoserScaleLimits(maxLossPctBeforeAdd=1.5, maxTotalAddNotional=10000.0),
            )
        )
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
                positionScaling=adverseScaling,
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=3,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if currentMinutes < 10 * 60 or currentMinutes > 14 * 60 + 30:
            return None

        portfolio = self.getPortfolio()
        reductions: Dict[str, str] = dict(self.state.get("reductions", {}))
        tradedSessions: Dict[str, str] = dict(self.state.get("tradedSessions", {}))
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        orders: List[TradeOrder] = []
        for security in updatedSecurities:
            if security.symbol not in self.SYMBOLS:
                continue
            try:
                allMinuteBars = self.dataService.getBars(security, BarInterval.ONE_MINUTE, 120)
                fifteenMinuteBars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 9)
            except NoDataException:
                continue

            sessionBars = [
                bar
                for bar in allMinuteBars
                if bar["date"].year == now.year and bar["date"].month == now.month and bar["date"].day == now.day
            ]
            if len(sessionBars) < 30 or len(fifteenMinuteBars) < 9:
                continue
            vwapSeries = self._vwapSeries(sessionBars)
            deviations = [
                float(sessionBars[index]["close"]) / max(vwapSeries[index], 0.0001) - 1.0
                for index in range(len(sessionBars))
            ]
            recentDeviations = deviations[-30:]
            meanDeviation = sum(recentDeviations) / len(recentDeviations)
            variance = sum((value - meanDeviation) * (value - meanDeviation) for value in recentDeviations) / len(recentDeviations)
            standardDeviation = variance ** 0.5
            if standardDeviation <= 0:
                continue
            zScore = (deviations[-1] - meanDeviation) / standardDeviation
            price = float(sessionBars[-1]["close"])
            vwap = vwapSeries[-1]

            longPosition = portfolio.findPosition(security, PositionType.LONG)
            shortPosition = portfolio.findPosition(security, PositionType.SHORT)
            reductionKey = f"{sessionDay}:{security.symbol}"
            position = longPosition or shortPosition
            if position is not None:
                alreadyReduced = reductions.get(reductionKey) == position.position.value
                entryPrice = float(position.avgPrice)
                favorableMove = price / entryPrice - 1.0 if position.position == PositionType.LONG else entryPrice / price - 1.0
                reachedVwap = (position.position == PositionType.LONG and price >= vwap) or (
                    position.position == PositionType.SHORT and price <= vwap
                )
                crossedBeyondVwap = (position.position == PositionType.LONG and zScore >= 0.5) or (
                    position.position == PositionType.SHORT and zScore <= -0.5
                )
                if crossedBeyondVwap and favorableMove >= 0.0015:
                    orders.extend(self.services.planCloseOrders([position]))
                    reductions.pop(reductionKey, None)
                elif reachedVwap and favorableMove >= 0.001 and not alreadyReduced:
                    orders.extend(self.services.planCloseOrders([position], ClosePositionSpec(closePercent=50.0)))
                    reductions[reductionKey] = position.position.value
                continue

            reductions.pop(reductionKey, None)
            if reductionKey in tradedSessions:
                continue
            trendMove = abs(float(fifteenMinuteBars[-1]["close"]) / float(fifteenMinuteBars[0]["close"]) - 1.0)
            if trendMove > 0.018:
                continue
            side: PositionType | None = None
            if zScore <= -1.35:
                side = PositionType.LONG
            elif zScore >= 1.35:
                side = PositionType.SHORT
            if side is None:
                continue

            execution = OpenExecutionSettings(
                positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 10.0),
                orderSpec=OrderSpec(),
                instrumentSelection=DirectInstrumentSelection(),
            )
            planned = self.services.planOpenOrders(OpenOrderRequest(security, execution, side=side))
            if planned:
                orders.extend(planned)
                tradedSessions[reductionKey] = side.value

        self.state["reductions"] = reductions
        self.state["tradedSessions"] = tradedSessions
        return orders or None

    def _vwapSeries(self, bars: List[Bar]) -> List[float]:
        cumulativeValue = 0.0
        cumulativeVolume = 0.0
        result: List[float] = []
        for bar in bars:
            typicalPrice = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
            volume = max(float(bar["volume"]), 1.0)
            cumulativeValue += typicalPrice * volume
            cumulativeVolume += volume
            result.append(cumulativeValue / cumulativeVolume)
        return result
