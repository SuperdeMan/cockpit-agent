"""M4 P4 视觉入口真栈验证：「那是什么」单帧图片问答（子 RFC §5）。

场景：
  ① 帧上传 → 拿到短 TTL frame_id
  ② **真图进真模型出真答案**：一张纯色图 → 模型说得出颜色（不是编的）
  ③ 路由：「那是什么」经 route_hints 落 vision.describe（不被 chitchat 兜走）
  ④ **没帧就说没帧**：不带 frame_id 问同一句 → 诚实降级，绝不编一个答案
  ⑤ 帧过期 → 同样诚实降级（不静默退化成文本问答）
  ⑥ **图像不进对话链**：obs 里查不到图像字节，只有 frame_id

前置：全栈已起（含 vision-agent）+ DashScope key（视觉走 qwen-vl 档）。缺任一 → SKIP。
依赖：pip install websockets httpx
用法：python test/e2e_vision.py
"""
import asyncio
import json
import struct
import sys
import time
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import httpx
    import websockets
except ImportError:
    print("请先：pip install websockets httpx")
    sys.exit(1)

WS = "ws://localhost:8090/ws"
AUDIO_API = "http://localhost:50059"
TIMEOUT = 90
SESSION = f"e2e-vision-{int(time.time())}"

_fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails.append(label)
    return ok


def skip(msg: str) -> None:
    print(f"[SKIP] {msg}")
    sys.exit(0)


def solid_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """无依赖生成纯色 PNG——测「模型真的看见了」而不是「模型会说漂亮话」，
    纯色图的正确答案唯一且可断言，比拿一张风景照让它描述强得多。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def ask(text: str, meta: dict, session: str = SESSION) -> dict:
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session, "meta": meta}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                return msg


async def main() -> int:
    print("=== M4 P4 视觉入口真栈验证 ===")
    async with httpx.AsyncClient() as client:
        try:
            info = (await client.get(f"{AUDIO_API}/api/vision/info", timeout=10)).json()
        except Exception as e:
            skip(f"llm-gateway 不可达：{e}")
        if not info.get("enabled"):
            skip(f"视觉档不可用（{info.get('reason')}）——需 DashScope key")
        print(f"（provider={info['provider']} model={info['model']} ttl={info['ttl_s']}s）")

        # ① 帧上传
        print("\n[① 帧上传]")
        png = solid_png(96, 96, (30, 160, 60))          # 纯绿
        r = (await client.post(f"{AUDIO_API}/api/vision/frame", params={"mime": "image/png"},
                               content=png, timeout=30)).json()
        fid = r.get("frame_id", "")
        check(fid.startswith("vf_"), "拿到 frame_id", json.dumps(r, ensure_ascii=False))
        check(r.get("bytes") == len(png), "字节数一致", str(r.get("bytes")))

        # ② 真图进真模型
        print("\n[② 真图 → 真模型 → 真答案（纯绿图，答案唯一可断言）]")
        res = await ask("这是什么颜色", {"vision_frame_id": fid})
        res_with_image = res     # 留给 ⑥：泄漏探针必须查**带图**的这轮（验收修正）
        speech = res.get("speech") or ""
        print(f"   ⇒ {speech[:80]}")
        check("绿" in speech, "模型真的看见了这张图（答出绿色）", speech[:40])

        # ③ 路由
        print("\n[③ 路由：「那是什么」落 vision.describe]")
        png2 = solid_png(96, 96, (230, 200, 20))        # 纯黄
        fid2 = (await client.post(f"{AUDIO_API}/api/vision/frame", params={"mime": "image/png"},
                                  content=png2, timeout=30)).json()["frame_id"]
        res = await ask("那是什么颜色的", {"vision_frame_id": fid2}, f"{SESSION}-r")
        speech = res.get("speech") or ""
        print(f"   ⇒ {speech[:80]}")
        check("黄" in speech, "route_hints 把视觉句路由到了 vision（不是 chitchat 兜走）",
              speech[:40])

        # ④ 没帧就说没帧
        print("\n[④ 没帧 → 诚实降级（绝不编一个答案）]")
        res = await ask("那是什么", {}, f"{SESSION}-nf")
        speech = res.get("speech") or ""
        print(f"   ⇒ {speech[:80]}")
        check("没拿到画面" in speech or "看不" in speech,
              "**没帧时诚实说没画面**（编答案比查不到严重得多）", speech[:50])

        # ⑤ 帧过期
        print("\n[⑤ 帧已过期/不存在 → 同样诚实降级]")
        res = await ask("那是什么", {"vision_frame_id": "vf_expired000000"}, f"{SESSION}-ex")
        speech = res.get("speech") or ""
        print(f"   ⇒ {speech[:80]}")
        # 首跑发现的诚实度缺口：网关静默只发文本时，VL 模型会答「看不清，画面有点模糊」
        # ——**它在假装看到了一张模糊的图**。改为网关显式 FAILED_PRECONDITION、
        # Agent 明说「那一眼已经过去了」，并与「模型挂了」区分（前者再问就好）。
        check("过去了" in speech or "过期" in speech, "帧过期时明说过期（不假装看到）",
              speech[:50])
        made_up = any(w in speech for w in ("写字楼", "大楼", "一棵", "一辆", "看起来像", "模糊"))
        check(not made_up, "不对不存在的画面编造任何描述", speech[:50])

        # ⑥ 图像不进对话链
        print("\n[⑥ 图像不进对话链]")
        # 验收修正：此前探针跑在 ⑤（帧过期）那轮的 res 上——那轮**根本没有图**，
        # 「不含图像字节」平凡成立。要查就查唯一可能泄漏的对象：②那轮真的把图
        # 交给了 VL 模型，它的响应体才是「图像会不会流回对话链」的判据。
        for probe in ("data:image", "base64,"):
            leaked = probe in json.dumps(res_with_image, ensure_ascii=False)
            check(not leaked, f"带图轮次的响应体不含 {probe}")
        # docstring 声称「obs 里只有 frame_id 无图像字节」——补上真查（此前从未查过）。
        try:
            obs = (await client.get("http://localhost:8092/api/turns",
                                    params={"session": SESSION, "limit": 5},
                                    timeout=5)).json()
            blob = json.dumps(obs, ensure_ascii=False)
            check("data:image" not in blob and "iVBORw0KGgo" not in blob,
                  "obs 轮次记录不含图像字节（只有 frame_id）")
        except Exception as e:
            print(f"   （obs 校验跳过：collector 不可达 {e}）")

    print("\n" + "=" * 46)
    if _fails:
        print(f"✗ {len(_fails)} 项未通过：" + "；".join(_fails))
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
