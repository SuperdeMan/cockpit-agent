"""规划知识 A/B 的容器内载荷：在 cloud-planner 容器里只规划、不执行任何一步（驱动见 `scripts/ab_planner_knowledge.py`）。

用法（由驱动经 `docker exec -i <cloud-planner> python - <base64 配置>` 送进容器，本机不直接跑）：配置是 JSON 的 base64，字段见驱动。

- 两臂逐条交替串行：同一句、同一遍先 A 后 B，时间漂移两臂均摊；技能库是进程级全局，并发会串臂，所以不并发。
- B 臂两种变体：换一份改过某个指南的技能库副本（`/tmp` 下，不碰线上文件），或改本次内存里目录中某个能力的描述（只改这一次装配出的副本）。
- 每条输出带该指南正文的摘要（`guide_sha`）与改了几处描述（`patched`），用来核对 B 臂确实注入了改过的那份。
- 只调 `ContextManager.assemble(mem_on=False)` 与 `PlanBuilder.build`：不派发、不建挂起、不读写任何用户记忆。
"""
import asyncio
import base64
import hashlib
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path


def decode_config(raw: str) -> dict:
    config = json.loads(base64.b64decode(raw).decode("utf-8"))
    if not isinstance(config, dict) or not config.get("texts") or int(config.get("n") or 0) < 1:
        raise ValueError("config needs texts and n")
    return config


def patch_catalog(ws, config: dict, arm: str) -> int:
    """B 臂改目录里某个能力的描述；返回改了几处（同一个 Agent 在预筛目录与完整注册表里可能是同一个对象，只算一次）。"""
    patch = config.get("description_b")
    if arm != "B" or not patch:
        return 0
    seen, patched = set(), 0
    for agent in list(getattr(ws, "catalog", None) or []) + list(getattr(ws, "registry_agents", None) or []):
        if agent.manifest.agent_id != patch["agent_id"] or id(agent) in seen:
            continue
        seen.add(id(agent))
        for cap in agent.manifest.capabilities:
            if cap.intent == patch["intent"]:
                cap.description = patch["description"]
                patched += 1
    return patched


def skill_stores(skills_mod, config: dict) -> dict:
    """两臂的技能库：A 是线上那份；B 在给了 `guide_name` 时是 `/tmp` 下换了那份指南的副本，否则与 A 相同。"""
    base = skills_mod.SkillStore()
    if not config.get("guide_name"):
        return {"A": base, "B": base}
    root = Path(tempfile.mkdtemp(prefix="ab-skills-"))
    shutil.copytree(base.root, root, dirs_exist_ok=True)
    (root / "guides" / (config["guide_name"] + ".yaml")).write_text(config["guide_yaml_b"], encoding="utf-8")
    return {"A": base, "B": skills_mod.SkillStore(root=str(root))}


async def plan_once(planner, cm, skills_mod, stores, config, arm, text, i, set_llm_pin, plan_context):
    skills_mod._default_store = stores[arm]
    set_llm_pin(config["provider"], config["model"])
    ctx = plan_context(request_id=uuid.uuid4().hex, session_id=f"ab-{config['name']}-{arm}-{i}",
                       user_id="ab-planner", vehicle_id="v1", raw_text=text, safety_origin_text=text)
    start = time.monotonic()
    try:
        ws = await cm.assemble(text, ctx, mem_on=False)
        patched = patch_catalog(ws, config, arm)
        plan = await planner.build(text, ws, ctx)
    except Exception as exc:
        return {"arm": arm, "text": text, "i": i, "error": type(exc).__name__}
    guide = next((d for d in stores[arm].load() if d.name == config.get("guide_name")), None)
    return {"arm": arm, "text": text, "i": i, "s": round(time.monotonic() - start, 1),
            "guide_sha": hashlib.sha1(guide.knowledge.encode()).hexdigest()[:8] if guide else "",
            "patched": patched,
            "intents": [s.intent for s in plan.steps], "complexity": plan.complexity,
            "clarify": bool(plan.clarify), "goal": str(plan.goal or "")[:60],
            "plan_mode": plan.plan_mode, "retry": list(getattr(plan, "retry_policies", None) or []),
            "skills": list(getattr(plan, "skills", None) or []), "addressed": plan.addressed,
            "tech_fail": bool(getattr(plan, "technical_failure", False))}


async def main(config: dict) -> None:
    sys.path[:0] = ["/app", "/app/gen/python"]
    from orchestrator.cloud import skills as skills_mod
    from orchestrator.cloud.clients import Clients, set_llm_pin
    from orchestrator.cloud.context import ContextManager
    from orchestrator.cloud.models import PlanContext
    from orchestrator.cloud.planning import PlanBuilder

    clients = Clients()
    planner = PlanBuilder(llm_fn=clients.llm_complete, registry_fn=clients.resolve,
                          llm_tool_fn=clients.llm_complete_tools, llm_mock_fn=clients.llm_served_by_mock)
    cm = ContextManager(clients, session=None)
    stores = skill_stores(skills_mod, config)
    for i in range(int(config["n"])):
        for text in config["texts"]:
            for arm in ("A", "B"):
                row = await plan_once(planner, cm, skills_mod, stores, config, arm, text, i, set_llm_pin, PlanContext)
                print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main(decode_config(sys.argv[1])))
