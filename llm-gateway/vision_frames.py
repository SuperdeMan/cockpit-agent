"""视觉单帧缓存（M4 P4）：HMI 抓的一帧图像在这里活最多两分钟，然后消失。

**图像永远不进对话链**（RFC §5.1）：`HandleRequest.meta` 是 map<string,string>，塞几百 KB
base64 会撑爆 gRPC meta，还会整条进 obs 采集（`OBS_CONTENT_CAPTURE=on` 时）——那是隐私事故，
不是性能问题。所以 proto 里流动的只有一个 16 字节的 frame_id，图像本体只在本进程内存里。

**不落 Redis、不落盘**：Redis 会持久化到磁盘，等于把车内外图像写进了存储。
进程重启即全灭，这是特性不是缺陷。

容量与 TTL 都刻意很小：单帧问答的语义是「问当下」，两分钟前的画面已经不是同一个东西了。
"""
from __future__ import annotations
import base64
import os
import time
import uuid
from collections import OrderedDict

DEFAULT_TTL_S = 120
DEFAULT_MAX_FRAMES = 16
# 单帧上限：HMI 侧已按 1280px/q0.8 压过，正常在 100-300KB。超限直接拒绝而不是静默截断。
DEFAULT_MAX_BYTES = 4 * 1024 * 1024


def ttl_s() -> int:
    return int(os.getenv("VISION_FRAME_TTL_S", str(DEFAULT_TTL_S)))


def max_frames() -> int:
    return int(os.getenv("VISION_FRAME_MAX", str(DEFAULT_MAX_FRAMES)))


def max_bytes() -> int:
    return int(os.getenv("VISION_FRAME_MAX_BYTES", str(DEFAULT_MAX_BYTES)))


class FrameStore:
    """LRU + TTL 的一次性帧库。线程/协程安全性靠 asyncio 单线程模型（同网关其余状态）。"""

    def __init__(self):
        self._frames: OrderedDict[str, tuple[float, str, bytes]] = OrderedDict()

    def put(self, data: bytes, mime: str = "image/jpeg") -> str:
        self._evict()
        fid = "vf_" + uuid.uuid4().hex[:14]
        self._frames[fid] = (time.time(), mime, data)
        while len(self._frames) > max_frames():
            self._frames.popitem(last=False)
        return fid

    def get(self, fid: str) -> tuple[str, bytes] | None:
        """取帧。过期/不存在返回 None——调用方必须**诚实说拿不到**，绝不用纯文本编一个答案。"""
        self._evict()
        hit = self._frames.get(fid)
        if not hit:
            return None
        _, mime, data = hit
        self._frames.move_to_end(fid)
        return mime, data

    def data_url(self, fid: str) -> str | None:
        hit = self.get(fid)
        if not hit:
            return None
        mime, data = hit
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def drop(self, fid: str) -> None:
        self._frames.pop(fid, None)

    def _evict(self) -> None:
        cutoff = time.time() - ttl_s()
        for fid in [k for k, (ts, _, _) in self._frames.items() if ts < cutoff]:
            del self._frames[fid]

    def __len__(self) -> int:
        self._evict()
        return len(self._frames)


_store = FrameStore()


def store() -> FrameStore:
    return _store
