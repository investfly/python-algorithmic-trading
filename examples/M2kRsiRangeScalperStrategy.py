from typing import Dict, List, Optional

from investfly.models import *


class M2kRsiRangeScalperStrategy(TradingStrategy):
    """Morning M2K RSI range scalper with expiring tick limits and a strict margin cap."""

    SECURITY = Security("M2K", SecurityType.FUTURE)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromFutureProduct(FutureProduct.M2K)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, -18.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.TICKS,
                [
                    ProfitTargetTier(10.0, 40.0),
                    ProfitTargetTier(20.0, 30.0),
                    ProfitTargetTier(36.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="m2k-scalp-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.TICKS_FROM_ENTRY, 0.0))
                    ),
                )
            ],
            maxHold=StrategyDuration(45, StrategyDurationUnit.MINUTES),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:45"),
        )
        return CustomStrategyPolicy(
            SecurityType.FUTURE,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            assetLifecycle=AssetLifecycleRules(
                future=FutureLifecycleRules(FutureRollMode.CLOSE_ONLY, daysBeforeExpiry=4)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=12.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=10.0, maxQuantity=1.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 9 * 60 + 40 or currentMinutes > 10 * 60 + 30:
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        tradedSessions: Dict[str, str] = dict(self.state.get("tradedSessions", {}))
        if sessionDay in tradedSessions or self.getPortfolio().openPositions:
            return None

        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_MINUTE, 22)
            fifteenMinuteBars = self.dataService.getBars(self.SECURITY, BarInterval.FIFTEEN_MINUTE, 13)
        except NoDataException:
            return None
        closes = [float(bar["close"]) for bar in bars]
        fifteenMinuteCloses = [float(bar["close"]) for bar in fifteenMinuteBars]
        rangeMove = abs(fifteenMinuteCloses[-1] / fifteenMinuteCloses[0] - 1.0)
        rangeMean = sum(fifteenMinuteCloses) / len(fifteenMinuteCloses)
        rangeDispersion = max(fifteenMinuteCloses) / min(fifteenMinuteCloses) - 1.0
        if rangeMove > 0.012 or rangeDispersion > 0.025:
            return None

        rsiSeries = self._rsiSeries(closes, 14)
        if len(rsiSeries) < 2:
            return None
        priorRsi = rsiSeries[-2]
        currentRsi = rsiSeries[-1]
        currentPrice = closes[-1]
        side = None
        if priorRsi <= 30.0 and priorRsi < currentRsi <= 30.0 and currentPrice < rangeMean:
            side = PositionType.LONG
        if side is None:
            return None

        marginGuard = GuardPolicy([
            PortfolioMarginGuard(minBuyingPowerPct=75.0, minMarginBufferPct=80.0)
        ])
        decisions = self.services.evaluateGuards(marginGuard, GuardScope.ENTRY_RULE, "m2k-rsi-range")
        if any(not decision.passed for decision in decisions):
            self.state["lastGuardDecisions"] = [decision.toDict() for decision in decisions]
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.LIMIT_ORDER,
                orderDuration=OrderDuration.DAY,
                limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.TICKS, 1.0),
                unfilledTimeout=StrategyDuration(3, StrategyDurationUnit.BARS, BarInterval.ONE_MINUTE),
            ),
            instrumentSelection=FutureContractSelection(FutureContractSelector(0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.SECURITY, execution, side=side))
        if orders:
            tradedSessions[sessionDay] = side.value
            self.state["tradedSessions"] = tradedSessions
            self.state["entryRsi"] = currentRsi
        return orders or None

    def _rsiSeries(self, closes: List[float], period: int) -> List[float]:
        values: List[float] = []
        for endIndex in range(period + 1, len(closes) + 1):
            window = closes[endIndex - period - 1:endIndex]
            gains = 0.0
            losses = 0.0
            for index in range(1, len(window)):
                change = window[index] - window[index - 1]
                if change > 0:
                    gains += change
                elif change < 0:
                    losses -= change
            if losses <= 0:
                values.append(100.0)
            else:
                values.append(100.0 - 100.0 / (1.0 + gains / losses))
        return values
