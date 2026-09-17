from typing import Any, List, Optional, cast

from investfly.models import *


class FundamentalMomentumRotationStrategy(TradingStrategy):
    """Weekly SP-100 rotation using normalized quality, value, momentum, and risk factors."""

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromStandardList(StandardSymbolsList.SP_100)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=8,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0),
            ),
        )

    @scheduled(TriggerSchedule.weekly(Weekday.MON, "10:00"))
    def rebalance(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        if not self._marketRegimeIsHealthy():
            positions = [
                position
                for position in self.getPortfolio().openPositions or []
                if position.security.securityType == SecurityType.STOCK
            ]
            self.state["leaders"] = []
            orders = self.services.planCloseOrders(positions)
            return orders or None

        factorRows: List[tuple[str, float, float, float, float, float]] = []
        for security in self.getUniverseSecurities():
            try:
                financials = self.dataService.getFinancials(security.symbol)
                bars = self.dataService.getBars(security, BarInterval.ONE_DAY, 64)
            except NoDataException:
                continue
            if len(bars) < 64:
                continue

            pe = float(cast(Any, financials.get(FinancialField.PRICE_TO_EARNINGS_RATIO, 0.0)))
            roe = float(cast(Any, financials.get(FinancialField.RETURN_ON_EQUITY, 0.0)))
            margin = float(cast(Any, financials.get(FinancialField.PROFIT_MARGIN, 0.0)))
            startPrice = float(bars[0]["close"])
            endPrice = float(bars[-1]["close"])
            if pe <= 0 or pe > 100 or roe <= 0 or margin <= 0 or startPrice <= 0:
                continue

            momentum = endPrice / startPrice - 1.0
            returns = [
                float(bars[index]["close"]) / float(bars[index - 1]["close"]) - 1.0
                for index in range(1, len(bars))
                if float(bars[index - 1]["close"]) > 0
            ]
            if not returns:
                continue
            meanReturn = sum(returns) / len(returns)
            variance = sum((value - meanReturn) * (value - meanReturn) for value in returns) / len(returns)
            volatility = variance ** 0.5
            factorRows.append((security.symbol, pe, roe, margin, momentum, volatility))

        if len(factorRows) < 8:
            return None

        scoreRows: List[tuple[str, float]] = []
        for row in factorRows:
            valuation = 1.0 - self._normalize(row[1], factorRows, 1)
            profitability = (
                self._normalize(row[2], factorRows, 2)
                + self._normalize(row[3], factorRows, 3)
            ) / 2.0
            momentum = self._normalize(row[4], factorRows, 4)
            stability = 1.0 - self._normalize(row[5], factorRows, 5)
            score = valuation * 0.20 + profitability * 0.20 + momentum * 0.50 + stability * 0.10
            scoreRows.append((row[0], score))

        scoreRows.sort(key=lambda item: item[1], reverse=True)
        leaders = scoreRows[:8]
        positiveScores = [max(0.01, item[1]) for item in leaders]
        scoreTotal = sum(positiveScores)
        targetWeights = [
            TargetWeightSpec(leaders[index][0], positiveScores[index] / scoreTotal * 100.0)
            for index in range(len(leaders))
        ]
        symbols = [item[0] for item in leaders]
        selection = RankedSecuritySelection(
            universe=SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, symbols),
            scoreExpression=SecurityScoreExpression(formula=["1"]),
            selectTopN=len(symbols),
        )
        plan = RebalancePlan(
            targetSecurities=selection,
            allocationBudget=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 90.0),
            targetWeights=targetWeights,
            rebalanceTolerance=0.02,
        )
        self.state["leaders"] = symbols
        orders = self.services.planRebalanceOrders(plan)
        return orders or None

    def _marketRegimeIsHealthy(self) -> bool:
        try:
            bars = self.dataService.getBars(Security("SPY", SecurityType.STOCK), BarInterval.ONE_DAY, 101)
        except NoDataException:
            return False
        if len(bars) < 101:
            return False
        currentPrice = float(bars[-1]["close"])
        averagePrice = sum(float(bar["close"]) for bar in bars[-100:]) / 100.0
        return currentPrice > averagePrice

    def _normalize(self, value: float, rows: List[tuple[str, float, float, float, float, float]], index: int) -> float:
        values = [cast(float, row[index]) for row in rows]
        minimum = min(values)
        maximum = max(values)
        if maximum <= minimum:
            return 0.5
        return (value - minimum) / (maximum - minimum)
