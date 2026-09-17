from typing import List, Optional

from investfly.models import *


class AdaptiveDebitSpreadOptionStrategy(TradingStrategy):
    """Chooses bull-call or bear-put debit spreads from an intraday trend score."""

    UNDERLYINGS = (
        Security("TSLA", SecurityType.STOCK),
    )

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, [security.symbol for security in self.UNDERLYINGS])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 12.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 25.0),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="debit-spread-roll",
                    trigger=OptionDteTrigger(atOrBelow=6),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.ALL_LEGS,
                            creditRequirement=OptionRollCreditRequirement.ALLOW_NET_DEBIT,
                            orderSpec=OrderSpec(
                                orderType=StrategyOrderType.LIMIT_ORDER,
                                orderDuration=OrderDuration.DAY,
                                unfilledTimeout=StrategyDuration(3, StrategyDurationUnit.BARS, BarInterval.FIVE_MINUTE),
                            ),
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="debit-spread-expiry-close",
                    trigger=OptionDteTrigger(atOrBelow=1),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.RETURN_TO_ENTRY_RULE),
                    ],
                ),
            ]
        )
        return CustomStrategyPolicy(
            SecurityType.OPTION,
            positionManagement=PositionManagementRules(optionGroupExits=OptionExitRules(groupRisk=groupRisk)),
            assetLifecycle=AssetLifecycleRules(option=lifecycle),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=6000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if not updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 12 * 60 + 30 or minutes > 13 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        ranked: List[tuple] = []
        for security in self.UNDERLYINGS:
            try:
                hourly = [float(bar["close"]) for bar in self.dataService.getBars(security, BarInterval.SIXTY_MINUTE, 10)]
                daily = [float(bar["close"]) for bar in self.dataService.getBars(security, BarInterval.ONE_DAY, 10)]
            except NoDataException:
                continue
            hourlyTrend = hourly[-1] / (sum(hourly[-8:]) / 8) - 1.0
            dailyTrend = daily[-1] / (sum(daily[-8:]) / 8) - 1.0
            if hourlyTrend * dailyTrend > 0:
                ranked.append((abs(hourlyTrend) + abs(dailyTrend), hourlyTrend + dailyTrend, security))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        trendStrength, direction, underlying = ranked[0]
        if trendStrength < 0.004:
            return None
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(direction > 0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(underlying, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["lastTrend"] = "bullish" if direction > 0 else "bearish"
            self.state["lastUnderlying"] = underlying.symbol
        return orders or None

    def buildStructure(self, bullish: bool) -> OptionStructureSpec:
        selector = OptionContractSelector(28, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.60)
        return BullCallDebitSpreadSpec(selector, 5.0) if bullish else BearPutDebitSpreadSpec(selector, 5.0)
