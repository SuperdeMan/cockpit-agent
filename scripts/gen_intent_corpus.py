#!/usr/bin/env python3
"""生成 test/eval_corpus/feishu_intents_full.jsonl —— R4.1 P2 覆盖率评测语料资产。

从飞书《同行者公版语音指令表 6.1》意图表（tblN5NfQff850L5O）经 lark-cli 重拉全量 ~1465 行，
把「标准说法」+「高频说法」（换行分隔的多条真实用户说法）拆行、全局去重，产出每行：
  {"text": <说法>, "domain": <domain>, "object": <三级对象>, "edge_expected": null}

纯派生评测资产（与已入库 commands.yaml/entities.yaml 同性质，只入派生字段、不入原表全字段）。
edge_expected 三态（true=端侧应接住 / false=cloud-by-design / null=未甄别）初始全 null，
§5.3 甄别后逐域回填。拆行 + 全局去重是确定性的 → 同一 Base 内容重跑产物 byte 一致（幂等）。

前置：lark-cli 已安装且已认证（见 docs/design/2026-07-03-intent-coverage-gap-analysis.md §2）。
拉取法照抄 scripts/export_commands.py::fetch_records（subprocess shell=True + has_more 分页）。

用法：
  python scripts/gen_intent_corpus.py                  # 经 lark-cli 重拉 + 生成
  python scripts/gen_intent_corpus.py --raw dump.json  # 从已存原始记录（[{field:value},...]）转换（离线）
  python scripts/gen_intent_corpus.py --dump-raw r.json # 只拉取并存原始记录（供离线复跑/审阅）
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "test", "eval_corpus", "feishu_intents_full.jsonl")
BASE_TOKEN = "BmoybN3OnaqCLLsXygocGviknUh"
TABLE_ID = "tblN5NfQff850L5O"


def fetch_records(base_token: str, table_id: str, limit: int = 200) -> list[dict]:
    """经 lark-cli 拉取全量记录，返回 [{field_name: value}, ...]（照抄 export_commands.py）。"""
    records: list[dict] = []
    offset = 0
    while True:
        cmd = [
            "lark-cli", "base", "+record-list",
            "--base-token", base_token, "--table-id", table_id,
            "--limit", str(limit), "--format", "json", "--as", "user",
        ]
        if offset > 0:
            cmd.extend(["--offset", str(offset)])
        res = subprocess.run(cmd, capture_output=True, shell=True)
        out = res.stdout.decode("utf-8", "replace") if res.stdout else ""
        i = out.find("{")
        if res.returncode != 0 or i < 0:
            err = res.stderr.decode("utf-8", "replace") if res.stderr else out
            raise SystemExit(f"lark-cli 拉取失败(offset={offset}): {err[:400]}")
        data = json.loads(out[i:])["data"]
        fields = data["fields"]
        for row in data["data"]:
            records.append({fields[j]: row[j] for j in range(min(len(fields), len(row)))})
        if not data.get("has_more"):
            break
        offset += limit
    return records


def _scalar(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def _split_utterances(cell) -> list[str]:
    """一个「说法」单元格拆成多条：换行分隔，strip，丢空。"""
    if not cell:
        return []
    if isinstance(cell, list):
        cell = "\n".join(str(x) for x in cell)
    return [s.strip() for s in str(cell).replace("\r", "\n").split("\n") if s.strip()]


# §5.3 甄别：导航子类里「端侧车机控制」的关键词（播报开关/音量/静音）。
_NAVI_EDGE_KEYWORDS = ("播报", "音量", "静音")


def _classify_edge_expected(obj: str, text: str):
    """§5.3 甄别（规则化 → gen 可复现，规则本身即文档；本轮只甄别导航 + 交互两类）。

    返回 True（端侧应接住）/ False（cloud-by-design，不计入端侧缺口）/ None（未甄别）。

    - 导航（643 条）：播报开关/音量/静音 = 端侧车机控制（true）；其余「搜 POI / 路线规划 /
      去某地 / 发起·继续·开始导航 / 目的地选第 N 个」= 本就该上云走 navigation Agent（false）。
    - 交互（~26 条，0% 识别）：取消/确认/退出/翻页/选择第 N 个等一律 cloud（false）——尤其
      **裸「取消」必须继续上云**走云端 `_confirm_reply` 确认闭环，端侧新增规则接住裸「取消」秒回
      会**劫持云端待确认会话的语音取消路径**（§5.3 最大坑，执行者必读）。
    - 其余对象：None（未甄别，留待后续逐域回填）。
    """
    if obj == "导航":
        return any(k in text for k in _NAVI_EDGE_KEYWORDS)
    if obj == "交互":
        return False
    return None


def build_corpus(records: list[dict]) -> list[dict]:
    """标准说法 + 高频说法 拆行、全局去重（首见者保留 domain/object），按 §5.3 规则打 edge_expected。"""
    seen: set[str] = set()
    corpus: list[dict] = []
    for rec in records:
        obj = _scalar(rec.get("三级 对象"))
        domain = _scalar(rec.get("domain"))
        for cell in (rec.get("标准说法"), rec.get("高频说法")):
            for text in _split_utterances(cell):
                if text in seen:
                    continue
                seen.add(text)
                corpus.append({"text": text, "domain": domain, "object": obj,
                               "edge_expected": _classify_edge_expected(obj, text)})
    return corpus


def main() -> int:
    ap = argparse.ArgumentParser(description="R4.1 P2 生成飞书全量意图语料 jsonl")
    ap.add_argument("--raw", help="从已存原始记录 JSON（[{field:value},...]）转换，跳过 lark 拉取")
    ap.add_argument("--dump-raw", help="只拉取并把原始记录存到该路径（供离线复跑/审阅），不写 jsonl")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    if args.raw:
        with open(args.raw, encoding="utf-8") as f:
            records = json.load(f)
    else:
        print(f"经 lark-cli 重拉意图表 {TABLE_ID} ...")
        records = fetch_records(BASE_TOKEN, TABLE_ID)
    print(f"  {len(records)} 条记录")

    if args.dump_raw:
        with open(args.dump_raw, "w", encoding="utf-8", newline="\n") as f:
            json.dump(records, f, ensure_ascii=False, indent=0)
        print(f"原始记录已存 {args.dump_raw}")
        return 0

    corpus = build_corpus(records)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for item in corpus:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    dom = Counter(c["domain"] for c in corpus)
    print(f"已写入 {args.out}：{len(corpus)} 条唯一说法")
    for d, n in dom.most_common():
        print(f"  {d or '(空)'}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
