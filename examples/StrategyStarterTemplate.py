from typing import List, Optional

from investfly.models import *


class StrategyStarterTemplate(TradingStrategy):
    """Intraday EMA tutorial with multi-timeframe confirmation and managed staged exits."""

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, ["AAPL", "MSFT"])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -4.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PERCENT_GAIN,
                [
                    ProfitTargetTier(4.0, 35.0),
                    ProfitTargetTier(8.0, 35.0),
                ],
            ),
            protectionAdjustments=[
                ProtectionAdjustment(
                    id="breakeven-after-target-1",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=1),
                    replacementStop=StopSpec.fixedStop(
                        FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, 0.0))
                    ),
                ),
                ProtectionAdjustment(
                    id="trail-after-target-2",
                    trigger=ProtectionTrigger(ProtectionTriggerType.TARGET_FILLED, targetNumber=2),
                    replacementStop=StopSpec.trailingStop(
                        TrailingStopRule(TrailingStopDistance(TrailingStopDistanceType.PERCENT, 2.0))
                    ),
                ),
            ],
            maxHold=StrategyDuration(10, StrategyDurationUnit.DAYS),
        )
        return CustomStrategyPolicy(
            assetType=SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=2,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=20.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        closes: List[OpenPosition] = []
        openRequests: List[OpenOrderRequest] = []
        portfolio = self.getPortfolio()
        for security in updatedSecurities:
            try:
                fast = self.dataService.computeIndicatorSeries(
                    IndicatorId.EMA, security, {IndicatorParam.EMA.PERIOD: 9, StandardParams.BARINTERVAL: BarInterval.FIFTEEN_MINUTE}
                )
                slow = self.dataService.computeIndicatorSeries(
                    IndicatorId.EMA, security, {IndicatorParam.EMA.PERIOD: 21, StandardParams.BARINTERVAL: BarInterval.FIFTEEN_MINUTE}
                )
                trendFast = self.dataService.computeIndicatorSeries(
                    IndicatorId.EMA, security, {IndicatorParam.EMA.PERIOD: 20, StandardParams.BARINTERVAL: BarInterval.SIXTY_MINUTE}
                )
                trendSlow = self.dataService.computeIndicatorSeries(
                    IndicatorId.EMA, security, {IndicatorParam.EMA.PERIOD: 50, StandardParams.BARINTERVAL: BarInterval.SIXTY_MINUTE}
                )
                atr = self.dataService.computeIndicatorSeries(
                    IndicatorId.ATR, security, {IndicatorParam.ATR.PERIOD: 14, StandardParams.BARINTERVAL: BarInterval.FIFTEEN_MINUTE}
                )
                position = portfolio.findPosition(security, PositionType.LONG)
                trendIsUp = trendFast.last.value > trendSlow.last.value
                if position is not None and (fast.cross_under(slow) or not trendIsUp):
                    closes.append(position)
                elif position is None and trendIsUp and fast.cross_over(slow):
                    bars = self.dataService.getBars(security, BarInterval.FIFTEEN_MINUTE, 1)
                    price = float(bars[-1]["close"])
                    volatilityPct = max(0.1, float(atr.last.value) / price * 100.0)
                    allocationPct = max(4.0, min(15.0, 9.0 / volatilityPct))
                    execution = OpenExecutionSettings(
                        positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, allocationPct),
                        orderSpec=OrderSpec(),
                        instrumentSelection=DirectInstrumentSelection(),
                    )
                    openRequests.append(OpenOrderRequest(security, execution))
            except NoDataException:
                continue

        orders = self.services.planCloseOrders(closes)
        for request in openRequests:
            orders.extend(self.services.planOpenOrders(request))
        return orders or None
