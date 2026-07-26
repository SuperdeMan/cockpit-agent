"""M4 P4 声纹探针：CAM++ 在中文语音上的真实可分性（RFC 2026-07-25-m4-p4 §7）。

**这份脚本的产出是三个数**——`VOICEPRINT_THRESHOLD` / `VOICEPRINT_MARGIN` /
`VOICEPRINT_MIN_SPEECH_MS`。在跑它之前那三个数只是经验值，跑完才是实测值。
仓库前科三次（ASR 双协议、qwen finish_reason、S2S 静默丢 tools）都是「文档说得好好的、
实测才发现不是那么回事」，声纹更甚：**特征算错时模型照样吐 192 维向量，同人余弦会悄悄掉到
异人水平，从代码上完全看不出来**——只有分布能暴露它。

探的五件（★=RFC §7 原列，R=管路自检中发现的风险）：
  ★1 模型可得性与加载耗时
  ★2 同人不同句余弦分布（同一 TTS 音色念不同句子）
  ★3 异人余弦分布（不同 TTS 音色）
  ★4 最短有效语音时长（1.0 / 1.5 / 2.0 / 3.0s 截断对比）
  ★5 三段注册的 self_consistency 分布
  R1 分布外音频（静音/白噪）的余弦——管路自检里纯音之间 cos=0.91，**远高于拟定阈值**：
     模型对训练分布外的输入不给低分。若单模板下噪声也能过闸，就得靠 margin/时长兜底。

**诚实边界**：音频源是 TTS 合成音色，不是真人。合成音色之间的可分性通常**优于**真人，
故本报告给出的阈值是**乐观下界**，真麦验收（§9 余项）可能需要下调。这一条必须写进报告，
不能拿合成音的漂亮数字当真人指标——那就是「评测把调用失败算成模型判断」那类失真的近亲。

前置：`make up`（要 llm-gateway 的 /api/tts + DashScope key）+ 本机 `pip install sherpa-onnx`
      + 模型已拉（scripts/fetch-voice-models.* voiceprint-campplus）。缺任一 → SKIP。
用法：python test/e2e_voiceprint_probe.py [--case all|separation|duration|ood|enroll]
"""
from __future__ import annotations
import argparse
import array
import base64
import io
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台 gbk 防崩

_ROOT = Path(__file__).resolve().parents[1]
LLM_HTTP = os.getenv("LLM_HTTP", "http://localhost:50059")
MODEL_PATH = _ROOT / "models" / "voiceprint" / "campplus_zh-cn_16k-common.onnx"

# 「说话人」= 当前批处理 TTS 引擎自己的音色。**刻意不硬编码某一家的音色 id**——
# /api/tts 不收引擎参数（跟随 TTS_PROVIDER），传错家的 voice_id 会被上游 400
# （首跑就踩了：给 MiMo 引擎传 cosyvoice 的 longxiaochun_v3）。改为经 /api/voices 自动发现。
MAX_SPEAKERS = 4
# 六句长度相当的中文短句（车内真实话术口吻）
SENTENCES = [
    "今天天气怎么样，路上堵不堵",
    "帮我把空调调到二十四度",
    "附近有什么好吃的川菜馆推荐",
    "导航去公司，避开拥堵路段",
    "我想听点轻松一些的音乐",
    "明天早上八点提醒我出发",
]


def _skip(msg: str) -> None:
    print(f"[SKIP] {msg}")
    sys.exit(0)


def discover_speakers() -> list[str]:
    """取当前批处理 TTS 引擎的中文音色（最多 MAX_SPEAKERS 个，尽量男女混合）。"""
    with urllib.request.urlopen(f"{LLM_HTTP}/api/voices", timeout=20) as r:
        voices = json.loads(r.read()).get("voices", [])
    zh = [v for v in voices if (v.get("language") or "zh") == "zh"]
    female = [v["voice_id"] for v in zh if v.get("gender") == "female"]
    male = [v["voice_id"] for v in zh if v.get("gender") == "male"]
    out: list[str] = []
    for i in range(max(len(female), len(male))):   # 交错取，保证不是清一色同性别
        if i < len(female):
            out.append(female[i])
        if i < len(male):
            out.append(male[i])
    if len(out) < 2:
        out = [v["voice_id"] for v in zh]
    return list(dict.fromkeys(out))[:MAX_SPEAKERS]


