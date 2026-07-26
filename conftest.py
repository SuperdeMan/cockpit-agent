"""根 conftest.py：统一 PYTHONPATH，解决测试收集问题。

F7 修复：项目根 + gen/python 进 sys.path，让 `python -m pytest` 一条命令全量通过，
不再需要手工 PYTHONPATH。
"""
import sys
import os

_root = os.path.dirname(__file__)
_gen_py = os.path.join(_root, "gen", "python")

if _root not in sys.path:
    sys.path.insert(0, _root)
if _gen_py not in sys.path and os.path.isdir(_gen_py):
    sys.path.insert(0, _gen_py)

# 单测钉死 skill 检索词法档：语义通道要打 llm-gateway Embed（网络），单测必须离线
# 确定；语义通道自身的行为用假 embed 在 test_skills.py 单独测（monkeypatch 可覆盖此值）。
os.environ["SKILLS_RETRIEVAL"] = "lexical"


def pytest_configure(config):
    """注册自定义 marker，避免 PytestUnknownMarkWarning。"""
    config.addinivalue_line(
        "markers",
        "nightly: 真实 LLM 全栈语料，默认 skip（需宿主 LLM_API_KEY + make up），"
        "不进普通 PR 门禁",
    )
