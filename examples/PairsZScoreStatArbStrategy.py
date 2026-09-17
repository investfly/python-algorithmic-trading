from typing import List, Optional

from investfly.models import *


class PairsZScoreStatArbStrategy(TradingStrategy):
    """Dollar-balanced AAPL/MSFT spread reversion with an explicit two-leg lifecycle."""

    LEFT = Security("AAPL", SecurityType.STOCK)
    RIGHT = Security("MSFT", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, [self.LEFT.symbol, self.RIGHT.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(
                    protectiveExits=ProtectiveExitPlan(
                        initialProtection=StopSpec.fixedStop(
                            FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -3.0))
                        ),
                        maxHold=StrategyDuration(3, StrategyDurationUnit.DAYS),
                    )
                )
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=15.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        try:
            leftBars = self.dataService.getBars(self.LEFT, BarInterval.FIVE_MINUTE, 61)
            rightBars = self.dataService.getBars(self.RIGHT, BarInterval.FIVE_MINUTE, 61)
            leftHourly = self.dataService.getBars(self.LEFT, BarInterval.SIXTY_MINUTE, 31)
            rightHourly = self.dataService.getBars(self.RIGHT, BarInterval.SIXTY_MINUTE, 31)
        except NoDataException:
            return None
        if len(leftBars) < 61 or len(rightBars) < 61 or len(leftHourly) < 31 or len(rightHourly) < 31:
            return None

        hourlyCorrelation = self._returnCorrelation(leftHourly, rightHourly)
        zScore = self._spreadZScore(leftBars, rightBars)
        portfolio = self.getPortfolio()
        leftPosition = self._positionFor(self.LEFT)
        rightPosition = self._positionFor(self.RIGHT)
        pairState = str(self.state.get("pairState", "FLAT"))

        if pairState != "FLAT":
            if leftPosition is None or rightPosition is None:
                incompleteTicks = int(self.state.get("incompleteTicks", 0)) + 1
                self.state["incompleteTicks"] = incompleteTicks
                if incompleteTicks >= 4:
                    positions = [position for position in [leftPosition, rightPosition] if position is not None]
                    self.state["pairState"] = "FLAT"
                    self.state["incompleteTicks"] = 0
                    orders = self.services.planCloseOrders(positions)
                    return orders or None
                return None

            self.state["incompleteTicks"] = 0
            meanReverted = (pairState == "SPREAD_HIGH" and zScore <= 0.35) or (
                pairState == "SPREAD_LOW" and zScore >= -0.35
            )
            modelInvalidated = abs(hourlyCorrelation) < 0.35 or (
                pairState == "SPREAD_HIGH" and zScore >= 3.2
            ) or (
                pairState == "SPREAD_LOW" and zScore <= -3.2
            )
            if meanReverted or modelInvalidated:
                self.state["pairState"] = "FLAT"
                orders = self.services.planCloseOrders([leftPosition, rightPosition])
                return orders or None
            return None

        if leftPosition is not None or rightPosition is not None:
            positions = [position for position in [leftPosition, rightPosition] if position is not None]
            orders = self.services.planCloseOrders(positions)
            return orders or None
        if abs(hourlyCorrelation) < 0.55 or abs(zScore) < 1.8:
            return None

        orderSpec = OrderSpec(
            orderType=StrategyOrderType.LIMIT_ORDER,
            orderDuration=OrderDuration.DAY,
            limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.PERCENT, 0.04),
            unfilledTimeout=StrategyDuration(10, StrategyDurationUnit.MINUTES),
        )
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 10.0),
            orderSpec=orderSpec,
            instrumentSelection=DirectInstrumentSelection(),
        )
        if zScore > 0:
            leftSide = PositionType.SHORT
            rightSide = PositionType.LONG
            pairState = "SPREAD_HIGH"
        else:
            leftSide = PositionType.LONG
            rightSide = PositionType.SHORT
            pairState = "SPREAD_LOW"

        leftOrders = self.services.planOpenOrders(OpenOrderRequest(self.LEFT, execution, side=leftSide))
        rightOrders = self.services.planOpenOrders(OpenOrderRequest(self.RIGHT, execution, side=rightSide))
        if not leftOrders or not rightOrders:
            return None
        self.state["pairState"] = pairState
        self.state["incompleteTicks"] = 0
        return leftOrders + rightOrders

    def _positionFor(self, security: Security) -> OpenPosition | None:
        portfolio = self.getPortfolio()
        return portfolio.findPosition(security, PositionType.LONG) or portfolio.findPosition(security, PositionType.SHORT)

    def _spreadZScore(self, leftBars: List[Bar], rightBars: List[Bar]) -> float:
        left = [float(bar["close"]) for bar in leftBars]
        right = [float(bar["close"]) for bar in rightBars]
        leftMean = sum(left[:-1]) / (len(left) - 1)
        rightMean = sum(right[:-1]) / (len(right) - 1)
        covariance = sum((right[index] - rightMean) * (left[index] - leftMean) for index in range(len(left) - 1))
        rightVariance = sum((value - rightMean) * (value - rightMean) for value in right[:-1])
        hedgeRatio = covariance / max(rightVariance, 0.0001)
        intercept = leftMean - hedgeRatio * rightMean
        historicalSpreads = [
            left[index] - (intercept + hedgeRatio * right[index])
            for index in range(len(left) - 1)
        ]
        spreadMean = sum(historicalSpreads) / len(historicalSpreads)
        variance = sum((value - spreadMean) * (value - spreadMean) for value in historicalSpreads) / len(historicalSpreads)
        currentSpread = left[-1] - (intercept + hedgeRatio * right[-1])
        return (currentSpread - spreadMean) / max(variance ** 0.5, 0.0001)

    def _returnCorrelation(self, leftBars: List[Bar], rightBars: List[Bar]) -> float:
        leftReturns = [
            float(leftBars[index]["close"]) / float(leftBars[index - 1]["close"]) - 1.0
            for index in range(1, len(leftBars))
        ]
        rightReturns = [
            float(rightBars[index]["close"]) / float(rightBars[index - 1]["close"]) - 1.0
            for index in range(1, len(rightBars))
        ]
        leftMean = sum(leftReturns) / len(leftReturns)
        rightMean = sum(rightReturns) / len(rightReturns)
        covariance = sum(
            (leftReturns[index] - leftMean) * (rightReturns[index] - rightMean)
            for index in range(len(leftReturns))
        )
        leftVariance = sum((value - leftMean) * (value - leftMean) for value in leftReturns)
        rightVariance = sum((value - rightMean) * (value - rightMean) for value in rightReturns)
        return covariance / max((leftVariance * rightVariance) ** 0.5, 0.0000001)
