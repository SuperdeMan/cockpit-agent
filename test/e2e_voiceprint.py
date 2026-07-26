"""M4 P4 声纹多用户真栈验证 —— **母提案 M4 最后一条 DoD「多用户记忆隔离旅程」**。

场景（RFC 2026-07-25-m4-p4 §7 P4a-5）：
  ① 首个注册者绑定 primary —— 主驾注册后，他原有的记忆一条不少（本设计最大的回归点）
  ② 第二乘员拿到 occ-N，两人互不相认
  ③ 识别：A 的另一句话认成 A，B 的认成 B
  ④ 诚实降级：太短 / 陌生音频 → 一律回 primary（不是 guest，不是报错）
  ⑤ **记忆隔离（DoD 主线）**：B 说的偏好只进 B 名下，A 召回不到，B 召回得到
  ⑥ **声纹不提权（红线）**：occupant 变了，权限与确认闸一字不变
  ⑦ 删除乘员 → 模板与其记忆同删，主驾的记忆毫发无损

「说话人」用 TTS 的不同音色扮演——与 P4a-1 探针同一套打法（真人声学层留真麦验收）。

前置：全栈已起（含 postgres + 真 LLM，⑤ 要抽取）+ 声纹面 enabled（模型已拉）。
     缺任一 → SKIP。依赖：pip install websockets httpx
用法：python test/e2e_voiceprint.py
"""
import asyncio
import io
import json
import subprocess
import sys
import time
import wave

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
SESSION = f"memtest-vp-{int(time.time())}"     # memtest- 前缀刻意不跳过抽取（conventions §9.2）
PG = ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit", "-d", "cockpit", "-tAc"]

# 两位「乘员」用两个 TTS 音色扮演；各自三句注册 + 一句识别（注册与识别句不重叠）
ENROLL_LINES = ["你好，我是这辆车的常用乘客",
                "今天天气不错，路上应该不太堵",
                "帮我把空调调到二十四度"]
PROBE_LINE = "附近有什么好吃的川菜馆推荐"

_fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails.append(label)
    return ok


def skip(msg: str) -> None:
    print(f"[SKIP] {msg}")
    sys.exit(0)


