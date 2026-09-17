from typing import Dict, List, Optional

from investfly.models import *


class ForexCrossSectionMomentumStrategy(TradingStrategy):
    """Weekly volatility-adjusted currency momentum across liquid USD pairs."""

    SYMBOLS = ["EUR-USD", "USD-JPY", "AUD-USD", "USD-CHF"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.FOREX, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            executionPolicy=ProtectiveExecutionPolicy.INVESTFLY_MANAGED_ONLY,
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, -250.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PIPS,
                [ProfitTargetTier(400.0, 100.0)],
            ),
            maxHold=StrategyDuration(28, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            SecurityType.FOREX,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=4,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=35.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=14.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_DAY)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        return None

    @scheduled(TriggerSchedule.weekly(Weekday.MON, "03:00"))
    def rotateCurrencyLeaders(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        marginGuard = GuardPolicy([
            PortfolioMarginGuard(minBuyingPowerPct=55.0, minMarginBufferPct=55.0, maxLeverage=2.0)
        ])
        if not all(decision.passed for decision in self.services.evaluateGuards(marginGuard, GuardScope.SCHEDULED_JOB)):
            return None

        scores: List[tuple[Security, float]] = []
        for symbol in self.SYMBOLS:
            security = Security(symbol, SecurityType.FOREX)
            try:
                dailyBars = self.dataService.getBars(security, BarInterval.ONE_DAY, 70)
            except NoDataException:
                continue
            if len(dailyBars) < 61:
                continue
            closes = [float(bar["close"]) for bar in dailyBars]
            momentum = closes[-1] / closes[-61] - 1.0
            returns = [closes[index] / closes[index - 1] - 1.0 for index in range(len(closes) - 20, len(closes))]
            volatility = self._standardDeviation(returns)
            score = momentum / max(volatility, 0.0005)
            try:
                hourlyBars = self.dataService.getBars(security, BarInterval.SIXTY_MINUTE, 25)
                if len(hourlyBars) >= 25:
                    hourlyMove = float(hourlyBars[-1]["close"]) / float(hourlyBars[-25]["close"]) - 1.0
                    score += hourlyMove / max(volatility, 0.0005) * 0.25
            except NoDataException:
                pass
            scores.append((security, score))

        if len(scores) < 4:
            return None
        scores.sort(key=lambda item: item[1], reverse=True)
        longSecurity = scores[0][0]
        shortSecurity = scores[-1][0]
        existingPositions = self.getPortfolio().openPositions or []
        existingLong = next((position for position in existingPositions if position.position == PositionType.LONG), None)
        existingShort = next((position for position in existingPositions if position.position == PositionType.SHORT), None)
        topSymbols = [scores[0][0].symbol, scores[1][0].symbol]
        bottomSymbols = [scores[-2][0].symbol, scores[-1][0].symbol]
        if existingLong is not None and existingLong.security.symbol in topSymbols:
            longSecurity = existingLong.security
        if existingShort is not None and existingShort.security.symbol in bottomSymbols:
            shortSecurity = existingShort.security
        targetSides: Dict[str, PositionType] = {
            longSecurity.symbol: PositionType.LONG,
            shortSecurity.symbol: PositionType.SHORT,
        }

        droppedPositions = [
            position
            for position in existingPositions
            if position.security.symbol not in targetSides
        ]
        orders = self.services.planCloseOrders(droppedPositions)
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 14.0),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.MARKET_ORDER,
                orderDuration=OrderDuration.DAY,
            ),
            instrumentSelection=DirectInstrumentSelection(),
        )
        existingPolicy = EntryExistingPositionPolicy(
            sameTarget=ExistingTargetPolicy(ExistingTargetAction.IGNORE_NEW_ENTRY),
            oppositeSide=EntryConflictPolicy(EntryConflictAction.CLOSE_OPPOSITE_THEN_OPEN),
        )
        targets: List[tuple[Security, PositionType]] = [
            (longSecurity, PositionType.LONG),
            (shortSecurity, PositionType.SHORT),
        ]
        for security, side in targets:
            orders.extend(
                self.services.planOpenOrders(
                    OpenOrderRequest(security, execution, side=side, existingPositionPolicy=existingPolicy)
                )
            )
        if orders:
            self.state["lastLongSymbol"] = longSecurity.symbol
            self.state["lastShortSymbol"] = shortSecurity.symbol
        return orders or None

    def _standardDeviation(self, values: List[float]) -> float:
        meanValue = sum(values) / len(values)
        variance = sum((value - meanValue) ** 2 for value in values) / len(values)
        return variance ** 0.5
