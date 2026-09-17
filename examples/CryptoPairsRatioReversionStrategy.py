from typing import Dict, List, Optional

from investfly.models import *


class CryptoPairsRatioReversionStrategy(TradingStrategy):
    """Long-only BTC/ETH capital rotation around a persistent ratio regime."""

    BTC = Security("BTC-USD", SecurityType.CRYPTO)
    ETH = Security("ETH-USD", SecurityType.CRYPTO)
    SYMBOLS = [BTC.symbol, ETH.symbol]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -5.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(1.5, 50.0),
                    ProfitTargetTier(3.0, 50.0),
                ],
            ),
            maxHold=StrategyDuration(7, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            SecurityType.CRYPTO,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=45.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not any(security.symbol in self.SYMBOLS for security in updatedSecurities):
            return None
        try:
            btcBars = self.dataService.getBars(self.BTC, BarInterval.FIVE_MINUTE, 145)
            ethBars = self.dataService.getBars(self.ETH, BarInterval.FIVE_MINUTE, 145)
            btcHourly = self.dataService.getBars(self.BTC, BarInterval.SIXTY_MINUTE, 25)
            ethHourly = self.dataService.getBars(self.ETH, BarInterval.SIXTY_MINUTE, 25)
        except NoDataException:
            return None
        if len(btcBars) < 145 or len(ethBars) < 145 or len(btcHourly) < 25 or len(ethHourly) < 25:
            return None

        ratios = [
            float(btcBars[index]["close"]) / max(float(ethBars[index]["close"]), 0.0001)
            for index in range(-144, 0)
        ]
        reference = ratios[-121:-1]
        mean = sum(reference) / len(reference)
        variance = sum((value - mean) * (value - mean) for value in reference) / len(reference)
        standardDeviation = variance ** 0.5
        if standardDeviation <= 0:
            return None
        zScore = (ratios[-1] - mean) / standardDeviation
        activeTarget = self.state.get("activeTarget")
        armed = bool(self.state.get("armed", True))
        now = self.context.currentTime
        minuteNumber = (now.year * 372 + now.month * 31 + now.day) * 1440 + now.hour * 60 + now.minute

        positions = [
            position
            for position in self.getPortfolio().openPositions or []
            if position.security.symbol in self.SYMBOLS
        ]
        if activeTarget is not None and abs(zScore) <= 0.20:
            targetPosition = next(
                (position for position in positions if position.security.symbol == activeTarget),
                None,
            )
            targetPrice = float(ethBars[-1]["close"]) if activeTarget == self.ETH.symbol else float(btcBars[-1]["close"])
            absoluteGain = (
                targetPrice / float(targetPosition.avgPrice) - 1.0
                if targetPosition is not None and float(targetPosition.avgPrice) > 0
                else 0.0
            )
            if len(positions) == 0 or absoluteGain >= 0.005:
                self.state["activeTarget"] = None
                self.state["armed"] = False
                self.state["lastExitMinute"] = minuteNumber
                self.state["lastExitZ"] = zScore
                orders = self.services.planCloseOrders(positions)
                return orders or None
        if activeTarget is not None:
            return None
        if not armed:
            lastExitMinute = int(self.state.get("lastExitMinute", -100000))
            if abs(zScore) <= 0.50 and minuteNumber - lastExitMinute >= 24 * 60:
                self.state["armed"] = True
            return None

        btcHourlyCloses = [float(bar["close"]) for bar in btcHourly]
        ethHourlyCloses = [float(bar["close"]) for bar in ethHourly]
        btcHealthy = btcHourlyCloses[-1] > sum(btcHourlyCloses[-24:]) / 24.0
        ethHealthy = ethHourlyCloses[-1] > sum(ethHourlyCloses[-24:]) / 24.0
        btcStabilizing = float(btcBars[-1]["close"]) >= float(btcBars[-3]["close"])
        ethStabilizing = float(ethBars[-1]["close"]) >= float(ethBars[-3]["close"])

        target = None
        if zScore >= 2.2 and ethHealthy and ethStabilizing:
            target = self.ETH.symbol
        elif zScore <= -2.2 and btcHealthy and btcStabilizing:
            try:
                btcDaily = self.dataService.getBars(self.BTC, BarInterval.ONE_DAY, 21)
            except NoDataException:
                btcDaily = []
            if len(btcDaily) >= 21:
                dailyCloses = [float(bar["close"]) for bar in btcDaily]
                dailyRegime = dailyCloses[-1] > sum(dailyCloses[-20:]) / 20.0
                fiveDayMomentum = dailyCloses[-1] / dailyCloses[-6] - 1.0
                if dailyRegime and fiveDayMomentum < 0.03:
                    target = self.BTC.symbol
        if target is None or target == activeTarget:
            return None

        selection = RankedSecuritySelection(
            universe=SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, [target]),
            scoreExpression=SecurityScoreExpression(formula=["1"]),
            selectTopN=1,
        )
        plan = RebalancePlan(
            targetSecurities=selection,
            allocationBudget=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 40.0),
            targetWeights=[
                TargetWeightSpec(target, 100.0),
            ],
            rebalanceTolerance=0.03,
        )
        orders = self.services.planRebalanceOrders(plan)
        if orders:
            ratioState: Dict[str, object] = dict(self.state.get("ratioRegime", {}))
            ratioState["target"] = target
            ratioState["entryZ"] = zScore
            ratioState["mean"] = mean
            ratioState["standardDeviation"] = standardDeviation
            self.state["ratioRegime"] = ratioState
            self.state["activeTarget"] = target
            self.state["armed"] = True
        return orders or None
