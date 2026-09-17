from typing import List, Optional

from investfly.models import *


class UsdJpyMonthlyMomentumStrategy(TradingStrategy):
    """Monthly London-open USD/JPY momentum with multi-timeframe regime selection."""

    SECURITY = Security("USD-JPY", SecurityType.FOREX)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.FOREX, [self.SECURITY.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            executionPolicy=ProtectiveExecutionPolicy.INVESTFLY_MANAGED_ONLY,
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, -250.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PIPS,
                [ProfitTargetTier(500.0, 100.0)],
            ),
            maxHold=StrategyDuration(20, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            SecurityType.FOREX,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=35.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_DAY)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        # Scheduled entries own the strategy; the daily trigger supplies the multi-year clock.
        # The monthly callback adds hourly confirmation wherever that newer dataset is available.
        return None

    @scheduled(TriggerSchedule.firstTradingDayOfMonth("03:00"))
    def enterMonthlyMomentum(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        monthKey = f"{self.context.currentTime.year:04d}-{self.context.currentTime.month:02d}"
        if self.state.get("lastEntryMonth") == monthKey:
            return None
        if self.getPortfolio().openPositions:
            return None
        marginGuard = GuardPolicy([
            PortfolioMarginGuard(minBuyingPowerPct=65.0, minMarginBufferPct=65.0, maxLeverage=2.0)
        ])
        if not all(decision.passed for decision in self.services.evaluateGuards(marginGuard, GuardScope.SCHEDULED_JOB, self.SECURITY.symbol)):
            return None

        try:
            dailyBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_DAY, 35)
        except NoDataException:
            return None
        if len(dailyBars) < 21:
            return None
        hourlyBars: List[Bar] = []
        try:
            hourlyBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 25)
        except NoDataException:
            pass

        dailyMomentum = float(dailyBars[-1]["close"]) / float(dailyBars[-21]["close"]) - 1.0
        hourlyMomentum = (
            float(hourlyBars[-1]["close"]) / float(hourlyBars[-13]["close"]) - 1.0
            if len(hourlyBars) >= 13
            else 0.0
        )
        dailyVolatility = self._returnVolatility(dailyBars[-21:])
        if dailyVolatility > 0.025:
            return None

        side = PositionType.LONG
        if dailyMomentum < -0.04 and hourlyMomentum < -0.005:
            side = PositionType.SHORT
        elif dailyMomentum < -0.04 or hourlyMomentum < -0.012:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 20.0),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.MARKET_ORDER,
                orderDuration=OrderDuration.DAY,
            ),
            instrumentSelection=DirectInstrumentSelection(),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.SECURITY, execution, side=side))
        if orders:
            self.state["lastEntryMonth"] = monthKey
            self.state["lastMomentumSide"] = side.value
        return orders or None

    def _returnVolatility(self, bars: List[Bar]) -> float:
        returns = [
            float(bars[index]["close"]) / float(bars[index - 1]["close"]) - 1.0
            for index in range(1, len(bars))
        ]
        meanReturn = sum(returns) / len(returns)
        variance = sum((value - meanReturn) ** 2 for value in returns) / len(returns)
        return variance ** 0.5
