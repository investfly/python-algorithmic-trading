from typing import List, Optional

from investfly.models import *


class EventAwareGapFadeStrategy(TradingStrategy):
    """Scheduled opening-gap fade that avoids news-driven repricing events."""

    SYMBOLS = ["AAPL", "NVDA", "TSLA"]

    def getSecurityUniverseSelector(self) -> SecurityUniverseSelector:
        return SecurityUniverseSelector.fromSymbols(SecurityType.STOCK, self.SYMBOLS)

    def getStrategyPolicy(self) -> CustomStrategyPolicy:
        return CustomStrategyPolicy(
            SecurityType.STOCK,
            positionManagement=PositionManagementRules(
                positionExits=ExitRules(
                    protectiveExits=ProtectiveExitPlan(
                        initialProtection=StopSpec.fixedStop(
                            FixedStopRule(FixedStopThreshold(FixedStopThresholdType.PERCENT_FROM_ENTRY, -1.25))
                        ),
                        profitTargets=StagedProfitTargets.percentGain(1.0, 100.0),
                        maxHold=StrategyDuration(90, StrategyDurationUnit.MINUTES),
                        sessionClose=SessionTimeTrigger(SessionTimeTriggerType.AT_TIME, time="11:00"),
                    )
                )
            ),
            portfolioLimits=PortfolioLimits(
                maxOpenPositions=1,
                maxOpenPositionsPerSymbol=1,
                maxPositionExposure=PositionExposureLimit(maxPositionPct=15.0),
            ),
        )

    @data_trigger(type=DataType.BARS, barInterval=BarInterval.ONE_MINUTE)
    def onMarketData(self, updatedSecurities: List[Security]) -> Optional[List[TradeOrder]]:
        # The scheduled jobs own the trade lifecycle; this trigger supplies the intraday clock
        # and makes current 1-minute bars available to the 09:35/11:00 callbacks.
        return None

    @scheduled(TriggerSchedule.daily("09:35"))
    def enterGapFade(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        now = self.context.currentTime
        eventDay = f"{now.year:04d}-{now.month:02d}-{now.day:02d}"
        if self.state.get("lastEventDay") == eventDay:
            return None
        if self.getPortfolio().openPositions:
            return None

        candidates: List[tuple[Security, PositionType, float]] = []
        for symbol in self.SYMBOLS:
            security = Security(symbol, SecurityType.STOCK)
            try:
                minuteBars = self.dataService.getBars(security, BarInterval.ONE_MINUTE, 66)
                dailyBars = self.dataService.getBars(security, BarInterval.ONE_DAY, 2)
                news = self.dataService.getNews(security)
            except NoDataException:
                continue
            sessionBars = [
                bar
                for bar in minuteBars
                if bar["date"].year == now.year and bar["date"].month == now.month and bar["date"].day == now.day
            ]
            if len(sessionBars) < 5 or len(dailyBars) < 2 or self._hasEventRisk(news):
                continue

            priorClose = float(dailyBars[-2]["close"])
            sessionOpen = float(sessionBars[0]["open"])
            currentPrice = float(sessionBars[-1]["close"])
            if priorClose <= 0 or sessionOpen <= 0:
                continue
            gapPct = sessionOpen / priorClose - 1.0
            if abs(gapPct) < 0.004 or abs(gapPct) > 0.06:
                continue
            priorVolumes = [float(bar["volume"]) for bar in minuteBars[:-len(sessionBars)]]
            openingVolume = sum(float(bar["volume"]) for bar in sessionBars)
            averageOpeningVolume = (sum(priorVolumes[-20:]) / min(len(priorVolumes), 20)) * len(sessionBars) if priorVolumes else openingVolume
            relativeVolume = openingVolume / max(averageOpeningVolume, 1.0)
            reversalPct = currentPrice / sessionOpen - 1.0
            side: PositionType | None = None
            if gapPct > 0 and reversalPct < -0.0002:
                side = PositionType.SHORT
            elif gapPct < 0 and reversalPct > 0.0002:
                side = PositionType.LONG
            if side is None:
                continue
            score = abs(gapPct) * abs(reversalPct) * max(relativeVolume, 0.5)
            candidates.append((security, side, score))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[2], reverse=True)
        security = candidates[0][0]
        side = candidates[0][1]
        execution = OpenExecutionSettings(
            positionSize=PositionSizeSpec(PositionSizeMode.PERCENT_OF_EQUITY, 12.0),
            orderSpec=OrderSpec(
                orderType=StrategyOrderType.LIMIT_ORDER,
                orderDuration=OrderDuration.DAY,
                limitPriceOffset=LimitPriceOffset(LimitPriceOffsetUnit.PERCENT, 0.0),
                unfilledTimeout=StrategyDuration(10, StrategyDurationUnit.MINUTES),
            ),
            instrumentSelection=DirectInstrumentSelection(),
        )
        orders = self.services.planOpenOrders(OpenOrderRequest(security, execution, side=side))
        if orders:
            self.state["lastEventDay"] = eventDay
            self.state["lastEventSymbol"] = security.symbol
        return orders or None

    @scheduled(TriggerSchedule.daily("11:00"))
    def closeMorningFades(self, event: ScheduleEvent) -> Optional[List[TradeOrder]]:
        positions = [
            position
            for position in self.getPortfolio().openPositions or []
            if position.security.symbol in self.SYMBOLS
        ]
        orders = self.services.planCloseOrders(positions)
        return orders or None

    def _hasEventRisk(self, news: List[StockNews]) -> bool:
        keywords = ["earnings", "guidance", "merger", "acquisition", "fda", "offering"]
        for article in news[:10]:
            title = article.title.lower()
            if any(keyword in title for keyword in keywords):
                return True
        return False