# ── 音频源：/api/tts 合成 → 16k mono s16le（同 e2e_s2s_probe 的 round-trip 打法）──
_pcm_cache: dict[tuple[str, str], bytes] = {}


def synth_pcm16k(text: str, voice: str) -> bytes:
    key = (text, voice)
    if key in _pcm_cache:
        return _pcm_cache[key]
    req = urllib.request.Request(
        f"{LLM_HTTP}/api/tts", method="POST",
        data=json.dumps({"text": text, "format": "wav", "voice_id": voice}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    if data.get("error"):
        raise RuntimeError(f"TTS 失败: {data['error']}")
    with wave.open(io.BytesIO(base64.b64decode(data["audio"])), "rb") as w:
        ch, fr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        pcm = w.readframes(n)
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
            if j + 1 < len(a):
                f = src - j
                out[i] = int(a[j] * (1 - f) + a[j + 1] * f)
            elif j < len(a):
                out[i] = a[j]
        a = out
    _pcm_cache[key] = a.tobytes()
    return _pcm_cache[key]


def truncate_ms(pcm: bytes, ms: int) -> bytes:
    n = int(16000 * ms / 1000) * 2
    return pcm[:n]


def cosine(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))]


def describe(name: str, vals: list[float]) -> None:
    if not vals:
        print(f"  {name}: (无样本)")
        return
    print(f"  {name}: n={len(vals)} min={min(vals):.4f} p5={pct(vals,5):.4f} "
          f"median={statistics.median(vals):.4f} p95={pct(vals,95):.4f} max={max(vals):.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all",
                    choices=["all", "separation", "duration", "ood", "enroll", "identify"])
    args = ap.parse_args()

    if not MODEL_PATH.exists():
        _skip(f"模型缺失 {MODEL_PATH}（跑 scripts/fetch-voice-models.sh voiceprint-campplus）")
    try:
        import sherpa_onnx
    except ImportError:
        _skip("本机无 sherpa-onnx（pip install sherpa-onnx）——探针在宿主提向量，不经网关")
    try:
        urllib.request.urlopen(f"{LLM_HTTP}/api/health", timeout=5).read()
    except Exception as e:
        _skip(f"llm-gateway 不可达（{e}）——需 make up")

    # ★1 加载
    t0 = time.monotonic()
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(MODEL_PATH), num_threads=1, provider="cpu"))
    load_ms = (time.monotonic() - t0) * 1000
    print(f"\n★1 模型加载：dim={ex.dim} load={load_ms:.0f}ms  file={MODEL_PATH.name}")

    def emb(pcm: bytes) -> list[float]:
        n = len(pcm) // 2
        import struct
        samples = [x / 32768.0 for x in struct.unpack(f"<{n}h", pcm[:n * 2])]
        s = ex.create_stream()
        s.accept_waveform(sample_rate=16000, waveform=samples)
        s.input_finished()
        return list(ex.compute(s))

    # 合成语料
    try:
        speakers = discover_speakers()
    except Exception as e:
        _skip(f"/api/voices 不可用：{e}")
    if len(speakers) < 2:
        _skip(f"当前 TTS 引擎可用中文音色不足 2 个（{speakers}）——无法构造异人对照")
    globals()["SPEAKERS"] = speakers
    SPEAKERS = speakers
    print(f"\n合成语料：{len(SPEAKERS)} 音色 × {len(SENTENCES)} 句 …（音色={SPEAKERS}）")
    audio: dict[str, list[bytes]] = {}
    for v in SPEAKERS:
        audio[v] = []
        for s in SENTENCES:
            try:
                audio[v].append(synth_pcm16k(s, v))
            except Exception as e:
                _skip(f"TTS 合成失败（{v}）：{e}")
        durs = [len(p) / 2 / 16000 for p in audio[v]]
        print(f"  {v}: {len(audio[v])} 段，时长 {min(durs):.1f}~{max(durs):.1f}s")

    embs = {v: [emb(p) for p in audio[v]] for v in SPEAKERS}
    same, diff = [], []
    for v in SPEAKERS:
        e = embs[v]
        same += [cosine(e[i], e[j]) for i in range(len(e)) for j in range(i + 1, len(e))]
    for i, v1 in enumerate(SPEAKERS):
        for v2 in SPEAKERS[i + 1:]:
            diff += [cosine(a, b) for a in embs[v1] for b in embs[v2]]

    if args.case in ("all", "separation"):
        print("\n★2/★3 可分性（同音色不同句 vs 不同音色）")
        describe("同人 same", same)
        describe("异人 diff", diff)
        gap = pct(same, 5) - pct(diff, 95)
        print(f"  分离间隙 same_p5 - diff_p95 = {gap:+.4f}"
              f"  {'✓ 可分' if gap > 0 else '✗ 重叠（见下方混淆对；靠 margin 兜底）'}")
        # 推荐阈值取两个分位数的中点；margin 取异人分布的离散度量级
        thr = (pct(same, 5) + pct(diff, 95)) / 2
        mrg = max(0.03, (pct(diff, 95) - statistics.median(diff)) / 2)
        print(f"  → 建议 VOICEPRINT_THRESHOLD={thr:.2f}  VOICEPRINT_MARGIN={mrg:.2f}")
        print("  混淆对（异人中位余弦，越高越像）：")
        pairs = []
        for i, v1 in enumerate(SPEAKERS):
            for v2 in SPEAKERS[i + 1:]:
                pairs.append((statistics.median(
                    [cosine(a, b) for a in embs[v1] for b in embs[v2]]), v1, v2))
        for m, v1, v2 in sorted(pairs, reverse=True):
            print(f"    {v1} × {v2}: {m:.4f}")

    if args.case in ("all", "identify"):
        # **这才是决定 DoD 的那个数**：单句对单句的分布重叠不等于识别失败——
        # 真实判定拿的是三段均值模板（中心化后噪声互相抵消），比单句对单句稳得多。
        # 前 3 句建模板、后 3 句当探针，完全不重叠。
        print("\n★identify 端到端识别（3 句建模板 / 3 句测，与生产判定同口径）")
        sys.path.insert(0, str(_ROOT / "memory"))
        import voiceprint as VP
        thr, mrg = VP.threshold(), VP.margin()
        templates = {v: VP.mean_template(embs[v][:3]) for v in SPEAKERS}
        stat = {"accept_correct": 0, "accept_wrong": 0,
                "below_threshold": 0, "ambiguous": 0}
        for v in SPEAKERS:
            for e in embs[v][3:]:
                p = VP.l2_normalize(e)
                out = VP.decide([(k, VP.cosine(p, t)) for k, t in templates.items()],
                                thr=thr, mrg=mrg)
                if out["decision"] == "accept":
                    stat["accept_correct" if out["occupant_id"] == v
                         else "accept_wrong"] += 1
                else:
                    stat[out["decision"]] += 1
        total = sum(stat.values())
        print(f"  阈值 threshold={thr} margin={mrg}，样本 {total}")
        for k, n in stat.items():
            print(f"    {k:<18} {n:>3}  ({n / total * 100:.1f}%)")
        print(f"  → 认对率 {stat['accept_correct'] / total * 100:.1f}%  "
              f"**认错率 {stat['accept_wrong'] / total * 100:.1f}%**"
              f"（认错=把 A 的记忆给 B，是唯一有隐私后果的一档；"
              f"其余都只是退回 primary=今天的行为）")

        # 阈值/间隙扫描：两类错误的代价**不对称**——认错有隐私后果，认不出只是回到今天。
        # 故选参不看「准确率最高」，看「认错率为 0 的前提下认对率最高」。
        print("\n  阈值扫描（thr × mrg → 认对% / 认错% / 弃权%）")
        print("        " + "".join(f"mrg={m:<10.2f}" for m in (0.03, 0.05, 0.10, 0.20)))
        for t in (0.45, 0.50, 0.55, 0.60, 0.62, 0.65, 0.70):
            row = f"  {t:.2f}  "
            for m in (0.03, 0.05, 0.10, 0.20):
                ok = wrong = abstain = 0
                for v in SPEAKERS:
                    for e in embs[v][3:]:
                        p = VP.l2_normalize(e)
                        o = VP.decide([(k, VP.cosine(p, tp))
                                       for k, tp in templates.items()], thr=t, mrg=m)
                        if o["decision"] != "accept":
                            abstain += 1
                        elif o["occupant_id"] == v:
                            ok += 1
                        else:
                            wrong += 1
                n = ok + wrong + abstain
                row += f"{ok/n*100:>3.0f}/{wrong/n*100:>3.0f}/{abstain/n*100:<3.0f}  "
            print(row)

    if args.case in ("all", "duration"):
        print("\n★4 最短有效语音时长（截断后与全长模板比）")
        for ms in (1000, 1500, 2000, 3000):
            vals = []
            for v in SPEAKERS:
                full = embs[v][0]
                for p in audio[v][1:]:
                    if len(p) / 2 / 16000 * 1000 < ms:
                        continue
                    vals.append(cosine(full, emb(truncate_ms(p, ms))))
            describe(f"{ms:>4}ms", vals)

    if args.case in ("all", "ood"):
        print("\nR1 分布外音频（本该认不出的东西）")
        rnd = random.Random(42)
        silence = b"\x00\x00" * 16000 * 2
        noise = array.array("h", [rnd.randint(-3000, 3000) for _ in range(16000 * 2)]).tobytes()
        for name, pcm in (("静音", silence), ("白噪", noise)):
            e = emb(pcm)
            vals = [cosine(e, x) for v in SPEAKERS for x in embs[v]]
            describe(f"{name} vs 全部说话人", vals)
        print("  判读：若这些值高于建议阈值，则**单模板场景**下噪声可能被接受——"
              "此时靠 min_speech_ms 与 margin（多模板时噪声对谁都差不多→ambiguous）兜底。")

    if args.case in ("all", "enroll"):
        print("\n★5 三段注册的 self_consistency")
        vals = []
        for v in SPEAKERS:
            e = embs[v][:3]
            vals.append(statistics.mean(
                [cosine(e[i], e[j]) for i in range(3) for j in range(i + 1, 3)]))
        describe("正常注册（同音色三句）", vals)
        bad = []
        n = len(SPEAKERS)
        if n >= 3:
            for i in range(n):
                trio = [embs[SPEAKERS[i]][0], embs[SPEAKERS[(i + 1) % n]][0],
                        embs[SPEAKERS[(i + 2) % n]][0]]
                bad.append(statistics.mean(
                    [cosine(trio[a], trio[b]) for a in range(3) for b in range(a + 1, 3)]))
            describe("坏注册（三段不同人）", bad)
            print(f"  → 建议 VOICEPRINT_MIN_CONSISTENCY 落在 "
                  f"{max(bad):.2f} 与 {min(vals):.2f} 之间")
        else:
            print("  (音色不足 3 个，跳过坏注册对照)")

    print("\n【诚实边界】音频源是 TTS 合成音色而非真人。合成音色之间的可分性通常优于真人，"
          "\n故上面的阈值是**乐观下界**，真麦验收后可能需要下调。不要拿它当真人指标。")


if __name__ == "__main__":
    main()
