from typing import Dict, List, Optional

from investfly.models import *


class CryptoRelativeStrengthRotationStrategy(TradingStrategy):
    """Weekly long-only crypto rotation with intraday drawdown monitoring."""

    SYMBOLS = ["BTC-USD", "ETH-USD", "XRP-USD", "DOGE-USD", "AAVE-USD"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -7.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(3.0, 50.0),
                    ProfitTargetTier(6.0, 50.0),
                ],
            ),
            maxHold=StrategyDuration(8, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            SecurityType.CRYPTO,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits),
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=3,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=45.0),
            ),
        )

    @scheduled(TriggerSchedule.weekly(Weekday.WED, "00:15"))
    def rebalanceWeekly(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        scores = self._rankedScores()
        portfolio = self.getPortfolio()
        if len(scores) < 2 or not self._btcRegimeHealthy():
            positions = [
                position
                for position in portfolio.openPositions or []
                if position.security.symbol in self.SYMBOLS
            ]
            self.state["targets"] = []
            orders = self.services.planCloseOrders(positions)
            return orders or None

        selected = [symbol for symbol, _ in scores[:2]]
        minimumScore = min(score for _, score in scores[:2])
        selectedScores = [max(score - minimumScore + 0.25, 0.01) ** 0.5 for _, score in scores[:2]]
        scoreTotal = sum(selectedScores)
        targetWeights = [
            TargetWeightSpec(symbol, selectedScores[index] / scoreTotal * 100.0)
            for index, symbol in enumerate(selected)
        ]
        selection = RankedSecuritySelection(
            universe=SecurityUniverseSelector.fromSymbols(SecurityType.CRYPTO, selected),
            scoreExpression=SecurityScoreExpression(formula=["1"]),
            selectTopN=len(selected),
        )
        orders = self.services.planRebalanceOrders(
            RebalancePlan(
                targetSecurities=selection,
                allocationBudget=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 85.0),
                targetWeightMode=TargetWeightMode.SCORE_WEIGHTED,
                targetWeights=targetWeights,
                rebalanceTolerance=0.04,
            )
        )
        self.state["targets"] = selected
        self.state["scores"] = {symbol: score for symbol, score in scores}
        return orders or None

    @scheduled(
        TriggerSchedule.hourly(
            days=ScheduleDayMode.EVERYDAY,
            window=ScheduleWindow("00:00", "23:59"),
        )
    )
    def hourlyRiskReview(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        return self._riskExitOrders()

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        peaks: Dict[str, float] = dict(self.state.get("peaks", {}))
        for security in updatedSecurities:
            if security.symbol not in self.SYMBOLS:
                continue
            try:
                bars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 2)
            except NoDataException:
                continue
            if not bars:
                continue
            price = float(bars[-1]["close"])
            peaks[security.symbol] = max(float(peaks.get(security.symbol, price)), price)
        self.state["peaks"] = peaks
        return None

    def _riskExitOrders(self) -> Optional[List[TradeOrder]]:
        peaks: Dict[str, float] = dict(self.state.get("peaks", {}))
        exits = []
        for position in self.getPortfolio().openPositions or []:
            if position.security.symbol not in self.SYMBOLS:
                continue
            try:
                bars = self.dataService.getBars(position.security, BarInterval.FIFTEEN_MINUTE, 2)
            except NoDataException:
                continue
            if not bars:
                continue
            price = float(bars[-1]["close"])
            peak = float(peaks.get(position.security.symbol, price))
            if peak > 0 and price / peak - 1.0 <= -0.10:
                exits.append(position)
        orders = self.services.planCloseOrders(exits)
        return orders or None

    def _rankedScores(self) -> List[tuple[str, float]]:
        scores: List[tuple[str, float]] = []
        for symbol in self.SYMBOLS:
            security = Security(symbol, SecurityType.CRYPTO)
            try:
                bars = self.dataService.getBars(security, BarInterval.ONE_DAY, 31)
            except NoDataException:
                continue
            if len(bars) < 31:
                continue
            closes = [float(bar["close"]) for bar in bars]
            returns = [
                closes[index] / closes[index - 1] - 1.0
                for index in range(1, len(closes))
                if closes[index - 1] > 0
            ]
            mean = sum(returns[-20:]) / 20.0
            variance = sum((value - mean) * (value - mean) for value in returns[-20:]) / 20.0
            volatility = max(variance ** 0.5, 0.001)
            momentum = closes[-1] / closes[-15] - 1.0
            recentMomentum = closes[-1] / closes[-6] - 1.0
            high = max(closes[-21:])
            drawdown = closes[-1] / high - 1.0
            score = momentum - abs(recentMomentum) * 2.0 - volatility * 2.0 + drawdown * 2.0
            if momentum > -0.02:
                scores.append((symbol, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores

    def _btcRegimeHealthy(self) -> bool:
        security = Security("BTC-USD", SecurityType.CRYPTO)
        try:
            bars = self.dataService.getBars(security, BarInterval.ONE_DAY, 31)
        except NoDataException:
            return False
        if len(bars) < 31:
            return False
        closes = [float(bar["close"]) for bar in bars]
        latestReturn = closes[-1] / closes[-2] - 1.0
        recentMomentum = closes[-1] / closes[-6] - 1.0
        return (
            closes[-1] > sum(closes[-30:]) / 30.0
            and latestReturn <= 0.0
            and recentMomentum > 0.0
            and recentMomentum < 0.02
        )
