"""股票域：行情 + K 线（Tushare A 股，失败降级东方财富实时行情/全市场）。"""
from __future__ import annotations
import logging

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.provenance import attach
from runtime.session_facts import PROVENANCE_MARKERS

logger = logging.getLogger("agent.info")

# 来源问句词表 2026-08-28 下沉到 `runtime/session_facts.PROVENANCE_MARKERS`（C4），
# 这里直接消费那一份、**不再留本地别名**（别名就是第二个名字）。
# 理由：这份判据此前**只有路由到 info.stock 之后才够得着**，而 QA T41 的病恰恰是
# 没路由到（MiniMax 把「数据源是什么」落给了 chitchat，编出「东方财富 19:23」）。
# 编排层要在落域**之前**用同一条判据，两边只许有一份。
# 下沉时净增两条说法（`数据来源`/`什么时候更新`），是同族补全不是扩面。


def _source_label(vendor: str) -> str:
    return {
        "tushare": "Tushare",
        "eastmoney": "东方财富代码解析与新浪行情",
        "mock": "模拟行情",
        "mock-stock": "模拟行情",
        "alphavantage": "Alpha Vantage",
    }.get((vendor or "").lower(), vendor or "未标明的数据源")


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
        raw_text = str(intent.raw_text or "")
        # “来源”本身可能在问公司收入/业务来源，不能因上一轮股票焦点就被
        # 改写成行情 provenance。只消费明确指向行情数据或更新时间的词组。
        provenance_query = any(mark in raw_text for mark in PROVENANCE_MARKERS)
        candles = []
        if not provenance_query:
            try:
                candles = await stock_provider.history(symbol, limit=20, meta=meta)
            except ProviderError as e:
                # 报价仍然有价值；历史失败时不混用 mock K 线误导用户。
                logger.warning("stock history unavailable, leaving chart empty: %s", e)

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
        # 真实性标记：主路径按配置源（tushare/mock）；东财降级路径如实标 degraded。
        # `data_time` 是**行情自己的时刻**（C4-A）：跨轮来源追问要复述的是它，不是取数
        # 时刻——真栈 T41 编出的「19:23 前后」正是把取数时刻当成了行情时刻的那个形态。
        # 称呼由这里声明，编排层的来源读出口照着念（它不该认识「行情」这个词）。
        if stock_provider is self.stock:
            attach(card, self.stock, data_time=q.market_time or "",
                   data_time_label="行情时间")
        else:
            attach(card, "eastmoney", mode="degraded", note="Tushare 失败降级东方财富",
                   data_time=q.market_time or "", data_time_label="行情时间")
        if provenance_query:
            vendor = str((card.get("_prov") or {}).get("vendor") or "")
            when = q.market_time or "上游未提供"
            speech = f"数据来源是{_source_label(vendor)}，行情时间是{when}。"
        return AgentResult(speech=speech, ui_card=card, data={"quote": card})
