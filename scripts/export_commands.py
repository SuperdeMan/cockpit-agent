#!/usr/bin/env python3
"""从飞书多维表格导出 commands.yaml。

用法: python scripts/export_commands.py [--base-token TOKEN] [--table-id TID] [--output PATH]

前置: pip install lark-cli (或通过环境变量 LARK_TOKEN 认证)
参考源: 同行者公版语音指令表 6.1 分类表(tblMPZYYAzV8YVUp) + 意图表(tblN5NfQff850L5O)

映射规则:
  分类表「五级操作/四级功能/三级对象组」→ object 层级
  「意图」+ per-project 列 → projects
  意图表「限制」→ drive_restricted / voice_forbidden
  意图表「网络依赖」→ online
  危险动作清单(door_lock/fuel_tank_cover/charging_port…) → require_confirm

产物: orchestrator/edge/knowledge/commands.yaml
"""
import argparse
import json
import os
import subprocess
import sys

import yaml


def fetch_records(base_token: str, table_id: str, limit: int = 200) -> list[dict]:
    """通过 lark-cli 拉取多维表格记录。"""
    all_records = []
    page_token = ""
    while True:
        cmd = [
            "lark-cli", "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", str(limit),
        ]
        if page_token:
            cmd.extend(["--page-token", page_token])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error fetching records: {result.stderr}", file=sys.stderr)
            break

        data = json.loads(result.stdout)
        all_records.extend(data.get("items", []))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")

    return all_records


def classify_to_objects(classify_records: list[dict], intent_records: list[dict]) -> dict:
    """将分类表+意图表记录转换为 commands.yaml 格式。"""
    objects = {}

    # 从分类表提取 object 层级
    for rec in classify_records:
        fields = rec.get("fields", {})
        # TODO: 映射分类表字段到 object 结构
        # obj_name = normalize(fields.get("三级对象组", ""))
        # ...

    # 从意图表补充限制/网络/确认属性
    for rec in intent_records:
        fields = rec.get("fields", {})
        # TODO: 映射意图表字段
        # restriction = fields.get("限制", "")
        # network = fields.get("网络依赖", "")
        # ...

    return objects


def main():
    parser = argparse.ArgumentParser(description="导出 commands.yaml")
    parser.add_argument("--base-token", default="BmoybN3OnaqCLLsXygocGviknUh")
    parser.add_argument("--classify-table", default="tblMPZYYAzV8YVUp", help="分类表 table_id")
    parser.add_argument("--intent-table", default="tblN5NfQff850L5O", help="意图表 table_id")
    parser.add_argument("--output", default="orchestrator/edge/knowledge/commands.yaml")
    args = parser.parse_args()

    print(f"拉取分类表 {args.classify_table}...")
    classify_records = fetch_records(args.base_token, args.classify_table)
    print(f"  获取 {len(classify_records)} 条记录")

    print(f"拉取意图表 {args.intent_table}...")
    intent_records = fetch_records(args.base_token, args.intent_table)
    print(f"  获取 {len(intent_records)} 条记录")

    objects = classify_to_objects(classify_records, intent_records)

    output = {"objects": objects}
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已写入 {args.output} ({len(objects)} 个对象)")


if __name__ == "__main__":
    main()
