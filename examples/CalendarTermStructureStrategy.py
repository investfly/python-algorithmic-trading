from typing import List, Optional

from investfly.models import *


class CalendarTermStructureStrategy(TradingStrategy):
    """Chooses call or put calendars after inspecting trend and listed expiries."""

    UNDERLYING = Security("AAPL", SecurityType.STOCK)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.singleStock(self.UNDERLYING.symbol)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        groupRisk = OptionGroupRiskRules(
            profitTarget=OptionGroupProfitRule(OptionProfitThresholdType.NET_PREMIUM_PCT, 12.0),
            stopLoss=OptionGroupLossRule(OptionLossThresholdType.NET_PREMIUM_PCT, 30.0),
        )
        rollOrder = OrderSpec(
            orderType=StrategyOrderType.LIMIT_ORDER,
            orderDuration=OrderDuration.DAY,
            unfilledTimeout=StrategyDuration(4, StrategyDurationUnit.BARS, BarInterval.FIFTEEN_MINUTE),
        )
        lifecycle = OptionLifecycleRules(
            rules=[
                OptionLifecycleRule(
                    id="calendar-call-near-roll",
                    trigger=OptionDteTrigger(atOrBelow=3, legId="nearShortCall"),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.MUST_BE_NET_CREDIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="calendar-put-near-roll",
                    trigger=OptionDteTrigger(atOrBelow=3, legId="nearShortPut"),
                    actions=[
                        OptionRollAction(
                            scope=OptionActionScope.TRIGGER_LEG,
                            creditRequirement=OptionRollCreditRequirement.MUST_BE_NET_CREDIT,
                            orderSpec=rollOrder,
                        )
                    ],
                ),
                OptionLifecycleRule(
                    id="calendar-call-far-close",
                    trigger=OptionDteTrigger(atOrBelow=5, legId="farLongCall"),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.WAIT_UNTIL_ORIGINAL_EXPIRATION),
                    ],
                ),
                OptionLifecycleRule(
                    id="calendar-put-far-close",
                    trigger=OptionDteTrigger(atOrBelow=5, legId="farLongPut"),
                    actions=[
                        OptionCloseLegsAction(scope=OptionActionScope.ALL_LEGS),
                        OptionCompleteAction(entryRearmPolicy=OptionEntryRearmPolicy.WAIT_UNTIL_ORIGINAL_EXPIRATION),
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
                optionGroupExposure=OptionGroupExposureLimits(maxGroupRiskPct=3.0, maxGroupNotional=9000.0, maxContracts=1),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIFTEEN_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        if self.UNDERLYING not in updatedSecurities or self.getPortfolio().openPositions:
            return None
        now = self.context.currentTime
        minutes = now.hour * 60 + now.minute
        sessionDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if now.weekday() >= 5 or minutes < 10 * 60 or minutes > 11 * 60 or self.state.get("lastEntryDay") == sessionDay:
            return None
        try:
            intraday = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.FIFTEEN_MINUTE, 20)]
            daily = [float(bar["close"]) for bar in self.dataService.getBars(self.UNDERLYING, BarInterval.ONE_DAY, 12)]
        except NoDataException:
            return None
        try:
            self.state["listedExpiryCount"] = len(self.dataService.listOptionExpiries(self.UNDERLYING.symbol))
        except Exception:
            self.state["listedExpiryCount"] = 0
        intradayTrend = intraday[-1] / (sum(intraday[-12:]) / 12) - 1.0
        dailyTrend = daily[-1] / (sum(daily[-10:]) / 10) - 1.0
        if intradayTrend * dailyTrend <= 0 or abs(intradayTrend) < 0.001:
            return None

        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.FIXED_QUANTITY, 1),
            orderSpec=OrderSpec(orderType=StrategyOrderType.MARKET_ORDER, orderDuration=OrderDuration.DAY),
            instrumentSelection=OptionStructureSelection(self.buildStructure(intradayTrend > 0)),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(self.UNDERLYING, execution))
        if orders:
            self.state["lastEntryDay"] = sessionDay
            self.state["calendarDirection"] = "call" if intradayTrend > 0 else "put"
        return orders or None

    def buildStructure(self, bullish: bool) -> OptionStructureSpec:
        nearSelector = OptionContractSelector(14, StrikeSelectionMode.TARGET_DELTA, targetDelta=0.50)
        return LongCallCalendarSpreadSpec(nearSelector, 45) if bullish else LongPutCalendarSpreadSpec(nearSelector, 45)
