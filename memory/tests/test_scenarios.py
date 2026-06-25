"""分层记忆——复杂场景测试集（确定性，内存兜底；无 PG/无 embedding 走 lexical）。

与 test_pg_store / test_extract / test_routine 的单点用例互补：这里每个用例是一段
**多步叙事**，把抽取→巩固→召回→时序→隐私→过期→合规等多个行为编织在一起，断言
**涌现行为**（系统作为整体表现是否正确），而非单个过滤器。

座舱记忆系统的能力维度（逐一覆盖）：
1. 偏好的学习→召回→改口→再召回（跨多轮演化 + 时序-lite）
2. 多乘员（驾驶员/乘客）同谓词偏好隔离
3. 隐私三档同场：highly_sensitive(家) / sensitive(宠物) / normal(口味)
4. 临时偏好过期 vs 永久偏好
5. 程序记忆：高频行为→routine→主动建议（阈值判别）
6. 抽取治理纵深：一段嘈杂对话一次过滤（LLM 误吐也兜得住）
7. 合规：导出/被遗忘权全链 + 跨用户隔离
8. planner/chitchat 依赖的召回契约（精确查询形状锁定）

纯 Python（store/pg_store/extract/routine），不连 PG/Redis/proto。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402

_NOW = __import__("time").time


def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""              # Redis 内存兜底
    s._vstore._dsn = ""     # 向量存储内存兜底（lexical 召回）
    return s


def _mock(payload) -> "callable":
    """构造注入 extract 的 complete_fn，返回固定 JSON。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    async def fn(messages):
        return text
    return fn


def _sem(user, text, predicate, scope, **kw):
    base = {"user_id": user, "kind": "semantic", "text": text, "predicate": predicate,
            "scope": scope, "occupant_id": kw.pop("occupant", "primary"), "confidence": 1.0}
    base.update(kw)
    return base


# ════════════════════════════════════════════════════════════════════════
# 场景 1：口味偏好的学习 → 召回 → 改口 → 再召回（多轮演化闭环 + 时序-lite）
# ════════════════════════════════════════════════════════════════════════
def test_scenario_preference_evolution_across_turns():
    """泓舟第一天闲聊说『不吃辣』，系统学到；第二天又说『现在能吃辣了』，系统改口。
    断言：①第一次巩固后召回到旧偏好；②冲突后只召回现行新偏好；③审计可追溯旧值。"""
    store = _store()

    async def go():
        # —— 第一天：闲聊里透露不吃辣 ——
        await store.append_turn("s-evo", "user", "我这人不太能吃辣")
        await store.append_turn("s-evo", "assistant", "好的，记住啦")
        learned1 = await store.consolidate("s-evo", "u1", complete_fn=_mock([{
            "category": "explicit_preference", "kind": "semantic", "predicate": "taste.spicy",
            "text": "用户不吃辣", "scope": "profile.taste", "confidence": 0.9}]))
        recall_after_day1 = await store.recall(user_id="u1", query="辣", scopes=["profile.taste"])

        # —— 第二天：口味变了 ——
        await store.append_turn("s-evo", "user", "我最近重口味了，能吃辣")
        learned2 = await store.consolidate("s-evo", "u1", complete_fn=_mock([{
            "category": "explicit_preference", "kind": "semantic", "predicate": "taste.spicy",
            "text": "用户现在能吃辣了", "scope": "profile.taste", "confidence": 0.9}]))

        current = await store.recall(user_id="u1", query="辣", scopes=["profile.taste"])
        audit = await store.recall(user_id="u1", query="辣", scopes=["profile.taste"],
                                   include_superseded=True)
        return learned1, recall_after_day1, learned2, current, audit

    learned1, day1, learned2, current, audit = asyncio.run(go())
    assert len(learned1) == 1 and day1 and day1[0][0]["text"] == "用户不吃辣"      # ① 学到旧值
    assert len(learned2) == 1                                                       # ② 冲突→插新+supersede
    assert len(current) == 1 and current[0][0]["text"] == "用户现在能吃辣了"        #   现行只剩新值
    assert len(audit) == 2                                                          # ③ 旧值仍可审计
    texts = {a[0]["text"] for a in audit}
    assert texts == {"用户不吃辣", "用户现在能吃辣了"}


