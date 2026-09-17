from typing import Dict, List, Optional

from investfly.models import *


class WeekendLiquidityMeanReversionStrategy(TradingStrategy):
    """Weekend BTC/ETH liquidity-dislocation fade with capped safety adds."""

    SYMBOLS = ["BTC-USD", "ETH-USD"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -2.5))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(0.8, 50.0),
                    ProfitTargetTier(1.6, 50.0),
                ],
            ),
            maxHold=StrategyDuration(8, StrategyDurationUnit.HOURS),
        )
        scaling = ScalingPlan(
            addToLoser=LoserScaleRule(
                adverseMove=MoveCondition(MoveUnit.PERCENT, threshold=0.4),
                addPlan=ScaleAddPlan(
                    maxSteps=2,
                    minIntervalBetweenAdds=StrategyDuration(20, StrategyDurationUnit.MINUTES),
                    firstStepPctOfBase=30.0,
                    stepSizeMultiplier=0.5,
                ),
                limits=LoserScaleLimits(
                    maxLossPctBeforeAdd=1.2,
                    maxTotalAddNotional=10000.0,
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
                maxPositionExposure=PositionExposureLimit(maxPositionPct=30.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        now = self.context.currentTime
        if now.weekday() not in (5, 6):
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        tradedSessions: Dict[str, bool] = dict(self.state.get("tradedSessions", {}))
        orders: List[TradeOrder] = []
        portfolio = self.getPortfolio()
        for security in updatedSecurities:
            if security.symbol not in self.SYMBOLS:
                continue
            stateKey = f"{sessionDay}:{security.symbol}"
            if tradedSessions.get(stateKey) or portfolio.findPosition(security, PositionType.LONG) is not None:
                continue
            try:
                minuteBars = self.dataService.getBars(security, BarInterval.ONE_MINUTE, 91)
                fifteenMinuteBars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 33)
            except NoDataException:
                continue
            if len(minuteBars) < 91 or len(fifteenMinuteBars) < 33:
                continue

            closes = [float(bar["close"]) for bar in minuteBars]
            reference = closes[-61:-1]
            mean = sum(reference) / len(reference)
            variance = sum((value - mean) * (value - mean) for value in reference) / len(reference)
            standardDeviation = variance ** 0.5
            if standardDeviation <= 0:
                continue
            zScore = (closes[-2] - mean) / standardDeviation
            baselineVolume = sum(float(bar["volume"]) for bar in minuteBars[-61:-1]) / 60.0
            recentVolume = sum(float(bar["volume"]) for bar in minuteBars[-5:]) / 5.0
            fifteenCloses = [float(bar["close"]) for bar in fifteenMinuteBars]
            fifteenMean = sum(fifteenCloses[-32:]) / 32.0
            zThreshold = -3.0 if security.symbol == "BTC-USD" else -2.5
            dislocation = zScore <= zThreshold and closes[-2] / closes[-17] - 1.0 <= -0.004
            reversal = closes[-1] > closes[-2] and float(minuteBars[-1]["low"]) >= float(minuteBars[-2]["low"])
            thinButTradable = recentVolume <= baselineVolume * 3.0 and recentVolume >= baselineVolume * 0.20
            regimeBounded = closes[-1] >= fifteenMean * 0.975
            if not dislocation or not reversal or not thinButTradable or not regimeBounded:
                continue

            execution = OpenExecutionSettings(
                positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 20.0),
                orderSpec=OrderSpec(
                    orderType=StrategyOrderType.LIMIT_ORDER,
                    orderDuration=OrderDuration.GTC,
                    limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.PERCENT, 0.04),
                    unfilledTimeout=StrategyDuration(2, StrategyDurationUnit.MINUTES),
                ),
                instrumentSelection=DirectInstrumentSelection(),
            )
            planned = self.services.planOpenOrders(OpenOrderRequest(security, execution))
            if planned:
                orders.extend(planned)
                tradedSessions[stateKey] = True
                self.state["lastDislocation"] = {
                    "symbol": security.symbol,
                    "zScore": zScore,
                    "baselineVolume": baselineVolume,
                }

        self.state["tradedSessions"] = tradedSessions
        return orders or None
