"""股票域：行情 + K 线（Tushare A 股，失败降级东方财富实时行情/全市场）。"""
from __future__ import annotations
import logging

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError

logger = logging.getLogger("agent.info")


class StockMixin:
    async def _stock(self, intent, ctx, meta) -> AgentResult:
        symbol = (intent.slots.get("symbol") or "").strip()
        if not symbol:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪只股票或指数？",
                               follow_up="请告诉我股票名称或代码", missing_slots=["symbol"])
        stock_provider = self.stock
        try:
            q = await self.stock.quote(symbol, meta=meta)
        except ProviderError as e:
            logger.warning("tushare quote failed: %s", e)
            # Tushare 失败（如无港美股权限）→ 降级到东方财富实时行情（免费，全市场）
            if self._stock_eastmoney:
                try:
                    q = await self._stock_eastmoney.quote_text(symbol, meta=meta)
                    stock_provider = self._stock_eastmoney  # history 也用东方财富
                except ProviderError as e2:
                    logger.warning("eastmoney quote also failed: %s", e2)
                    return AgentResult(
                        status=FAILED,
                        speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。"
                               f"您可以试试用代码查询，如「600519」（A股）、「00700」（港股）。",
                    )
            else:
                return AgentResult(
                    status=FAILED,
                    speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。",
                )
        try:
            candles = await stock_provider.history(symbol, limit=20, meta=meta)
        except ProviderError as e:
            # 报价仍然有价值；历史失败时不混用 mock K 线误导用户。
            logger.warning("stock history unavailable, leaving chart empty: %s", e)
            candles = []

        parts = [f"{q.name or symbol}"]
        if q.price:
            parts.append(f"当前价{q.price}")
        if q.change and q.change_pct:
            direction = "跌" if q.change.startswith("-") else "涨"
            parts.append(f"，{direction}{q.change}（{q.change_pct}）")
        speech = "".join(parts) + "。"

        card = {"type": "stock_quote", "name": q.name, "symbol": q.symbol,
                "price": q.price, "change": q.change, "change_pct": q.change_pct,
                "market_time": q.market_time, "market": getattr(q, "market", "") or "",
                "candles": [
                    {"date": candle.date, "open": candle.open, "high": candle.high,
                     "low": candle.low, "close": candle.close, "volume": candle.volume}
                    for candle in candles
                ]}
        return AgentResult(speech=speech, ui_card=card, data={"quote": card})