# ════════════════════════════════════════════════════════════════════════
# 场景 2：多乘员同谓词偏好隔离（驾驶员 vs 乘客，不串味）
# ════════════════════════════════════════════════════════════════════════
def test_scenario_multi_occupant_same_predicate_isolation():
    """驾驶员不吃辣、副驾无辣不欢——同一 taste.spicy 谓词、不同 occupant。
    断言：各自召回互不串味；给乘客点餐取到乘客口味；导出含两条。"""
    store = _store()

    async def go():
        await store.remember([
            _sem("u1", "驾驶员不吃辣", "taste.spicy", "profile.taste", occupant="primary"),
            _sem("u1", "副驾无辣不欢、爱吃辣", "taste.spicy", "profile.taste", occupant="passenger"),
        ])
        driver = await store.recall(user_id="u1", query="辣", occupant_id="primary")
        passenger = await store.recall(user_id="u1", query="辣", occupant_id="passenger")
        exported = await store.export_user("u1")
        return driver, passenger, exported

    driver, passenger, exported = asyncio.run(go())
    assert driver and all(h[0]["occupant_id"] == "primary" for h in driver)
    assert driver[0][0]["text"] == "驾驶员不吃辣"
    assert passenger and all(h[0]["occupant_id"] == "passenger" for h in passenger)
    assert passenger[0][0]["text"] == "副驾无辣不欢、爱吃辣"
    # 两位乘员的同谓词偏好都在，互不覆盖
    spicy = [m for m in exported["memories"] if m["predicate"] == "taste.spicy"]
    assert {m["occupant_id"] for m in spicy} == {"primary", "passenger"}


# ════════════════════════════════════════════════════════════════════════
# 场景 3：隐私三档同场对比（家=highly / 宠物=sensitive / 口味=normal）
# ════════════════════════════════════════════════════════════════════════
def test_scenario_privacy_tiers_in_one_user():
    """同一用户三类记忆并存，验证隐私分级的差异化召回：
    - 家（highly_sensitive）：泛化召回**不带出**（即便文本命中），仅定向可读（导航能用）。
    - 宠物名（sensitive，用户主动告知）：泛化召回**能带出**（『我宠物叫啥』答得上）。
    - 口味（normal）：泛化召回正常带出。"""
    store = _store()

    async def go():
        # 家：经画像写入，镜像为 highly_sensitive memory_item
        await store.upsert_profile("u1", "places", {
            "home": {"name": "阳光小区", "address": "上海长宁", "lat": 31.2, "lng": 121.4}})
        # 宠物名：用户主动告知 → sensitive，可泛化召回
        await store.remember([_sem("u1", "用户的宠物叫旺财", "person.pet", "profile.person",
                                   privacy_level="sensitive", provenance="user_stated")])
        # 口味：普通偏好
        await store.remember([_sem("u1", "用户不吃辣偏清淡", "taste.spicy", "profile.taste")])

        # 泛化召回（无 scope/predicate）。用"阳光小区"——它确实命中家的镜像文本，
        # 因此若家未出现，证明是**隐私护栏**挡掉、而非没匹配上。
        recall_home = await store.recall(user_id="u1", query="阳光小区")
        recall_pet = await store.recall(user_id="u1", query="宠物")       # 应带出
        recall_taste = await store.recall(user_id="u1", query="清淡")     # 应带出
        # 定向读取：导航/记忆页按谓词前缀取回家（列举模式，绕过泛化隐私排除）
        places = await (await store._vec()).get_places("u1")
        targeted_home = await store.recall(user_id="u1", query="阳光小区", predicate_prefix="place.")
        return recall_home, recall_pet, recall_taste, places, targeted_home

    home, pet, taste, places, targeted = asyncio.run(go())
    # 家：泛化召回不带出（highly_sensitive 隐私护栏），即便 lexical 命中其文本
    assert all(h[0]["predicate"] != "place.home" for h in home)
    # 宠物：泛化召回带出（sensitive 可被泛化，"我宠物叫啥"答得上）
    assert any(h[0]["predicate"] == "person.pet" for h in pet)
    # 口味：泛化召回带出
    assert any(h[0]["predicate"] == "taste.spicy" for h in taste)
    # 家：定向可读（导航 / 记忆页）
    assert places.get("home", {}).get("name") == "阳光小区"
    assert any(h[0]["predicate"] == "place.home" for h in targeted)


