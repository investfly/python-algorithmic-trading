from typing import Any, Dict, List, Optional

from investfly.models import *


class OpeningRangeBreakoutScaleStrategy(TradingStrategy):
    """Intraday opening-range breakout with persistent session state and managed pyramiding."""

    SYMBOLS = ["AAPL", "MSFT", "NVDA"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -1.5))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(1.5, 40.0),
                    ProfitTargetTier(3.0, 30.0),
                    ProfitTargetTier(5.0, 30.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="orb-breakeven",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="orb-trailing",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=2),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.PERCENT, 0.75))
                    ),
                ),
            ],
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="15:50"),
        )
        scaling = ScalingPlan(
            addToWinner=WinnerScaleRule(
                MoveCondition(MoveUnit.PERCENT, threshold=0.5),
                ScaleAddPlan(
                    maxSteps=2,
                    minIntervalBetweenAdds=StrategyDuration(10, StrategyDurationUnit.MINUTES),
                    firstStepPctOfBase=50.0,
                    stepSizeMultiplier=0.5,
                ),
            )
        )
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
                positionScaling=scaling,
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
        openMinutes = 9 * 60 + 30
        rangeEndMinutes = 9 * 60 + 44
        entryEndMinutes = 12 * 60
        if currentMinutes < openMinutes or currentMinutes > entryEndMinutes:
            return None

        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        ranges: Dict[str, Dict[str, Any]] = dict(self.state.get("ranges", {}))
        orders: List[TradeOrder] = []
        portfolio = self.getPortfolio()
        for security in updatedSecurities:
            if security.symbol not in self.SYMBOLS:
                continue
            stateKey = f"{sessionDay}:{security.symbol}"
            rangeState: Dict[str, Any] = dict(ranges.get(stateKey, {}))
            try:
                minuteBars = self.dataService.getBars(security, BarInterval.ONE_MINUTE, 1)
            except NoDataException:
                continue
            if not minuteBars:
                continue
            currentBar = minuteBars[-1]

            if currentMinutes <= rangeEndMinutes:
                currentHigh = float(currentBar["high"])
                currentLow = float(currentBar["low"])
                previousHigh = float(rangeState.get("high", currentHigh))
                previousLow = float(rangeState.get("low", currentLow))
                rangeState["high"] = max(previousHigh, currentHigh)
                rangeState["low"] = min(previousLow, currentLow)
                rangeState["volume"] = float(rangeState.get("volume", 0.0)) + float(currentBar["volume"])
                ranges[stateKey] = rangeState
                continue

            if not rangeState or bool(rangeState.get("traded", False)):
                continue
            if portfolio.findPosition(security, PositionType.LONG) is not None:
                continue
            try:
                fiveMinuteBars = self.dataService.getBars(security, BarInterval.FIVE_MINUTE, 21)
                fifteenMinuteBars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 11)
            except NoDataException:
                continue
            if len(fiveMinuteBars) < 21 or len(fifteenMinuteBars) < 11:
                continue

            price = float(currentBar["close"])
            openingHigh = float(rangeState["high"])
            averageVolume = sum(float(bar["volume"]) for bar in fiveMinuteBars[:-1]) / 20.0
            relativeVolume = float(fiveMinuteBars[-1]["volume"]) / max(averageVolume, 1.0)
            fiveMinuteAverage = sum(float(bar["close"]) for bar in fiveMinuteBars[-9:]) / 9.0
            fifteenMinuteAverage = sum(float(bar["close"]) for bar in fifteenMinuteBars[-10:]) / 10.0
            trendConfirmed = price > fiveMinuteAverage and price > fifteenMinuteAverage
            if price <= openingHigh * 1.0005 or relativeVolume < 0.65 or not trendConfirmed:
                continue

            execution = OpenExecutionSettings(
                positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 12.0),
                orderSpec=OrderSpec(
                    orderType=StrategyOrderType.LIMIT_ORDER,
                    orderDuration=OrderDuration.DAY,
                    limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.PERCENT, 0.03),
                    unfilledTimeout=StrategyDuration(5, StrategyDurationUnit.MINUTES),
                ),
                instrumentSelection=DirectInstrumentSelection(),
            )
            planned = self.services.planOpenOrders(OpenOrderRequest(security, execution))
            if planned:
                orders.extend(planned)
                rangeState["traded"] = True
                ranges[stateKey] = rangeState

        self.state["ranges"] = ranges
        return orders or None
