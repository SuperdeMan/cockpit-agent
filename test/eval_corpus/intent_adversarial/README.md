# 意图与落域对抗语料

本目录只保存人工可审计的语义契约。运行时报告写到显式 `--out-json/--out-md`，不得在本目录留下临时结果。

设计规格：`docs/design/2026-08-02-intent-routing-adversarial-testing.md`（唯一真相源）。

## 文件

- `suites.yaml`：状态选择与重复策略，不保存 provider、model、secret。
- `coverage_exemptions.yaml`：逐 intent、逐要求的显式豁免。
- `journey_links.yaml`：链接现有 journey id，不复制旅程 gold。
- `cases/*.yaml`：按攻击机制分文件；每个文件固定 `schema_version: 1` 与 `cases:`。

## 命名与字段

- case id：`attack.family.variant`，一经进入 reviewed 不改名；替代时 retired 原项并新增 id。
- `family_id`：同源原句、paraphrase、最小变体共享；用于 seen/unseen 防泄漏。
- `tags.attacks/domains/layers`：attacks 至少含一个 `A1`–`A9` 编号；domains 声明涉及域；layers 声明 L0–L3 执行层。细分机制另放 `tags.mechanisms`。
- plan：必要组之间 AND、组内 `any_of` OR；默认禁止未声明额外 intent。
- adaptive：初始 `plan` 与 `replans[].after.result + plan` 分开写，result 形状对齐生产 observation。
- relation：变体必须同时有自己的 absolute gold，不能只写相对关系。

## 状态

`candidate → reviewed → stable → retired`。只有 `reviewed_by: human` 的案例可以进入 `reviewed`；只有固定 provider 重复稳定的案例可以进入 `stable`。`retired` 保留原文、原因和替代保护，不删除历史。

## 数据隔离

同源文本共享 `family_id`。进入 Skill/Exemplar/Hint 修复资产的 family 只能计入 `seen_regression`；未进入修复资产的 family 才能计入 `unseen_transfer`。

## 脱敏

真实 badcase 入库前删除姓名、电话、精确住址、车牌、账号与 token。无法确认脱敏完成时保持 `candidate` 且不提交原文。

## 清理

运行报告只写 `docs/reviews/eval/_ci-run-*` 或显式输出路径。corpus 内不放 trace、临时 prompt、模型原始回复和失败截图。retired 用例不删除；写 `retired_reason` 与替代 case/保护链接。