# ════════════════════════════════════════════════════════════════════════
# 场景 4：临时偏好过期 vs 永久偏好
# ════════════════════════════════════════════════════════════════════════
def test_scenario_temporary_pref_expires_permanent_persists():
    """『今天别走高速』是临时偏好（带 expires_at），过期后不该再影响导航；
    『不吃辣』是永久偏好，一直有效。
    断言：过期临时偏好不召回；未过期临时偏好召回；永久偏好恒召回。"""
    store = _store()
    now = int(_NOW())

    async def go():
        await store.remember([
            # 已过期的临时偏好（模拟昨天设的"今天别走高速"，今天已失效）
            _sem("u1", "今天别走高速", "route.today_no_highway", "profile.route",
                 expires_at=now - 10),
            # 仍有效的临时偏好（一小时后才过期）
            _sem("u1", "这趟少开空调", "hvac.eco_now", "profile.comfort",
                 expires_at=now + 3600),
            # 永久偏好
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
        ])
        routes = await store.recall(user_id="u1", query="", scopes=["profile.route"])
        comfort = await store.recall(user_id="u1", query="", scopes=["profile.comfort"])
        taste = await store.recall(user_id="u1", query="", scopes=["profile.taste"])
        return routes, comfort, taste

    routes, comfort, taste = asyncio.run(go())
    assert routes == []                                            # 过期临时偏好不召回
    assert len(comfort) == 1 and comfort[0][0]["predicate"] == "hvac.eco_now"  # 未过期临时偏好召回
    assert len(taste) == 1 and taste[0][0]["predicate"] == "taste.spicy"        # 永久偏好恒召回


# ════════════════════════════════════════════════════════════════════════
# 场景 5：程序记忆——高频行为沉淀 routine + 主动建议（阈值判别）
# ════════════════════════════════════════════════════════════════════════
def test_scenario_routine_emerges_above_threshold_only():
    """用户三个工作日早晨都在公司星巴克买咖啡（达阈值→沉淀 routine+建议），
    晚上只去过两次健身房（未达阈值→不沉淀）。
    断言：只产出咖啡 routine；建议非空；二次 derive 去重；procedural 仅 1 条。"""
    store = _store()

    async def go():
        for _ in range(3):  # 早晨咖啡 ×3（达阈值）
            await store.remember([{
                "user_id": "u1", "kind": "episodic", "text": "早晨在公司星巴克买咖啡",
                "scope": "episodic.general",
                "value_json": json.dumps({"action": "买咖啡", "place": "公司星巴克", "hour": 8},
                                         ensure_ascii=False)}])
        for _ in range(2):  # 晚上健身 ×2（不足阈值）
            await store.remember([{
                "user_id": "u1", "kind": "episodic", "text": "晚上去健身房",
                "scope": "episodic.general",
                "value_json": json.dumps({"action": "健身", "place": "健身房", "hour": 20},
                                         ensure_ascii=False)}])
        first = await store.derive_routines("u1", min_count=3)
        second = await store.derive_routines("u1", min_count=3)  # 已沉淀→去重
        exported = await store.export_user("u1")
        return first, second, exported

    first, second, exported = asyncio.run(go())
    assert len(first) == 1                                   # 只有咖啡达阈值
    assert "买咖啡" in first[0]["predicate"]
    assert first[0]["suggestion"] and "咖啡" in first[0]["suggestion"]
    assert second == []                                      # 去重，不重复沉淀
    kinds = [m["kind"] for m in exported["memories"]]
    assert kinds.count("procedural") == 1                    # routine 只沉淀一条
    assert kinds.count("episodic") == 5                      # 原始情景不丢


