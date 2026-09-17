from typing import Dict, List, Optional

from investfly.models import *


class VolatilityTargetSectorRotationStrategy(TradingStrategy):
    """Monthly sector ETF allocation using covariance-penalized inverse-volatility targets."""

    SYMBOLS = ["XLK", "XLF", "XLE", "XLV", "XLU", "XLI", "XLP", "XLY", "XLB"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=6,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=22.0),
            ),
        )

    @scheduled(TriggerSchedule.lastTradingDayOfMonth("15:30"))
    def rebalance(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        if not self._riskRegimeIsHealthy():
            positions = [
                position
                for position in self.getPortfolio().openPositions or []
                if position.security.symbol in self.SYMBOLS
            ]
            self.state["targets"] = []
            orders = self.services.planCloseOrders(positions)
            return orders or None

        returnSeries: Dict[str, List[float]] = {}
        momentum: Dict[str, float] = {}
        for symbol in self.SYMBOLS:
            security = Security(symbol, SecurityType.STOCK)
            try:
                bars = self.dataService.getBars(security, BarInterval.ONE_DAY, 127)
            except NoDataException:
                continue
            if len(bars) < 127:
                continue
            returns = [
                float(bars[index]["close"]) / float(bars[index - 1]["close"]) - 1.0
                for index in range(1, len(bars))
                if float(bars[index - 1]["close"]) > 0
            ]
            returnSeries[symbol] = returns[-63:]
            momentum[symbol] = float(bars[-1]["close"]) / float(bars[-64]["close"]) - 1.0

        selected = [symbol for symbol in self.SYMBOLS if symbol in returnSeries and momentum[symbol] > 0]
        selected.sort(key=lambda symbol: momentum[symbol], reverse=True)
        selected = selected[:6]
        if len(selected) < 3:
            return None

        rawWeights: Dict[str, float] = {}
        for symbol in selected:
            values = returnSeries[symbol]
            mean = sum(values) / len(values)
            variance = sum((value - mean) * (value - mean) for value in values) / len(values)
            volatility = max(variance ** 0.5, 0.0001)
            correlations = [
                max(0.0, self._correlation(values, returnSeries[other]))
                for other in selected
                if other != symbol
            ]
            averageCorrelation = sum(correlations) / len(correlations) if correlations else 0.0
            rawWeights[symbol] = 1.0 / (volatility * (1.0 + averageCorrelation))

        weightTotal = sum(rawWeights.values())
        targetWeights = [
            TargetWeightSpec(symbol, min(22.0, rawWeights[symbol] / weightTotal * 100.0))
            for symbol in selected
        ]
        selection = RankedSecuritySelection(
            universe=SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, selected),
            scoreExpression=SecurityScoreExpression(formula=["1"]),
            selectTopN=len(selected),
        )
        plan = RebalancePlan(
            targetSecurities=selection,
            allocationBudget=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 90.0),
            targetWeights=targetWeights,
            rebalanceTolerance=0.025,
        )
        self.state["targets"] = selected
        orders = self.services.planRebalanceOrders(plan)
        return orders or None

    def _riskRegimeIsHealthy(self) -> bool:
        try:
            bars = self.dataService.getBars(Security("SPY", SecurityType.STOCK), BarInterval.ONE_DAY, 151)
        except NoDataException:
            return False
        if len(bars) < 151:
            return False
        average = sum(float(bar["close"]) for bar in bars[-150:]) / 150.0
        return float(bars[-1]["close"]) > average

    def _correlation(self, left: List[float], right: List[float]) -> float:
        count = min(len(left), len(right))
        leftValues = left[-count:]
        rightValues = right[-count:]
        leftMean = sum(leftValues) / count
        rightMean = sum(rightValues) / count
        covariance = sum(
            (leftValues[index] - leftMean) * (rightValues[index] - rightMean)
            for index in range(count)
        )
        leftVariance = sum((value - leftMean) * (value - leftMean) for value in leftValues)
        rightVariance = sum((value - rightMean) * (value - rightMean) for value in rightValues)
        return covariance / max((leftVariance * rightVariance) ** 0.5, 0.0000001)