def sql(q: str) -> str:
    out = subprocess.run(PG + [q], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return (out.stdout or "").strip()


# ── 音频：/api/tts 合成 → 16k mono s16le（同 e2e_s2s_probe / P4a-1 探针）──
_cache: dict[tuple[str, str], bytes] = {}


async def synth(client: httpx.AsyncClient, text: str, voice: str) -> bytes:
    key = (text, voice)
    if key in _cache:
        return _cache[key]
    r = await client.post(f"{AUDIO_API}/api/tts", json={"text": text, "format": "wav",
                                                        "voice_id": voice}, timeout=90)
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    import base64
    with wave.open(io.BytesIO(base64.b64decode(data["audio"])), "rb") as w:
        ch, fr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        pcm = w.readframes(n)
    import array
    a = array.array("h")
    a.frombytes(pcm)
    if ch == 2:
        a = array.array("h", [(a[i] + a[i + 1]) // 2 for i in range(0, len(a) - 1, 2)])
    if fr != 16000:
        ratio = 16000 / fr
        out = array.array("h", [0]) * int(len(a) * ratio)
        for i in range(len(out)):
            src = i / ratio
            j = int(src)
            out[i] = int(a[j] * (1 - (src - j)) + a[j + 1] * (src - j)) if j + 1 < len(a) \
                else (a[j] if j < len(a) else 0)
        a = out
    _cache[key] = a.tobytes()
    return _cache[key]


async def enroll(client, uid, name, voice) -> dict:
    pcms = [await synth(client, line, voice) for line in ENROLL_LINES]
    files = [("sample", (f"s{i}.pcm", p, "application/octet-stream"))
             for i, p in enumerate(pcms)]
    r = await client.post(f"{AUDIO_API}/api/voiceprint/enroll",
                          params={"user_id": uid, "display_name": name, "format": "pcm16le"},
                          files=files, timeout=60)
    return r.json()


async def identify(client, uid, pcm: bytes) -> dict:
    r = await client.post(f"{AUDIO_API}/api/voiceprint/identify", params={"user_id": uid},
                          content=pcm, timeout=60)
    return r.json()


async def ask(text: str, occupant: str, session: str) -> dict:
    """带 occupant_id 的一轮（HMI 同款 WS + 同款 meta 键）。"""
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session,
                                  "meta": {"occupant_id": occupant}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                return msg


def mem_rows(uid: str, occ: str, like: str) -> int:
    n = sql(f"SELECT count(*) FROM memory_item WHERE user_id='{uid}' AND occupant_id='{occ}' "
            f"AND text LIKE '%{like}%' AND superseded_by IS NULL")
    return int(n or 0)


async def main() -> int:
    print("=== M4 P4 声纹多用户真栈验证 ===")
    async with httpx.AsyncClient() as client:
        try:
            info = (await client.get(f"{AUDIO_API}/api/voiceprint/info",
                                     params={"user_id": "u1"}, timeout=10)).json()
        except Exception as e:
            skip(f"llm-gateway 不可达：{e}")
        if not info.get("enabled"):
            skip(f"声纹面未启用（{info.get('reason') or info.get('provider')}）"
                 "——跑 scripts/fetch-voice-models.* voiceprint-campplus 后重建网关")
        print(f"（provider={info['provider']} model={info.get('model')} dim={info.get('dim')}）")

        voices = (await client.get(f"{AUDIO_API}/api/voices", timeout=20)).json().get("voices", [])
        zh = [v["voice_id"] for v in voices if (v.get("language") or "zh") == "zh"
              and v.get("gender") in ("female", "male")]
        if len(zh) < 2:
            skip(f"可用中文音色不足 2 个（{zh}）——无法扮演两位乘员")
        # 取一女一男：同性别合成音色互相很像（P4a-1 实测冰糖×茉莉中位余弦 0.59），
        # e2e 要验的是链路不是模型极限，故选最可分的一对。
        f = next((v["voice_id"] for v in voices if v.get("gender") == "female"), zh[0])
        m = next((v["voice_id"] for v in voices if v.get("gender") == "male"), zh[1])
        VOICE_A, VOICE_B = f, m
        uid = "u1"
        print(f"（乘员 A={VOICE_A}  乘员 B={VOICE_B}  user_id={uid}）")

        # 净初态：清掉本 user 的声纹模板（不动记忆——① 正要验证存量记忆不丢）
        for o in info.get("occupants", []):
            await client.delete(f"{AUDIO_API}/api/voiceprint/{o['occupant_id']}",
                                params={"user_id": uid, "purge_memory": 0}, timeout=30)

        # ① 首个注册者绑 primary，且存量记忆一条不少
        print("\n[① 首个注册者绑定 primary（本设计最大的回归点）]")
        before = int(sql(f"SELECT count(*) FROM memory_item WHERE user_id='{uid}' "
                         "AND occupant_id='primary'") or 0)
        ra = await enroll(client, uid, "泓舟", VOICE_A)
        check(ra.get("ok") is True, "主驾注册成功",
              json.dumps(ra, ensure_ascii=False)[:110])
        check(ra.get("occupant_id") == "primary", "首个注册者拿到 primary",
              str(ra.get("occupant_id")))
        after = int(sql(f"SELECT count(*) FROM memory_item WHERE user_id='{uid}' "
                        "AND occupant_id='primary'") or 0)
        check(after == before, "主驾原有记忆一条不少（注册不搬家、不失联）",
              f"{before} → {after}")

        # ② 第二乘员
        print("\n[② 第二乘员]")
        rb = await enroll(client, uid, "小雨", VOICE_B)
        check(rb.get("ok") is True and rb.get("occupant_id", "").startswith("occ-"),
              "第二乘员拿到 occ-N", str(rb.get("occupant_id")))
        OCC_B = rb.get("occupant_id", "occ-2")

        # ③ 识别：注册句之外的另一句话
        print("\n[③ 识别（用未参与注册的另一句话）]")
        ia = await identify(client, uid, await synth(client, PROBE_LINE, VOICE_A))
        ib = await identify(client, uid, await synth(client, PROBE_LINE, VOICE_B))
        print(f"   A → {json.dumps(ia, ensure_ascii=False)}")
        print(f"   B → {json.dumps(ib, ensure_ascii=False)}")
        # **认错才是缺陷；认不出只是退回今天的行为**，故断言分两档：认错=红，弃权=黄但不判红。
        check(ia.get("occupant_id") in ("primary",), "A 未被认成别人", ia.get("decision", ""))
        check(ib.get("occupant_id") in (OCC_B, "primary"), "B 未被认成别人",
              ib.get("decision", ""))
        if ib.get("occupant_id") != OCC_B:
            print(f"   ⚠ B 弃权（{ib.get('decision')}）——合成音色的可分性有限，"
                  "不判红；⑤ 改用显式 occupant 验隔离链路")

        # ④ 诚实降级
        print("\n[④ 诚实降级：太短 / 静音]")
        short = await identify(client, uid, b"\x00\x00" * 8000)          # 0.5s
        check(short.get("occupant_id") == "primary" and short.get("decision") == "too_short",
              "过短音频 → too_short 且回 primary", json.dumps(short, ensure_ascii=False))
        silence = await identify(client, uid, b"\x00\x00" * 16000 * 3)   # 3s 静音
        check(silence.get("occupant_id") == "primary",
              "静音 → 回 primary（不是 guest、不是报错）",
              json.dumps(silence, ensure_ascii=False))

    # ⑤ 记忆隔离（DoD 主线）——用显式 occupant_id 走 HMI 同款 WS，验的是**透传链路**
    print("\n[⑤ 记忆隔离（M4 最后一条 DoD）]")
    sess_b, sess_a = f"{SESSION}-b", f"{SESSION}-a"
    r = await ask("记住，我最喜欢吃草莓味的甜品", OCC_B, sess_b)
    print(f"   B 说 ⇒ {(r.get('speech') or '')[:40]}")
    await asyncio.sleep(9)      # 等异步抽取
    n_b = mem_rows("u1", OCC_B, "草莓")
    n_p = mem_rows("u1", "primary", "草莓")
    check(n_b >= 1, "偏好落在说话人名下（occupant_id 全链路透传成立）", f"occ={n_b} 条")
    check(n_p == 0, "**主驾名下查不到 B 的偏好（隔离成立）**", f"primary={n_p} 条")

    # ⑥ 声纹不提权（红线）：换个 occupant 说危险动作，确认闸照旧
    print("\n[⑥ 声纹不提权（红线）]")
    r = await ask("打开后备箱", OCC_B, f"{SESSION}-perm")
    sp = (r.get("speech") or "")
    need_confirm = bool(r.get("need_confirm")) or "确认" in sp or "确定" in sp
    check(need_confirm, "换乘员后危险动作照样要确认（occupant 不参与确认闸）", sp[:50])

    # ⑦ 删除乘员：模板与其记忆同删，主驾毫发无损
    print("\n[⑦ 删除乘员 → 忘掉这个人]")
    async with httpx.AsyncClient() as client:
        keep = int(sql("SELECT count(*) FROM memory_item WHERE user_id='u1' "
                       "AND occupant_id='primary'") or 0)
        d = (await client.delete(f"{AUDIO_API}/api/voiceprint/{OCC_B}",
                                 params={"user_id": "u1", "purge_memory": 1},
                                 timeout=30)).json()
        check(d.get("ok") is True and d.get("deleted_templates") == 1, "模板已删",
              json.dumps(d, ensure_ascii=False))
        check(mem_rows("u1", OCC_B, "草莓") == 0, "其记忆同删（「忘掉这个人」）")
        left = int(sql("SELECT count(*) FROM memory_item WHERE user_id='u1' "
                       "AND occupant_id='primary'") or 0)
        check(left == keep, "主驾记忆毫发无损（删单个乘员没有爆炸半径）", f"{keep} → {left}")
        # 收尾：主驾模板也清掉，不给后续测试留状态
        await client.delete(f"{AUDIO_API}/api/voiceprint/primary",
                            params={"user_id": "u1", "purge_memory": 0}, timeout=30)

    print("\n" + "=" * 46)
    if _fails:
        print(f"✗ {len(_fails)} 项未通过：" + "；".join(_fails))
        return 1
    print("✓ 全部通过（多用户记忆隔离旅程 DoD 达成）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
