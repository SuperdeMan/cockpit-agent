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
