# Cloud Shadow NLU 影子评测报告（M1b）

> 生成：2026-07-24T15:30:57.614049+00:00　commit：a10fe2a　语料：test/eval_corpus/feishu_intents_full.jsonl
> 范围 8579 条，其中 LLM 臂已覆盖 8579 条
> 本报告为离线影子评测——不改变任何运行时行为（RFC §2.3）

## 总表

| 臂 | 全量 | 端侧应接子集 |
|---|---|---|
| 规则 hit 率（coverage 口径） | 75.9% | 79.7% |
| LLM domain 准确率 | 91.2% | 90.6% |

LLM unknown 率：0.4%

## 三桶决策表（分域）

| domain | 条数 | 规则 hit | 切换候选（规则✗LLM✓） | 双失 |
|---|---|---|---|---|
| setting | 4087 | 73.2% | 24.3% | 100 |
| weather | 1514 | 84.0% | 16.1% | 0 |
| media | 1193 | 92.0% | 3.8% | 51 |
| navi | 1176 | 56.4% | 42.2% | 17 |
| information | 297 | 97.0% | 0.7% | 7 |
| phone | 129 | 70.5% | 29.5% | 0 |
| app | 126 | 81.8% | 11.9% | 8 |
| base | 57 | 5.3% | 40.4% | 31 |

## 切换建议（按 净增益×流量 排序）

1. **setting**：规则 hit 73.2%，LLM 净增 24.3%（995/4087 条）
1. **navi**：规则 hit 56.4%，LLM 净增 42.2%（496/1176 条）
1. **weather**：规则 hit 84.0%，LLM 净增 16.1%（243/1514 条）
1. **media**：规则 hit 92.0%，LLM 净增 3.8%（45/1193 条）
1. **phone**：规则 hit 70.5%，LLM 净增 29.5%（38/129 条）

## 混淆矩阵（金标 × LLM）

| gold \ llm | app | base | information | media | navi | phone | setting | unknown | weather |
|---|---|---|---|---|---|---|---|---|---|
| app | 82 |  |  | 2 | 13 | 1 | 27 | 1 |  |
| base | 11 | 23 |  | 17 |  |  | 5 | 1 |  |
| information | 20 |  | 235 |  | 10 | 12 |  | 20 |  |
| media | 134 |  |  | 1002 |  |  | 57 |  |  |
| navi | 11 | 2 | 23 |  | 1102 |  | 33 | 5 |  |
| phone | 12 |  |  |  |  | 117 |  |  |  |
| setting | 225 | 27 |  | 53 | 4 | 7 | 3761 | 10 |  |
| weather |  |  | 15 |  |  |  |  |  | 1499 |

## 槽位输出采样（无金标，仅供 Edge NLU schema 设计参考）

- [setting] 打开座椅加热 → `{"object": "座椅加热", "action": "打开"}`
- [setting] 打开主驾座椅加热 → `{"object": "主驾座椅加热", "action": "打开"}`
- [setting] 请将右座椅加热打开 → `{"object": "右座椅加热", "action": "打开"}`
- [weather] 上午天气怎么样 → `{"date": "上午"}`
- [weather] 麻烦你将大后年的天气查询 → `{"date": "大后年"}`
- [weather] 五更天的天气查询一下 → `{"date": "五更天"}`
- [app] 打开行车记录仪 → `{"object": "行车记录仪", "action": "打开"}`
- [app] 请把行车记录仪打开 → `{"object": "行车记录仪", "action": "打开"}`
- [app] 请帮我把行车记录仪打开一下 → `{"object": "行车记录仪", "action": "打开"}`
- [media] 切换音效 → `{"object": "音效", "action": "切换"}`
- [media] 请帮我把音效切换 → `{"object": "音效", "action": "切换"}`
- [media] 换音效 → `{"object": "音效", "action": "切换"}`
- [base] 上一个 → `{"action": "上一个"}`
- [base] 切换上一个 → `{"action": "切换上一个"}`
- [base] 切上一个 → `{"action": "切上一个"}`
- [phone] 接听 → `{"action": "接听"}`
- [phone] 接听一下电话 → `{"action": "接听"}`
- [phone] 把电话接听 → `{"action": "接听"}`
- [navi] 打开导航播报 → `{"object": "导航播报", "action": "打开"}`
- [navi] 麻烦你将导航信息播报打开 → `{"object": "导航信息播报", "action": "打开"}`
- [navi] 请将导航信息播报打开 → `{"object": "导航信息播报", "action": "打开"}`
- [information] 查一下深圳成指情况 → `{"keyword": "深圳成指"}`
- [information] 请帮我把标普500指数的信息查询一下 → `{"keyword": "标普500指数"}`
- [information] 请将标普500的信息查询清楚 → `{"keyword": "标普500"}`
