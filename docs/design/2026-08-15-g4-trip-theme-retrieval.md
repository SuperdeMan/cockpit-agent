# G4 主题行程：trip 候选池加「主题检索步」（EVA 二轮 P3 簇 · 独立 RFC）

- **状态**：已落地（本文件 §6 实施记录；泓舟 2026-08-15 授权处理 EVA 二轮余项，缺口分析已批「P3 簇另立 RFC 再动」路径）
- **交付对象**：本会话实施
- **关联**：[缺口分析](2026-08-14-eva-round2-capability-gaps.md) §2-G4、§1.1 #4/#8；
  `agents/trip_planner/src/{extract,pipeline,models,agent}.py`

---

## 0. 现状与问题（证据）

1. **主题→POI 被结构性挡死**：trip 候选池固定为高德「{城市} 景点/美食」top8
   （`pipeline.py::build_poi_pool`），LLM 骨架**不得越池、池外名字无条件丢弃**
   （`_parse_skeleton`，已亲验）——这是防幻觉的**正确设计**，但把「《太平年》取景地」
   这类主题知识也一并挡死了。
2. **落域进不去**：「跟着《太平年》游杭州」不含天数/偏好/行程触发词，
   `extract_trip` 的出行判定不成立；「北上追春天」的正确形态（NEED_SLOT 追问）
   已具备，缺的是这句能稳定落进 trip.plan。

## 1. 目标

「跟着《X》游 Y」「X 同款打卡」型请求落进 trip.plan，并让行程里出现**经高德接地
验证的**主题相关地点；池的封闭纪律不变——只是入池来源多一路，接地失败的主题
候选照旧丢弃，绝不臆造。

## 2. 方案

### 2.1 抽取面（`extract.py`）

- 新增独立函数 `extract_theme(text) -> str`：`《X》`书名号 / 「跟着 X 游/玩」/
  「X 同款/取景地/打卡地」三族确定性解析（不动 `extract_trip` 的三元组 API）。
- `extract_trip` 的出行判定补一条：**主题在场也算 trigger**（「跟着《太平年》游杭州」
  此前四个信号一个不中）。

### 2.2 主题检索步（`pipeline.py::build_theme_pool`）

```
theme 非空 → LLM 产候选地名 JSON 数组（≤8，明示「只列真实存在的具体地点，
不确定就少列」）→ 逐个「{dest}{名}」高德搜索 + name_matches 校验
（失败退裸名再试一次）→ 通过者入池（POI 即搜索结果，坐标来自地图不来自 LLM）
```

- **幻觉防线与 landmark_candidates 同款**：LLM 只产「名字候选」，坐标/存在性由高德
  裁决；接不到的候选直接丢，池子不足由普通池兜着。
- 搜索词带 `dest` 前缀是城市约束（缺口分析 §2-G3 的「城市偏置风险」同款处理：
  「鼓楼」这类多义名不带城市必然接错）。
- 主题池与普通池**并集去重**；主题池的名字单独传给 propose 的 prompt 提示
  「优先编排主题相关地点」（与 weather_hint 同款织入方式，仍只能选池内名字）。

### 2.3 模型与话术

- `Trip` 加 `theme: str = ""`（`from_dict` 容错，向后兼容）；`narrate` 开头带
  「按《X》主题」；卡片经 `card_dict()` 自然携带。
- manifest `trip.plan` 加 `theme` 槽（planner 可直接填；extract 兜底——与 G1
  `arrive_by` 同款双轨）+ 判别化描述 + examples。

### 2.4 落域侧

- exemplars `trip.yaml` +2：主题行程（换主题换城市教签名，不抄 EVA 原句）、
  「北上追春天」型方向性模糊（落 trip.plan 由 NEED_SLOT 追问——正确形态已在）。
- L0 语料 +2 reviewed（一正一硬负：谈论影视内容本身不落 trip）；`suites.yaml`
  584→586（第七次适用：既有 intent 的全新话术族，G4 兑现物）。

### 2.5 刻意不做（v1 边界）

- **联网检索主题候选**（info.search/Exa 通道）：v1 只用 planner LLM 的内化知识
  + 高德验证。理由：多一跳网络依赖换取的候选质量未证，而接地校验才是质量的
  真正闸门；LLM 知识覆盖不了的冷门主题诚实降级为普通行程（话术如实说）。
- 「桥上看江景≠终点设在机动车道」可达性校验（缺口分析 §1.1#9③）：独立缺口另立。
- 方向性主题（「北上追春天」）的目的地推荐：维持 NEED_SLOT 追问，不代用户选城市。

## 3. 安全红线自查

- LLM 仍只产名字，坐标全部来自高德接地；池外丢弃纪律逐字保留。
- 主题步失败/空结果 → 普通池兜底 + 话术如实（不假装有主题内容）。

## 4. 验收

- 单测：extract（theme 三族/主题算 trigger）、pipeline（接地校验拒错名/LLM 失败
  降级）、agent（theme 贯穿 propose 提示与话术）。
- L0 门禁 + exemplars 门禁 + trip/cloud 回归全绿；catalog 锚点随 manifest 更新。

## 5. 风险

- LLM 对冷门主题产不出可接地候选 → 行为=普通行程 + 「主题相关地点暂未能确认」
  话术，不比现状差。
- 主题词族误触发（「《流浪地球》好看吗」）→ 硬负例语料锁住 + extract_theme 只在
  trip 语境消费（extract_trip 仍要求目的地在场才成行）。

---

## 6. 实施记录（2026-08-15）

按 §2 落地。一处实施发现：`_THEME_TAG_RE` 的 lazy 捕获会吞句首动词
（「去打卡繁花同款」→「去打卡繁花」）——补了动词前缀剥离循环（书名号族不受影响）。

- **代码面**：`extract.py`（`extract_theme` 三族 + 主题算 trip trigger）、
  `pipeline.py`（`build_theme_pool` LLM 提议→「{城市}{名}」优先接地→`name_matches`
  拒错名；`theme_hint` 与 weather_hint 同款织入）、`models.py`（`Trip.theme`）、
  `agent.py`（theme 槽/原话双轨 + 接地成功才标主题）、manifest（theme 槽 +
  判别化描述含「只聊作品内容」排除条款）。
- **测试**：trip_planner **56** 全绿（新 9：theme 抽取 4 + theme pool 接地/降级/
  城市前缀 4 + narrate 主题 1）；catalog 锚点 12249→**12340**（+91，条数 144 不变）。
- **落域资产**：exemplars trip +2（主题行程换 IP 换城市教签名；「南方追春天」
  方向性模糊维持 NEED_SLOT 形态）；L0 语料 +2 reviewed（composition：主题→trip.plan
  正例 + 「聊作品内容」硬负），`suites.yaml` 584→**586** 第七次适用。
- **门禁**：check_intent_gate 2/2 exit 0（81/81、586 恰好用满）；exemplars 门禁
  PASS（域错配 2.4% 持平）。
