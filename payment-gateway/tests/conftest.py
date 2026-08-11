"""payment-gateway 测试装载。

目录名带连字符不能作包名；又不能学 llm-gateway 裸插 sys.path——两家都有
`providers` 模块名，裸名进 sys.modules 谁先跑谁劫持（「import server 裸名劫持」
老教训）。解法：以 `payment_gateway` 别名把目录注册成正常包，测试全部走
`from payment_gateway import store` 全限定导入，**不占任何裸名**；被测代码的
平坦 import 有包形态兜底（server.py/worker.py 的 try/except 双形态）。
"""
from __future__ import annotations

import importlib.util
import os
import sys

_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ensure_pkg():
    if "payment_gateway" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "payment_gateway", os.path.join(_DIR, "__init__.py"),
        submodule_search_locations=[_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["payment_gateway"] = pkg
    spec.loader.exec_module(pkg)


_ensure_pkg()
