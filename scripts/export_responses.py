#!/usr/bin/env python3
"""从飞书多维表格导出 responses.yaml。

用法: python scripts/export_responses.py [--base-token TOKEN] [--output PATH]

参考源:
  具体响应表(tblclodUq24mPqnk)
  通用兜底反馈语(tblTlq6fOfrr1M8H)

映射规则:
  「回复语标识」→ key
  「执行状态」+「执行场景」+「分支标识」→ 选择条件
  「回复语 普通话详细/简洁」（换行分隔）→ speech_full / speech_brief 列表

产物: orchestrator/edge/knowledge/responses.yaml
"""
import argparse
import json
import os
import subprocess
import sys

import yaml


def fetch_records(base_token: str, table_id: str, limit: int = 200) -> list[dict]:
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
            print(f"Error: {result.stderr}", file=sys.stderr)
            break
        data = json.loads(result.stdout)
        all_records.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    return all_records


def records_to_responses(specific_records: list[dict], fallback_records: list[dict]) -> dict:
    responses = {}
    # TODO: 映射具体响应表
    for rec in specific_records:
        fields = rec.get("fields", {})
        # key = fields.get("回复语标识", "")
        # scene = fields.get("执行场景", "")
        # status = fields.get("执行状态", "")
        # speech_full = fields.get("回复语 普通话详细", "").split("\n")
        # speech_brief = fields.get("回复语 普通话简洁", "").split("\n")
    # TODO: 映射兜底表
    for rec in fallback_records:
        fields = rec.get("fields", {})
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

    print(f"已写入 {args.output} ({len(responses)} 条)")


if __name__ == "__main__":
    main()
