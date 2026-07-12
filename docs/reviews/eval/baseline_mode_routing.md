# 意图路由评测基线 — mode_routing

生成时间：2026-07-12T07:30:49.105127+00:00　commit：f210eb2

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| mode_deterministic | 57 | 36 | 63.2% |
| mode_typical | 40 | 36 | 90.0% |
| mode_boundary | 30 | 22 | 73.3% |
| mode_adversarial | 24 | 18 | 75.0% |
| mode_followup | 10 | 10 | 100.0% |
| mode_guardrail | 16 | 15 | 93.8% |
| **合计** | **177** | **137** | **77.4%** |

## 失败用例
- [mode_deterministic] `搜一下固态电池最新进展` — expected=['info.search'] actual=[]
- [mode_deterministic] `帮我查一下iPhone现在什么价格` — expected=['info.search'] actual=[]
- [mode_deterministic] `帮我查一下特斯拉FSD在中国的落地进展` — expected=['info.search'] actual=[]
- [mode_deterministic] `搜索量子计算商业化现状` — expected=['info.search'] actual=[]
- [mode_deterministic] `今天有什么新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `来点科技新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `看看今天的头条` — expected=['info.news'] actual=[]
- [mode_deterministic] `有什么国际新闻吗` — expected=['info.news'] actual=[]
- [mode_deterministic] `刷刷新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `新能源汽车的新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `英伟达最新动态` — expected=['info.news'] actual=[]
- [mode_deterministic] `讲讲今天的要闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `英伟达最新消息` — expected=['info.news'] actual=[]
- [mode_deterministic] `给我读读今天的国际要闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `有没有关于低空经济的新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `帮我查一下磷酸铁锂和三元锂的区别` — expected=['info.search'] actual=[]
- [mode_deterministic] `查一下固态电池` — expected=['info.search'] actual=[]
- [mode_deterministic] `搜一下你叫什么名字` — expected=['info.search'] actual=[]
- [mode_deterministic] `检索一下宁德时代最新财报要点` — expected=['info.search'] actual=[]
- [mode_deterministic] `再来点财经新闻` — expected=['info.news'] actual=[]
- [mode_deterministic] `打开空调然后来点科技新闻` — expected=['info.news'] actual=[]
- [mode_typical] `一光年大概是多少公里` — expected='chitchat' actual="search ['info.search']"
- [mode_typical] `昨晚的苹果发布会都发布了什么` — expected='search' actual="news ['info.news']"
- [mode_typical] `帮我查一下特斯拉FSD在中国的落地进展` — expected='search' actual="research ['research.run']"
- [mode_typical] `英伟达最新动态` — expected='news' actual="search ['info.search']"
- [mode_boundary] `英伟达最新消息` — expected='news' actual="search ['info.search']"
- [mode_boundary] `美联储这次降息了没有` — expected='search' actual="news ['info.news']"
- [mode_boundary] `固态电池现在发展到什么阶段了` — expected='search' actual="research ['research.run']"
- [mode_boundary] `麒麟电池和4680电池有什么区别` — expected='search' actual="research ['research.run']"
- [mode_boundary] `帮我查一下磷酸铁锂和三元锂的区别` — expected='search' actual="research ['research.run']"
- [mode_boundary] `珠穆朗玛峰有多高` — expected='chitchat' actual="search ['info.search']"
- [mode_boundary] `什么是钙钛矿电池` — expected='chitchat|search' actual="research ['research.run']"
- [mode_boundary] `苹果公司的创始人是谁` — expected='chitchat' actual="search ['info.search']"
- [mode_adversarial] `黄金今天什么价格` — expected='search' actual="stock ['info.stock']"
- [mode_adversarial] `下周有什么新电影上映` — expected='search|news' actual="chitchat ['chitchat.talk']"
- [mode_adversarial] `秦始皇哪一年统一六国` — expected='chitchat' actual="search ['info.search']"
- [mode_adversarial] `搜一下你叫什么名字` — expected='search' actual="chitchat ['chitchat.talk']"
- [mode_adversarial] `搜索引擎是怎么工作的` — expected='chitchat' actual="research ['research.run']"
- [mode_adversarial] `查理和巧克力工厂是谁写的` — expected='chitchat' actual="search ['info.search']"
- [mode_guardrail] `打开空调然后来点科技新闻` — expected='news' actual="chitchat ['chitchat.talk']"

## 数据来源
| 来源 | 用例数 |
|---|---|
| test/eval_corpus/mode_routing_cases.yaml | 122 |

## 混淆矩阵（期望首选 × 实际）

| expected \ actual | chitchat | news | other:manual.query | other:navigation.navigate_to | other:nearby.search | other:reminder.create | other:reminder.list | other:trip.plan | research | search | sports | stock | weather |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chitchat | 23 |  |  |  |  |  |  |  | 2 | 5 |  |  |  |
| news | 1 | 15 |  |  |  |  |  |  |  | 3 |  |  |  |
| other:manual.query |  |  | 2 |  |  |  |  |  |  |  |  |  |  |
| other:navigation.navigate_to |  |  |  | 2 |  |  |  |  |  |  |  |  |  |
| other:nearby.search |  |  |  |  | 2 |  |  |  |  |  |  |  |  |
| other:reminder.create |  |  |  |  |  | 2 |  |  |  |  |  |  |  |
| other:reminder.list |  |  |  |  |  |  | 1 |  |  |  |  |  |  |
| other:trip.plan |  |  |  |  |  |  |  | 1 |  |  |  |  |  |
| research |  |  |  |  |  |  |  |  | 22 |  |  |  |  |
| search | 2 | 2 |  |  |  |  |  |  | 4 | 21 |  | 2 |  |
| sports |  |  |  |  |  |  |  |  |  | 1 | 2 |  |  |
| stock |  |  |  |  |  |  |  |  |  |  |  | 2 |  |
| weather |  |  |  |  |  |  |  |  |  |  |  |  | 3 |

> active provider：`minimax:MiniMax-M3`　CLARIFY_ENABLED=off　live 120 例 + 确定性子集 57 例
