# CA2-03：稳定任务与来源目标引用

> 状态：隔离实现与云侧全族验证完成，已合入并发布，精确版本/真栈样本见首批执行记录 §5。依赖 CA2-02。
> 本包只完成 Plan/挂起/活动任务帧的稳定身份；SDK 持久操作账本扩展仍归 CA2-08。

## 最小契约

Plan 增加 task_id、plan_revision、goal_refs；Step 增加 goal_ids。
ID 由服务端产生，沿已有 step_record / pending_plan 保存和恢复；
活动任务帧复用同一份 ID，改口仍走已有 _apply_task_patch，不建立另一套状态机。

goal_refs 记录 goal_id、origin_exchange_id、source_sha256、start/end 与 coverage。
它们是原话分句的来源引用，**不是已证实的语义诉求分解**；coverage 当前一律 unknown。
一个步骤可引用多个原文片段；无法绑定就留空，不从现有计划倒推“用户只提了这些”。
PLANNER_GOALS 保持 off；模型 goals/covers 不参与 ID、来源或完成判断。

## 生命周期

- 新请求生成 task ID 和来源引用；相同文本的新请求不复用旧 ID。
- 补槽/确认恢复保留 task/goal ID，并递增处理版本。
- T2/改派在同一请求上下文继承任务，产生新批时版本递增；不生成另一套任务。
- 已有活动任务允许的显式改口沿用 task ID / 对应 goal ID，更新该目标的来源版本；
  其它引用保留。旧活动帧无引用时保持未知，不补造历史目标。
- 旧挂起记录没有来源轮次时维持原行为；损坏的新身份记录不能恢复成有效执行计划。

PlanContext.task_identity 只是请求内传递，不来自客户端 prefs，不下发 Agent 当授权。
确认权仍由既有 operation_id / 服务端确认流程维护，task_id 和 goal_id 都不是凭证。

## 验证与已发现回归

定向 74 passed：包括真实引擎离线 stub 的“补槽→挂确认”、序列化/恢复、连续 T2 身份、
改口、模型伪造覆盖、未知旧数据、坏记录和独立请求隔离。

云侧全族首跑 2118 passed / 2 failed / 1 skipped；两条现有断言指出 CA2-02 的改派接线
把恢复轮“拿铁”业务输入替换成了安全原话。已修为两者分开：改派的业务起点仍为本轮答案，
授权继续使用原任务 safety_origin_text；缺少可证明的范围时不构造范围引用。
原断言保持；修后云侧全族 **2120 passed / 1 skipped**，53.98 s。
