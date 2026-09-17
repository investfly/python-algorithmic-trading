from typing import Dict, List, Optional

from investfly.models import *


class AdaptiveBreadthMomentumStrategy(TradingStrategy):
    """Cross-sectional technology momentum gated by broad-market participation."""

    CANDIDATE_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, self.CANDIDATE_SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(
                    protectiveExits=buildProtectiveExitPlan(
                        targetProfitPct=12.0,
                        stopLossPct=6.0,
                        trailingStopPct=3.0,
                        maxHold=StrategyDuration(20, StrategyDurationUnit.DAYS),
                    )
                )
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=40.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        now = self.context.currentTime
        signalDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if self.state.get("lastSignalDay") == signalDay:
            return None

        try:
            leaders = self._selectLeaders()
        except NoDataException:
            return None
        if not leaders:
            return None
        self.state["lastSignalDay"] = signalDay

        portfolio = self.getPortfolio()
        previousLeaders = list(self.state.get("leaders", []))
        cooldowns: Dict[str, str] = dict(self.state.get("cooldowns", {}))
        currentDay = signalDay
        closes: List[OpenPosition] = []
        for position in portfolio.openPositions or []:
            if position.security.securityType != SecurityType.STOCK:
                continue
            if position.security.symbol not in leaders:
                closes.append(position)
                cooldowns[position.security.symbol] = currentDay

        orders = self.services.planCloseOrders(closes)
        for symbol in leaders:
            security = Security(symbol, SecurityType.STOCK)
            if portfolio.findPosition(security, PositionType.LONG) is not None:
                continue
            if cooldowns.get(symbol) == currentDay:
                continue
            execution = OpenExecutionSettings(
                positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 35.0),
                orderSpec=OrderSpec(),
                instrumentSelection=DirectInstrumentSelection(),
            )
            orders.extend(self.services.planOpenOrders(OpenOrderRequest(security, execution)))

        if leaders != previousLeaders:
            self.state["leaders"] = leaders
        self.state["cooldowns"] = cooldowns
        return orders or None

    def _selectLeaders(self) -> List[str]:
        spy = Security("SPY", SecurityType.STOCK)
        spyDaily = self.dataService.getBars(spy, BarInterval.ONE_DAY, 51)
        if len(spyDaily) < 51:
            return []
        spyClose = float(spyDaily[-1]["close"])
        spyAverage = sum(float(bar["close"]) for bar in spyDaily[-50:]) / 50.0

        scores: List[tuple[str, float]] = []
        advancing = 0
        observed = 0
        for symbol in self.CANDIDATE_SYMBOLS:
            security = Security(symbol, SecurityType.STOCK)
            try:
                dailyBars = self.dataService.getBars(security, BarInterval.ONE_DAY, 51)
                hourlyBars = self.dataService.getBars(security, BarInterval.SIXTY_MINUTE, 21)
            except NoDataException:
                continue
            if len(dailyBars) < 51 or len(hourlyBars) < 21:
                continue
            dailyClose = float(dailyBars[-1]["close"])
            dailyAverage = sum(float(bar["close"]) for bar in dailyBars[-50:]) / 50.0
            observed += 1
            if dailyClose > dailyAverage:
                advancing += 1

            startPrice = float(hourlyBars[0]["close"])
            endPrice = float(hourlyBars[-1]["close"])
            if startPrice <= 0:
                continue
            hourlyMomentum = endPrice / startPrice - 1.0
            dailyMomentum = dailyClose / float(dailyBars[-21]["close"]) - 1.0
            returns = [
                abs(float(hourlyBars[index]["close"]) / float(hourlyBars[index - 1]["close"]) - 1.0)
                for index in range(1, len(hourlyBars))
                if float(hourlyBars[index - 1]["close"]) > 0
            ]
            averageMove = sum(returns) / len(returns) if returns else 1.0
            scores.append((symbol, (dailyMomentum * 0.7 + hourlyMomentum * 0.3) / max(averageMove, 0.0001)))

        breadth = float(advancing) / float(observed) if observed else 0.0
        scores.sort(key=lambda item: item[1], reverse=True)
        leaderCount = 2 if spyClose > spyAverage and breadth >= 0.5 else 1
        return [item[0] for item in scores[:leaderCount] if item[1] > 0]