# ════════════════════════════════════════════════════════════════════════
# 场景 6：抽取治理纵深——一段嘈杂对话一次过滤（LLM 误吐也兜得住）
# ════════════════════════════════════════════════════════════════════════
def test_scenario_extraction_defense_in_depth():
    """一段真实对话里混杂：稳定偏好(记)、宠物名(记)、一次性指令(不该被当偏好)、
    精确坐标(丢)、电话号(丢)、健康敏感画像(丢)。即便 LLM 误把坐标/电话/敏感也吐出来，
    治理层兜底丢弃。断言：只有偏好与宠物入库，隐私/敏感全滤。"""
    store = _store()

    async def go():
        await store.append_turn("s-noise", "user",
                                "以后导航都别走高速；我宠物叫旺财；我家在 31.2304,121.4737；"
                                "我电话 13800001111；我有高血压少给我推荐重盐的")
        # 模拟 LLM 抽取（含它本不该吐、但万一吐了的脏数据）
        learned = await store.consolidate("s-noise", "u1", complete_fn=_mock([
            {"category": "explicit_preference", "kind": "semantic", "predicate": "route.avoid_highway",
             "text": "用户导航偏好不走高速", "scope": "profile.route", "confidence": 0.9},
            {"category": "personal_fact", "kind": "semantic", "predicate": "person.pet",
             "text": "用户的宠物叫旺财", "scope": "profile.person", "confidence": 0.9},
            {"category": "explicit_preference", "kind": "semantic", "predicate": "place.home",
             "text": "用户家坐标 31.2304,121.4737", "scope": "profile.places", "confidence": 0.9},
            {"category": "personal_fact", "kind": "semantic", "predicate": "person.phone",
             "text": "用户电话 13800001111", "scope": "profile.person", "confidence": 0.9},
            {"category": "sensitive_fact", "kind": "semantic", "predicate": "health.condition",
             "text": "用户有高血压", "scope": "profile.health", "confidence": 0.95},
        ]))
        exported = await store.export_user("u1")
        return learned, exported

    learned, exported = asyncio.run(go())
    preds = {m["predicate"] for m in exported["memories"]}
    assert "route.avoid_highway" in preds   # 稳定偏好：记
    assert "person.pet" in preds            # 用户主动告知的宠物名：记
    assert "place.home" not in preds        # 含精确坐标 → 黑名单丢弃
    assert "person.phone" not in preds      # 电话号(PII) → 丢弃
    assert "health.condition" not in preds  # 健康敏感画像 → 丢弃
    assert len(exported["memories"]) == 2   # 只剩两条干净记忆


