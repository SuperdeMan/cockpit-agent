#!/usr/bin/env python3
"""从飞书多维表格导出 entities.yaml。

用法: python scripts/export_entities.py [--base-token TOKEN] [--table-id TID] [--output PATH]

参考源: 同行者公版语音指令表 6.1 词库(tblDLspoGsO4Iu4w)

映射规则:
  「主词」+「主词别称」→ key
  「协议标识」→ value
  「子词库/概念父子库」展开父库（如 <车道位置>→各子项）
  按「词库属性=大类」分组（位置类/模式类/颜色类…）
  跳过标「该行仅作分类」的占位行

产物: orchestrator/edge/knowledge/entities.yaml
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
            print(f"Error: {result.stderr}", file=sys.stderr)
            break

        data = json.loads(result.stdout)
        all_records.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    return all_records


def records_to_entities(records: list[dict]) -> dict:
    """将词库记录转换为 entities.yaml 分组字典。"""
    groups = {}
    for rec in records:
        fields = rec.get("fields", {})
        # TODO: 映射词库字段
        # category = fields.get("词库属性", "未分类")
        # main_word = fields.get("主词", "")
        # alias = fields.get("主词别称", "")
        # protocol_id = fields.get("协议标识", "")
        # ...
    return groups


def main():
    parser = argparse.ArgumentParser(description="导出 entities.yaml")
    parser.add_argument("--base-token", default="BmoybN3OnaqCLLsXygocGviknUh")
    parser.add_argument("--table-id", default="tblDLspoGsO4Iu4w")
    parser.add_argument("--output", default="orchestrator/edge/knowledge/entities.yaml")
    args = parser.parse_args()

    print(f"拉取词库 {args.table_id}...")
    records = fetch_records(args.base_token, args.table_id)
    print(f"  获取 {len(records)} 条记录")

    entities = records_to_entities(records)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(entities, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已写入 {args.output} ({len(entities)} 个分组)")


if __name__ == "__main__":
    main()
