from typing import List, Optional

from investfly.models import *


class ForexRsiHedgedRangeStrategy(TradingStrategy):
    """Adaptive EUR/USD RSI range reversion with broker-aware long/short hedging."""

    SECURITY = Security("EUR-USD", SecurityType.FOREX)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.FOREX, [self.SECURITY.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            executionPolicy=ProtectiveExecutionPolicy.INVESTFLY_MANAGED_ONLY,
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, -50.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PIPS,
                [ProfitTargetTier(50.0, 100.0)],
            ),
            maxHold=StrategyDuration(120, StrategyDurationUnit.HOURS),
        )
        return CustomStrategyPolicy(
            SecurityType.FOREX,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=4,
                maxOpenPositionsPerSymbol=2,
                maxTotalMarginPct=80.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=30.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        sessionGuard = GuardPolicy([TimeGuard.between("13:00", "16:45")])
        if not all(decision.passed for decision in self.services.evaluateGuards(sessionGuard, GuardScope.ENTRY_RULE, self.SECURITY.symbol)):
            return None
        try:
            minuteBars = self.dataService.getBars(self.SECURITY, BarInterval.ONE_MINUTE, 80)
            rangeBars = self.dataService.getBars(self.SECURITY, BarInterval.FIFTEEN_MINUTE, 17)
        except NoDataException:
            return None
        if len(minuteBars) < 80 or len(rangeBars) < 17:
            return None

        rsiValues = self._rsiSeries(minuteBars, 7)
        if len(rsiValues) < 40:
            return None
        recentRsi = sorted(rsiValues[-40:])
        lowThreshold = min(5.0, recentRsi[3])
        highThreshold = max(92.0, recentRsi[-4])
        currentRsi = rsiValues[-1]
        priorRsi = rsiValues[-2]

        rangeHigh = max(float(bar["high"]) for bar in rangeBars)
        rangeLow = min(float(bar["low"]) for bar in rangeBars)
        midPrice = (rangeHigh + rangeLow) / 2.0
        rangePct = (rangeHigh - rangeLow) / max(midPrice, 0.0001)
        directionMove = abs(float(rangeBars[-1]["close"]) / float(rangeBars[0]["close"]) - 1.0)
        if rangePct > 0.025 or directionMove > 0.012:
            return None

        side: PositionType | None = None
        if currentRsi <= lowThreshold and priorRsi > lowThreshold:
            side = PositionType.LONG
        elif currentRsi >= highThreshold and priorRsi < highThreshold:
            side = PositionType.SHORT
        if side is None:
            return None

        positionQuantity = 20000.0
        oppositeSide = PositionType.SHORT if side == PositionType.LONG else PositionType.LONG
        oppositePosition = self.getPortfolio().findPosition(self.SECURITY, oppositeSide)
        if oppositePosition is not None:
            currentPrice = float(minuteBars[-1]["close"])
            rawMovePips = (currentPrice - float(oppositePosition.avgPrice)) / 0.0001
            positionMovePips = rawMovePips if oppositePosition.position == PositionType.LONG else -rawMovePips
            if positionMovePips > -12.0:
                return None
            positionQuantity = 10000.0

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, positionQuantity),
            orderSpec=OrderSpec(),
            instrumentSelection=DirectInstrumentSelection(),
        )
        existingPolicy = EntryExistingPositionPolicy(
            sameTarget=ExistingTargetPolicy(ExistingTargetAction.IGNORE_NEW_ENTRY),
            oppositeSide=EntryConflictPolicy(EntryConflictAction.OPEN_HEDGED_POSITION, allowHedging=True),
        )
        orders = self.services.planOpenOrders(
            OpenOrderRequest(self.SECURITY, execution, side=side, existingPositionPolicy=existingPolicy)
        )
        return orders or None

    def _rsiSeries(self, bars: List[Bar], period: int) -> List[float]:
        closes = [float(bar["close"]) for bar in bars]
        result: List[float] = []
        for end in range(period, len(closes)):
            gains = 0.0
            losses = 0.0
            for index in range(end - period + 1, end + 1):
                change = closes[index] - closes[index - 1]
                if change > 0:
                    gains += change
                else:
                    losses -= change
            if losses <= 0:
                result.append(100.0)
            else:
                relativeStrength = gains / losses
                result.append(100.0 - 100.0 / (1.0 + relativeStrength))
        return result
