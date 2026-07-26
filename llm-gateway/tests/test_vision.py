"""视觉单帧单测（M4 P4）：帧库 TTL/LRU / 多模态注入 / **图像不进对话链的源码铁律**。

最要紧的两条：
1. **拿不到帧 = 显式失败，不是「那就只发文字吧」**——静默降级会让 VL 模型对着空气答
   「看不清，画面有点模糊」，它在假装看到了一张模糊的图（真栈 e2e ⑤ 首跑实测原话）。
2. **帧不落盘不落 Redis**——Redis 会持久化到磁盘，等于把车内外图像写进了存储。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import vision_frames as VF  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for k in ("VISION_FRAME_TTL_S", "VISION_FRAME_MAX", "VISION_FRAME_MAX_BYTES"):
        monkeypatch.delenv(k, raising=False)
    VF._store = VF.FrameStore()
    yield


# ── 帧库 ──────────────────────────────────────────────────────────────────

def test_put_get_round_trip():
    fid = VF.store().put(b"\xff\xd8jpegbytes", "image/jpeg")
    assert fid.startswith("vf_")
    mime, data = VF.store().get(fid)
    assert mime == "image/jpeg" and data == b"\xff\xd8jpegbytes"


def test_missing_frame_returns_none_not_error():
    assert VF.store().get("vf_nonexistent") is None
    assert VF.store().data_url("vf_nonexistent") is None


def test_frames_expire(monkeypatch):
    """单帧问答的语义是「问当下」——两分钟前的画面已经不是同一个东西了。"""
    monkeypatch.setenv("VISION_FRAME_TTL_S", "0")
    fid = VF.store().put(b"x")
    time.sleep(0.01)
    assert VF.store().get(fid) is None


def test_lru_caps_frame_count(monkeypatch):
    monkeypatch.setenv("VISION_FRAME_MAX", "3")
    ids = [VF.store().put(bytes([i])) for i in range(5)]
    assert len(VF.store()) == 3
    assert VF.store().get(ids[0]) is None      # 最旧的被挤掉
    assert VF.store().get(ids[-1]) is not None


def test_data_url_is_base64_data_uri():
    fid = VF.store().put(b"abc", "image/png")
    url = VF.store().data_url(fid)
    assert url.startswith("data:image/png;base64,")


def test_drop_removes_frame():
    fid = VF.store().put(b"x")
    VF.store().drop(fid)
    assert VF.store().get(fid) is None


# ── 多模态注入（server._msgs）────────────────────────────────────────────

class _Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


class _Req:
    def __init__(self, messages, meta=None):
        self.messages = messages
        self.meta = meta or {}


def _gateway_server_mod():
    """按**文件路径**显式加载 llm-gateway/server.py。

    不能写 `from server import ...`：`server` 是全仓最通用的模块名之一（memory / 编排 /
    _sdk 都有），全量跑单测时 `sys.modules['server']` 早被别人占了——单独跑绿、全量跑红。
    本仓库已经在 providers 通用包名上栽过一次，这是同一族。
    """
    import importlib.util
    import sys as _sys
    if "llm_gateway_server" in _sys.modules:
        return _sys.modules["llm_gateway_server"]
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
    spec = importlib.util.spec_from_file_location("llm_gateway_server", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["llm_gateway_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def _msgs(req):
    return _gateway_server_mod().LLMGatewayServicer._msgs(req)


def test_no_frame_id_keeps_plain_text():
    out = _msgs(_Req([_Msg("user", "那是什么")]))
    assert out == [{"role": "user", "content": "那是什么"}]


def test_frame_id_upgrades_last_user_message():
    fid = VF.store().put(b"\xff\xd8", "image/jpeg")
    out = _msgs(_Req([_Msg("system", "sys"), _Msg("user", "那是什么")],
                     {"vision_frame_id": fid}))
    assert out[0]["content"] == "sys", "system 消息不该被动"
    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "那是什么"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_expired_frame_raises_instead_of_silent_text_fallback():
    """**关键**：声明了要看图却拿不到图 = 显式失败，不是「那就只发文字吧」。

    真栈 e2e ⑤ 首跑实测：静默只发文本时 VL 模型答「看不清，画面有点模糊」——
    它在假装看到了一张模糊的图。那比说不出更糟：用户没法判断真假。
    """
    with pytest.raises(_gateway_server_mod().FrameUnavailable):
        _msgs(_Req([_Msg("user", "那是什么")], {"vision_frame_id": "vf_gone"}))


def test_only_last_user_message_gets_the_image():
    fid = VF.store().put(b"x")
    out = _msgs(_Req([_Msg("user", "上一句"), _Msg("assistant", "答"), _Msg("user", "那是什么")],
                     {"vision_frame_id": fid}))
    assert isinstance(out[0]["content"], str), "历史轮不该被塞图"
    assert isinstance(out[2]["content"], list)


# ── 源码铁律 ──────────────────────────────────────────────────────────────

def _src(name: str) -> str:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           name), encoding="utf-8") as f:
        return f.read()


def _code_only(src: str) -> str:
    """剥掉注释与文档字符串——源码级断言要查的是**用法**，不是行文里提到的词。
    （首版就栽在这：自己注释里写「不落 Redis」被判成用了 Redis。）"""
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""          # 文档字符串置空
    return ast.unparse(tree)


def test_frames_never_persisted():
    """帧不落盘、不落 Redis——Redis 会持久化到磁盘，等于把车内外图像写进存储。"""
    code = _code_only(_src("vision_frames.py"))
    for lit in ("redis", "Redis", "open(", "sqlite", "POSTGRES", "asyncpg"):
        assert lit not in code, f"vision_frames.py 实际用到了 {lit!r}——帧只该在进程内存里"


def test_vision_endpoint_never_logs_image_bytes():
    """obs/日志只记 id 与尺寸，不记图。"""
    src = _src("http_server.py")
    start = src.index('@routes.post("/api/vision/frame")')
    end = src.index('@routes.get("/api/vision/info")')
    block = src[start:end]
    assert "b64encode" not in block and "data:image" not in block
    assert "len(data)" in block, "应只记字节数"


def test_vision_uses_dedicated_vl_tier():
    """看图必须走 qwen-vl 独立档：聊天大脑（qwen3.7-max）对多模态 content 直接 400
    （P4b 探针实测），而档位解析对不认识的模型是**静默回落 primary**——不独立成档，
    一次瞬时失败就会把看图请求打到看不了图的模型上，且不会有任何报错。"""
    src = _src("llm_runtime.py")
    assert '"qwen-vl"' in src
    assert '"internal": True' in src, "视觉档不该出现在 HMI「AI 大脑」切换列表里"
