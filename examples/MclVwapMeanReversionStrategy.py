from typing import Dict, List, Optional

from investfly.models import *


class MclVwapMeanReversionStrategy(TradingStrategy):
    """Session-VWAP MCL reversion with bounded volatility and notice-window risk reduction."""

    SECURITY = Security("MCL", SecurityType.FUTURE)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromFutureProduct(FutureProduct.MCL)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -45.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.TICKS,
                [
                    ProfitTargetTier(24.0, 50.0),
                    ProfitTargetTier(48.0, 50.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="mcl-vwap-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, 0.0))
                    ),
                )
            ],
            maxHold=StrategyDuration(90, StrategyDurationUnit.MINUTES),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="14:20"),
        )
        return CustomStrategyPolicy(
            SecurityType.FUTURE,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            assetLifecycle=AssetLifecycleRules(
                future=FutureLifecycleRules(FutureRollMode.CLOSE_ONLY, daysBeforeExpiry=7)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=20.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=15.0, maxQuantity=1.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 13 * 60 + 20 or currentMinutes > 14 * 60:
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        tradedSessions: Dict[str, str] = dict(self.state.get("tradedSessions", {}))
        if sessionDay in tradedSessions or self.getPortfolio().openPositions:
            return None

        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.FIVE_MINUTE, 18)
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 3)
        except NoDataException:
            return None
        sessionBars = [
            bar
            for bar in bars
            if bar["date"].year == now.year and bar["date"].month == now.month and bar["date"].day == now.day
        ]
        if len(sessionBars) < 18 or len(hourlyBars) < 3:
            return None

        hourlyMove = abs(float(hourlyBars[-1]["close"]) / float(hourlyBars[0]["close"]) - 1.0)
        trueRanges = [
            max(
                float(bar["high"]) - float(bar["low"]),
                abs(float(bar["high"]) - float(bars[index - 1]["close"])),
                abs(float(bar["low"]) - float(bars[index - 1]["close"])),
            )
            for index, bar in enumerate(bars[-15:], start=len(bars) - 15)
            if index > 0
        ]
        averageTrueRange = sum(trueRanges[-14:]) / 14.0
        price = float(sessionBars[-1]["close"])
        if hourlyMove > 0.025 or averageTrueRange / max(price, 0.01) < 0.0007 or averageTrueRange / price > 0.012:
            return None

        vwapSeries = self._vwapSeries(sessionBars)
        deviations = [
            float(sessionBars[index]["close"]) / max(vwapSeries[index], 0.01) - 1.0
            for index in range(len(sessionBars))
        ]
        recent = deviations[-18:]
        mean = sum(recent) / len(recent)
        variance = sum((value - mean) * (value - mean) for value in recent) / len(recent)
        standardDeviation = variance ** 0.5
        if standardDeviation <= 0:
            return None
        zScore = (deviations[-1] - mean) / standardDeviation
        priorZScore = (deviations[-2] - mean) / standardDeviation
        priorClose = float(sessionBars[-2]["close"])
        side = None
        if priorZScore <= -0.9 and zScore > priorZScore and price > priorClose:
            side = PositionType.LONG
        elif priorZScore >= 0.9 and zScore < priorZScore and price < priorClose:
            side = PositionType.SHORT
        if side is None:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(),
            instrumentSelection=FutureContractSelection(FutureContractSelector(0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.SECURITY, execution, side=side))
        if orders:
            tradedSessions[sessionDay] = side.value
            self.state["tradedSessions"] = tradedSessions
            self.state["entryAtr"] = averageTrueRange
            self.state["entryZScore"] = zScore
        return orders or None

    @scheduled(TriggerSchedule.daily("13:30"))
    def reduceFirstNoticeRisk(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        positions = list(self.getPortfolio().openPositions or [])
        if not positions:
            return None
        try:
            contracts = self.dataService.listFutures(FutureProduct.MCL)
        except Exception:
            return None
        if not contracts:
            return None
        daysToExpiry = (contracts[0].expiryDate - self.context.currentTime.date()).days
        if daysToExpiry > 10:
            return None
        orders = self.services.planCloseOrders(positions, ClosePositionSpec(closePercent=50.0))
        if orders:
            self.state["lastNoticeRiskReduction"] = str(self.context.currentTime.date())
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
