# 旅程级 e2e 报告（journeys_report）

- 生成时间：2026-07-15 10:39:55（耗时 31.7s）
- active LLM：`mimo:mimo-v2.5-pro`（跨 provider 结果不可直接对比）
- 车道：all
- **回归级 3/3**（必须全绿）；目标级 0/0（红灯=工程 backlog）
- 时延（全轮）：P50=11.1s P95=11.1s max=11.7s n=3

## 记分卡

| 维度 | 通过 |
|---|---|
| autonomy | 2/2 |
| honesty | 2/2 |

## 旅程明细

| id | 级别 | 结果 | 说明 |
|---|---|---|---|
| A1-2 导航+沿途充电一句话（waypoint 并入 + 不重复导航） | regression | ✅ pass |  |
| A3-1 时效问题不落陈旧直答：联网/改派/诚实三容忍 | regression | ✅ pass |  |
| A5-2 数据界外诚实不编造（免费档日期门） | regression | ✅ pass |  |
