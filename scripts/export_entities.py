#!/usr/bin/env python3
"""从飞书多维表格导出 entities.yaml。

用法: python scripts/export_entities.py [--base-token TOKEN] [--table-id TID] [--output PATH]

参考源: 同行者公版语音指令表 6.1 词库(tblDLspoGsO4Iu4w)

字段映射:
  「主词」→ entity key（如 <车道位置>、<主路>）
  「主词别称」→ aliases 列表（换行分隔）
  「协议标识」→ protocol_id（如 main_road、sub_road）
  「词库属性」→ category 大类（位置类/模式类/颜色类…）
  「子词库」→ children 列表（换行分隔）
  「概念子词库」→ concept_children
  「父词库」→ parent（父 entity key）
  「无限实体」→ infinite（是否无限实体）
  「词库名称」→ display_name

跳过「词库属性」含"大类"的纯分类占位行。

产物: orchestrator/edge/knowledge/entities.yaml
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


def strip_angle_brackets(s: str) -> str:
    """去掉 <> 括号，返回纯文本。"""
    if s and s.startswith("<") and s.endswith(">"):
        return s[1:-1]
    return s


def records_to_entities(records: list[dict]) -> dict:
    """将词库记录转换为 entities.yaml 分组字典。

    输出结构:
    {
      "位置类": {
        "<车道位置>": {
          "label": "车道位置",
          "children": ["<主路>", "<辅路>", ...],
          "infinite": false,
        },
        "<主路>": {
          "label": "主路",
          "protocol_id": "main_road",
          "aliases": ["主干道"],
          "parent": "<车道位置>",
        },
        ...
      },
      "模式类": { ... },
    }
    """
    groups = {}

    for rec in records:
        main_word = rec.get("主词", "")
        if not main_word:
            continue

        # 解析词库属性（大类）
        category_raw = rec.get("词库属性", [])
        if isinstance(category_raw, list):
            category = category_raw[0] if category_raw else "未分类"
        elif isinstance(category_raw, str):
            category = category_raw if category_raw else "未分类"
        else:
            category = "未分类"

        # 跳过纯分类占位行（如 "【位置类】该行仅作为分类方便查看，不录入"）
        if "该行仅作为分类" in main_word or "该行仅作分类" in main_word:
            continue
        # 跳过 category 为"大类"的行
        if category == "大类":
            continue

        if category not in groups:
            groups[category] = {}

        # 构建 entity 条目
        entry = {}

        display_name = rec.get("词库名称", "")
        label = strip_angle_brackets(main_word)
        # 优先用 display_name（去掉括号），否则用 main_word 去括号
        if display_name and display_name != main_word:
            entry["label"] = strip_angle_brackets(display_name)
        elif label != main_word:
            entry["label"] = label

        protocol_id = rec.get("协议标识", "")
        if protocol_id:
            entry["protocol_id"] = protocol_id

        aliases = parse_text_list(rec.get("主词别称", ""))
        if aliases:
            entry["aliases"] = aliases

        children = parse_text_list(rec.get("子词库", ""))
        if children:
            entry["children"] = children

        concept_children = parse_text_list(rec.get("概念子词库", ""))
        if concept_children:
            entry["concept_children"] = concept_children

        parent = rec.get("父词库", "")
        if parent:
            entry["parent"] = parent

        infinite_raw = rec.get("无限实体", [])
        if isinstance(infinite_raw, list):
            infinite = infinite_raw[0] if infinite_raw else "否"
        elif isinstance(infinite_raw, str):
            infinite = infinite_raw
        else:
            infinite = "否"
        if infinite == "是":
            entry["infinite"] = True

        groups[category][main_word] = entry

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

    total = sum(len(v) for v in entities.values())
    print(f"已写入 {args.output} ({len(entities)} 个分组, {total} 个实体)")


if __name__ == "__main__":
    main()
