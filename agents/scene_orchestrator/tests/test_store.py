"""SceneStore 单测（内存后端跑全部逻辑；PG 分支结构同 reminder，靠 e2e 覆盖）。"""
import asyncio
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.scene_orchestrator.src.store import ENABLED, DISABLED, Scene, SceneStore

_SCHEMA = os.path.join(_ROOT, "agents", "scene_orchestrator", "schema.sql")


def _store() -> SceneStore:
    return SceneStore(dsn="")          # 空 DSN → 内存后端


def _run(coro):
    return asyncio.run(coro)


def test_memory_fallback_warns(caplog):
    s = _store()
    with caplog.at_level("WARNING"):
        assert _run(s.init()) is False
    assert "内存态兜底" in caplog.text


def test_save_and_get():
    st = _store()
    _run(st.init())
    s = _run(st.save(Scene(user_id="u1", name="钓鱼模式",
                           actions=[{"type": "vehicle.control", "command": "fragrance.on"}])))
    assert s.id.startswith("usr-") and s.created_at > 0 and s.updated_at > 0
    got = _run(st.get("u1", s.id))
    assert got and got.name == "钓鱼模式" and len(got.actions) == 1
    assert _run(st.get("u2", s.id)) is None          # per-user 隔离


def test_same_name_overwrites_not_duplicates():
    """(user_id, name) 唯一——「再建一次钓鱼模式」是覆盖，不是留两条同名。"""
    st = _store()
    _run(st.init())
    a = _run(st.save(Scene(user_id="u1", name="钓鱼模式", actions=[{"command": "x"}])))
    b = _run(st.save(Scene(user_id="u1", name="钓鱼模式",
                           actions=[{"command": "y"}, {"command": "z"}])))
    assert a.id == b.id
    assert len(_run(st.list("u1"))) == 1
    assert len(_run(st.get("u1", a.id)).actions) == 2


def test_list_only_enabled_and_per_user():
    st = _store()
    _run(st.init())
    _run(st.save(Scene(user_id="u1", name="A")))
    d = _run(st.save(Scene(user_id="u1", name="B")))
    _run(st.save(Scene(user_id="u2", name="C")))
    _run(st.set_status("u1", d.id, DISABLED))
    names = [s.name for s in _run(st.list("u1"))]
    assert names == ["A"]
    assert [s.name for s in _run(st.list("u1", statuses=(ENABLED, DISABLED)))] == ["A", "B"]
    assert [s.name for s in _run(st.list("u2"))] == ["C"]


def test_list_sorted_by_use_count():
    st = _store()
    _run(st.init())
    _run(st.save(Scene(user_id="u1", name="少用")))
    b = _run(st.save(Scene(user_id="u1", name="常用")))
    _run(st.bump_use("u1", b.id))
    _run(st.bump_use("u1", b.id))
    assert [s.name for s in _run(st.list("u1"))] == ["常用", "少用"]
    assert _run(st.get("u1", b.id)).use_count == 2


def test_use_count_survives_overwrite():
    """改场景内容不该把「用过 5 次」清零。"""
    st = _store()
    _run(st.init())
    s = _run(st.save(Scene(user_id="u1", name="钓鱼模式")))
    _run(st.bump_use("u1", s.id))
    s2 = _run(st.save(Scene(user_id="u1", name="钓鱼模式", actions=[{"command": "a"}])))
    assert s2.use_count == 1 and s2.id == s.id


def test_delete():
    st = _store()
    _run(st.init())
    s = _run(st.save(Scene(user_id="u1", name="钓鱼模式")))
    assert _run(st.delete("u2", s.id)) is False       # 别人的删不掉
    assert _run(st.delete("u1", s.id)) is True
    assert _run(st.get("u1", s.id)) is None


def test_get_by_name():
    st = _store()
    _run(st.init())
    _run(st.save(Scene(user_id="u1", name="钓鱼模式")))
    assert _run(st.get_by_name("u1", "钓鱼模式")).name == "钓鱼模式"
    assert _run(st.get_by_name("u1", "露营模式")) is None


def test_dsl_fields_match_pg_columns():
    """v2.1 修正①：DSL 顶层键 = Scene 字段 = PG 列，一一同名，不做改名翻译。"""
    with open(_SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    body = sql.split("CREATE TABLE IF NOT EXISTS scene_item (")[1].split(");")[0]
    cols = {m.group(1) for m in re.finditer(r"^\s{2}(\w+)\s", body, re.M)}
    fields = set(Scene(user_id="u", name="n").to_dict())
    assert cols == fields, f"列与字段不一致：只在 PG={cols - fields}，只在 DSL={fields - cols}"
