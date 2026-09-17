from typing import List, Optional

from investfly.models import *


class DirectionalLongOptionRegimeStrategy(TradingStrategy):
    """Selects a long call or put from a multi-timeframe directional score."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 10.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 12.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="directional-dte-close",
                    trigger=OptionDteTrigger(atOrBelow=5),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE),
                    ],
                )
            ]
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules(groupRisk=groupRisk)),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=4.0, maxGroupNotional=8000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        currentMinutes = now.hour * 60 + now.minute
        if now.weekday() >= 5 or currentMinutes < 10 * 60 or currentMinutes > 14 * 60:
            return None
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            fiveMinuteBars = self.dataService.getBars(self.UNDERLYING, BarInterval.FIVE_MINUTE, 18)
            hourlyBars = self.dataService.getBars(self.UNDERLYING, BarInterval.SIXTY_MINUTE, 10)
            dailyBars = self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 8)
        except NoDataException:
            return None

        score = self._directionScore(
            [float(bar["close"]) for bar in fiveMinuteBars],
            [float(bar["close"]) for bar in hourlyBars],
            [float(bar["close"]) for bar in dailyBars],
        )
        if abs(score) < 2:
            return None
        structure = self.buildStructure(score > 0)
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(structure),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["lastDirectionScore"] = score
        return orders or None

    def buildStructure(self, bullish: bool) -> OptionStructureSpec:
        selector = OptionContractSelector(21, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.55)
        return LongCallSpec(selector) if bullish else LongPutSpec(selector)

    def _directionScore(self, fastBars: List[float], hourlyBars: List[float], dailyBars: List[float]) -> int:
        score = 0
        score += 1 if sum(fastBars[-4:]) / 4 > sum(fastBars[-12:]) / 12 else -1
        score += 1 if sum(hourlyBars[-3:]) / 3 > sum(hourlyBars[-8:]) / 8 else -1
        score += 1 if dailyBars[-1] > sum(dailyBars[-6:]) / 6 else -1
        return score
