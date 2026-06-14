#!/usr/bin/env python3
"""从飞书多维表格导出 responses.yaml。

用法: python scripts/export_responses.py [--base-token TOKEN] [--output PATH]

参考源:
  具体响应表(tblclodUq24mPqnk)
  通用兜底反馈语(tblTlq6fOfrr1M8H)

字段映射（具体响应表）:
  「回复语标识」→ response_id (如 open_ac_cooling_mode_3)
  「响应 ID」→ internal_id (如 R00001)
  「执行状态」→ status 列表 (如 [失败])
  「执行场景」→ scene
  「执行动作」→ action
  「分支标识」→ branch
  「回复语 普通话详细」→ speech_full（换行分隔 → 列表）
  「回复语 普通话简洁」→ speech_brief（换行分隔 → 列表）
  「标准句型」→ pattern
  「高频说法」→ examples（换行分隔 → 列表）

字段映射（兜底表）:
  「回复语标识」→ response_id
  「执行场景」→ scene
  「执行动作」→ action
  「普通话-详细」→ speech_full（换行分隔 → 列表）
  「普通话-精简」→ speech_brief（换行分隔 → 列表）

产物: orchestrator/edge/knowledge/responses.yaml
"""
import argparse
import json
import os
import subprocess
import sys

import yaml


def fetch_records(base_token: str, table_id: str, limit: int = 200) -> list[dict]:
    """通过 lark-cli 拉取多维表格记录，返回 [{field_name: value}, ...] 格式。"""
    all_records = []
    offset = 0
    while True:
        cmd = [
            "lark-cli", "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", str(limit),
            "--format", "json",
        ]
        if offset > 0:
            cmd.extend(["--offset", str(offset)])

        result = subprocess.run(cmd, capture_output=True, shell=True)
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            print(f"Error: {stderr}", file=sys.stderr)
            break
        json_start = stdout.find("{")
        if json_start == -1:
            print("No JSON found in output", file=sys.stderr)
            break
        data = json.loads(stdout[json_start:])

        fields = data["data"]["fields"]
        rows = data["data"]["data"]
        for row in rows:
            record = {}
            for i, field_name in enumerate(fields):
                if i < len(row):
                    record[field_name] = row[i]
            all_records.append(record)

        if not data["data"].get("has_more", False):
            break
        offset += limit

    return all_records


def parse_text_list(raw) -> list[str]:
    """解析换行分隔的文本字段为列表。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split("\n") if x.strip()]
    return []


def parse_status(raw) -> list[str]:
    """解析「执行状态」字段。"""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def records_to_responses(specific_records: list[dict], fallback_records: list[dict]) -> dict:
    """将具体响应表+兜底表记录转换为 responses.yaml 格式。

    输出结构:
    {
      "specific": {
        "open_ac_cooling_mode_3": {
          "internal_id": "R00001",
          "scene": "空调控制",
          "action": "提示异常，播报反馈语",
          "pattern": "<打开>...",
          "variants": [
            {
              "status": ["失败"],
              "branch": "2",
              "speech_full": [...],
              "speech_brief": [...],
            },
          ],
          "examples": [...],
        },
      },
      "fallback": {
        "Car_general_restrictions_1": {
          "scene": "非限制在ON挡或车辆启动后操作",
          "action": "提示异常，播报反馈语",
          "speech_full": [...],
          "speech_brief": [...],
        },
      },
    }
    """
    responses = {"specific": {}, "fallback": {}}

    # --- 具体响应表 ---
    for rec in specific_records:
        resp_id = rec.get("回复语标识", "")
        if not resp_id:
            continue

        internal_id = rec.get("响应 ID", "")
        scene = rec.get("执行场景", "")
        action = rec.get("执行动作", "")
        pattern = rec.get("标准句型", "")
        status = parse_status(rec.get("执行状态", []))
        branch = rec.get("分支标识", "")
        speech_full = parse_text_list(rec.get("回复语 普通话详细", ""))
        speech_brief = parse_text_list(rec.get("回复语 普通话简洁", ""))
        examples = parse_text_list(rec.get("高频说法", ""))

        if resp_id not in responses["specific"]:
            responses["specific"][resp_id] = {
                "internal_id": internal_id,
                "scene": scene,
                "action": action,
            }
            if pattern:
                responses["specific"][resp_id]["pattern"] = pattern
            if examples:
                responses["specific"][resp_id]["examples"] = examples
            responses["specific"][resp_id]["variants"] = []

        # 同一个 reply_id 可能有多个 status/branch 变体
        variant = {}
        if status:
            variant["status"] = status
        if branch:
            variant["branch"] = branch
        if speech_full:
            variant["speech_full"] = speech_full
        if speech_brief:
            variant["speech_brief"] = speech_brief

        if variant:
            responses["specific"][resp_id]["variants"].append(variant)

    # --- 兜底表 ---
    for rec in fallback_records:
        resp_id = rec.get("回复语标识", "")
        if not resp_id:
            continue

        scene = rec.get("执行场景", "")
        action = rec.get("执行动作", "")
        # 兜底表用「普通话-详细」和「普通话-精简」（注意短横线，非空格）
        speech_full = parse_text_list(rec.get("普通话-详细", rec.get("回复语 普通话详细", "")))
        speech_brief = parse_text_list(rec.get("普通话-精简", rec.get("回复语 普通话简洁", "")))

        entry = {"scene": scene, "action": action}
        if speech_full:
            entry["speech_full"] = speech_full
        if speech_brief:
            entry["speech_brief"] = speech_brief

        responses["fallback"][resp_id] = entry

    return responses


def main():
    parser = argparse.ArgumentParser(description="导出 responses.yaml")
    parser.add_argument("--base-token", default="BmoybN3OnaqCLLsXygocGviknUh")
    parser.add_argument("--specific-table", default="tblclodUq24mPqnk")
    parser.add_argument("--fallback-table", default="tblTlq6fOfrr1M8H")
    parser.add_argument("--output", default="orchestrator/edge/knowledge/responses.yaml")
    args = parser.parse_args()

    print(f"拉取具体响应表 {args.specific_table}...")
    specific = fetch_records(args.base_token, args.specific_table)
    print(f"  获取 {len(specific)} 条")

    print(f"拉取兜底表 {args.fallback_table}...")
    fallback = fetch_records(args.base_token, args.fallback_table)
    print(f"  获取 {len(fallback)} 条")

    responses = records_to_responses(specific, fallback)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(responses, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    n_specific = len(responses.get("specific", {}))
    n_fallback = len(responses.get("fallback", {}))
    print(f"已写入 {args.output} (具体 {n_specific} 条, 兜底 {n_fallback} 条)")


if __name__ == "__main__":
    main()