# ════════════════════════════════════════════════════════════════════════
# 场景 7：合规——导出/被遗忘权全链 + 跨用户隔离
# ════════════════════════════════════════════════════════════════════════
def test_scenario_gdpr_export_then_forget_isolated():
    """用户攒了偏好+地点+宠物+情景多类记忆。导出应全可见（profile+memories）；
    行使被遗忘权后全清（含 places 镜像）；另一个用户的记忆毫发无损。"""
    store = _store()

    async def go():
        # u1 多类记忆
        await store.upsert_profile("u1", "places", {"home": {"name": "阳光小区", "lat": 31.2, "lng": 121.4}})
        await store.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            _sem("u1", "用户的宠物叫旺财", "person.pet", "profile.person", privacy_level="sensitive"),
            {"user_id": "u1", "kind": "episodic", "text": "在西湖边散步", "scope": "episodic.general"},
        ])
        # 另一个用户，验证隔离
        await store.remember([_sem("u2", "用户喜欢摇滚", "music.genre", "profile.music")])

        exported = await store.export_user("u1")
        deleted = await store.forget_user("u1")  # 行使被遗忘权（全量硬删）
        after_recall = await store.recall(user_id="u1", query="辣")
        after_places = await (await store._vec()).get_places("u1")
        u2_intact = await store.recall(user_id="u2", query="摇滚")
        return exported, deleted, after_recall, after_places, u2_intact

    exported, deleted, after_recall, after_places, u2_intact = asyncio.run(go())
    # 导出全可见
    assert exported["profile"]["places"]["home"]["name"] == "阳光小区"
    ex_preds = {m["predicate"] for m in exported["memories"]}
    assert {"taste.spicy", "person.pet", "place.home"} <= ex_preds  # 含 places 镜像
    # 被遗忘权：全清
    assert deleted >= 3
    assert after_recall == []
    assert after_places == {}            # places 镜像一并清掉
    # 跨用户隔离：u2 毫发无损
    assert u2_intact and u2_intact[0][0]["predicate"] == "music.genre"


# ════════════════════════════════════════════════════════════════════════
# 场景 8：召回契约——锁定 planner / chitchat 依赖的精确查询形状
# ════════════════════════════════════════════════════════════════════════
def test_scenario_recall_contract_for_planner_injection():
    """engine._recall 用 kinds=['semantic'],top_k=3,min_confidence=0.5 取偏好注入规划。
    本用例锁定该**过滤契约**：高置信现行语义偏好被取回；低置信被阈值挡掉；情景不混入
    （kinds）；被取代的旧值不出现（时序）；top_k 截断。任何破坏这一形状的改动都会让规划
    丢失偏好或被污染。（真实语义相关性由 test/e2e_memory.py 用真 embedding 覆盖。）"""
    store = _store()

    async def go():
        await store.remember([
            _sem("u1", "用户明确不吃辣", "taste.spicy", "profile.taste", confidence=0.9),
            _sem("u1", "用户大概喜欢吃辣？", "taste.guess", "profile.taste", confidence=0.3),  # 低置信
            {"user_id": "u1", "kind": "episodic", "text": "上次吃了辣的火锅",
             "scope": "episodic.general", "confidence": 0.9},  # 情景，不该混入
        ])
        # 一条被取代的旧偏好（时序-lite）
        old = await store.remember([_sem("u1", "用户以前爱吃辣", "taste.history",
                                         "profile.taste", confidence=0.9)])
        new = await store.remember([_sem("u1", "用户口味变清淡", "taste.history",
                                         "profile.taste", confidence=0.9)])
        await (await store._vec()).supersede(old[0], new[0])

        # —— 复刻 engine._recall 的过滤契约（query="" 列举模式，隔离相关性、专测过滤）——
        mems = await store.recall(user_id="u1", query="", kinds=["semantic"],
                                  top_k=3, min_confidence=0.5)
        return mems

    mems = asyncio.run(go())
    preds = [m[0]["predicate"] for m in mems]
    assert "taste.spicy" in preds          # 高置信现行语义偏好：取回
    assert "taste.guess" not in preds      # 低置信(<0.5)：阈值挡掉
    assert all(m[0]["kind"] == "semantic" for m in mems)  # 情景不混入
    assert len(mems) <= 3                                  # top_k 截断
    assert all(m[1] > 0 for m in mems)                     # 每条都有正分
    # 被取代的旧值不出现，只取现行的 taste.history
    history = [m for m in mems if m[0]["predicate"] == "taste.history"]
    assert len(history) == 1 and history[0][0]["text"] == "用户口味变清淡"
