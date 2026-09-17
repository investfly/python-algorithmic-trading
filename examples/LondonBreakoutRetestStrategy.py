from typing import List, Optional

from investfly.models import *


class LondonBreakoutRetestStrategy(TradingStrategy):
    """GBP/USD Asian-range breakout and retest with a false-breakout reversal state machine."""

    SECURITY = Security("GBP-USD", SecurityType.FOREX)

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.FOREX, [self.SECURITY.symbol])

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        protectiveExits = ProtectiveExitPlan(
            executionPolicy=ProtectiveExecutionPolicy.INVESTFLY_MANAGED_ONLY,
            initialProtection=StopSpec.fixedStop(
                FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PIPS_FROM_ENTRY, -40.0))
            ),
            profitTargets=StagedProfitTargets(
                ProfitThresholdType.PIPS,
                [ProfitTargetTier(60.0, 100.0)],
            ),
            maxHold=StrategyDuration(12, StrategyDurationUnit.HOURS),
            sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="11:30"),
        )
        return CustomStrategyPolicy(
            SecurityType.FOREX,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(protectiveExits=protectiveExits)
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxTotalMarginPct=30.0,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=15.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.FIVE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        try:
            bars = self.dataService.getBars(self.SECURITY, BarInterval.FIVE_MINUTE, 80)
            trendBars = self.dataService.getBars(self.SECURITY, BarInterval.SIXTY_MINUTE, 25)
        except NoDataException:
            return None
        if len(bars) < 30 or len(trendBars) < 13:
            return None

        currentBar = bars[-1]
        currentTime = self.context.currentTime
        sessionKey = f"{currentTime.year:04d}-{currentTime.month:02d}-{currentTime.day:02d}"
        if self.state.get("rangeSession") != sessionKey:
            rangeBars = self._asianRangeBars(bars[:-1], currentTime)
            if len(rangeBars) < 6:
                return None
            self.state["rangeSession"] = sessionKey
            self.state["rangeHigh"] = max(float(bar["high"]) for bar in rangeBars)
            self.state["rangeLow"] = min(float(bar["low"]) for bar in rangeBars)
            self.state.pop("breakoutSide", None)
            self.state.pop("breakoutBar", None)
            self.state.pop("pendingReverseSide", None)
            self.state.pop("activeBoundary", None)
            self.state["reversalUsed"] = False

        rangeHigh = float(self.state["rangeHigh"])
        rangeLow = float(self.state["rangeLow"])
        currentClose = float(currentBar["close"])
        if currentTime.hour < 3:
            self.state["rangeHigh"] = max(rangeHigh, float(currentBar["high"]))
            self.state["rangeLow"] = min(rangeLow, float(currentBar["low"]))
            return None
        positions = [
            position
            for position in self.getPortfolio().openPositions or []
            if position.security.symbol == self.SECURITY.symbol
        ]
        if currentTime.hour > 11 or (currentTime.hour == 11 and currentTime.minute >= 30):
            return self.services.planCloseOrders(positions) or None

        if positions:
            position = positions[0]
            activeBoundary = float(self.state.get(
                "activeBoundary",
                rangeHigh if position.position == PositionType.LONG else rangeLow,
            ))
            if self.state.get("reversalUsed", False):
                failedLong = position.position == PositionType.LONG and currentClose < activeBoundary - 0.0005
                failedShort = position.position == PositionType.SHORT and currentClose > activeBoundary + 0.0005
            else:
                failedLong = position.position == PositionType.LONG and currentClose < rangeLow - 0.0002
                failedShort = position.position == PositionType.SHORT and currentClose > rangeHigh + 0.0002
            if failedLong or failedShort:
                if not self.state.get("reversalUsed", False):
                    self.state["pendingReverseSide"] = (
                        PositionType.SHORT.value if position.position == PositionType.LONG else PositionType.LONG.value
                    )
                    self.state["activeBoundary"] = rangeLow if position.position == PositionType.LONG else rangeHigh
                return self.services.planCloseOrders(positions) or None
            return None

        pendingReverse = self.state.get("pendingReverseSide")
        if pendingReverse is not None:
            reverseSide = PositionType(pendingReverse)
            orders = self.services.planOpenOrders(
                OpenOrderRequest(
                    self.SECURITY,
                    self._execution(15.0, marketOrder=True),
                    side=reverseSide,
                    existingPositionPolicy=EntryExistingPositionPolicy(
                        sameTarget=ExistingTargetPolicy(ExistingTargetAction.IGNORE_NEW_ENTRY),
                        oppositeSide=EntryConflictPolicy(EntryConflictAction.CLOSE_OPPOSITE_THEN_OPEN),
                    ),
                )
            )
            if orders:
                self.state.pop("pendingReverseSide", None)
                self.state["reversalUsed"] = True
                self.state["lastEntrySession"] = sessionKey
            return orders or None

        if self.state.get("lastEntrySession") == sessionKey or currentTime.hour >= 8:
            return None
        priorClose = float(bars[-2]["close"])
        trendMove = float(trendBars[-1]["close"]) / float(trendBars[-13]["close"]) - 1.0
        breakoutSide = self.state.get("breakoutSide")
        if breakoutSide is None:
            if priorClose <= rangeHigh + 0.0002 and currentClose > rangeHigh + 0.0002 and trendMove > 0.002:
                self.state["breakoutSide"] = PositionType.LONG.value
                self.state["breakoutBar"] = currentBar["date"].isoformat()
            elif priorClose >= rangeLow - 0.0002 and currentClose < rangeLow - 0.0002 and trendMove < -0.002:
                self.state["breakoutSide"] = PositionType.SHORT.value
                self.state["breakoutBar"] = currentBar["date"].isoformat()
            return None

        if self.state.get("breakoutBar") == currentBar["date"].isoformat():
            return None
        side = PositionType(breakoutSide)
        retested = (
            side == PositionType.LONG
            and float(currentBar["low"]) <= rangeHigh + 0.0002
            and currentClose > rangeHigh
        ) or (
            side == PositionType.SHORT
            and float(currentBar["high"]) >= rangeLow - 0.0002
            and currentClose < rangeLow
        )
        if not retested:
            return None

        orders = self.services.planOpenOrders(
            OpenOrderRequest(self.SECURITY, self._execution(15.0), side=side)
        )
        if orders:
            self.state["lastEntrySession"] = sessionKey
            self.state["activeBoundary"] = rangeHigh if side == PositionType.LONG else rangeLow
        return orders or None

    def _asianRangeBars(self, bars: List[Bar], currentTime) -> List[Bar]:
        sameDateBars = [
            bar
            for bar in bars
            if bar["date"].year == currentTime.year
            and bar["date"].month == currentTime.month
            and bar["date"].day == currentTime.day
            and bar["date"].hour < 3
        ]
        return sameDateBars if len(sameDateBars) >= 6 else bars[-24:]

    def _execution(self, allocationPct: float, marketOrder: bool = False) -> OpenExecutionSettings:
        orderSpec = OrderSpec(
            orderType=StrategyOrderType.MARKET_ORDER if marketOrder else StrategyOrderType.LIMIT_ORDER,
            orderDuration=OrderDuration.DAY,
            limitPriceOffset=None if marketOrder else LimitPriceOffset(LimitPriceOffsetUnit.PIPS, 1.0),
            unfilledTimeout=None if marketOrder else StrategyDuration(10, StrategyDurationUnit.MINUTES),
        )
        return OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, allocationPct),
            orderSpec=orderSpec,
            instrumentSelection=DirectInstrumentSelection(),
        )
