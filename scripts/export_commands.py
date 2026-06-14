#!/usr/bin/env python3
"""从飞书多维表格导出 commands.yaml。

用法: python scripts/export_commands.py [--base-token TOKEN] [--table-id TID] [--output PATH]

前置: lark-cli 已安装且已认证（LARK_TOKEN 环境变量）
参考源: 同行者公版语音指令表 6.1 分类表(tblMPZYYAzV8YVUp) + 意图表(tblN5NfQff850L5O)

映射规则:
  意图表「data」(JSON) → object / operate / mode / positions / unit / attr
  意图表「三级 对象」→ 中文描述
  意图表「domain」→ domain 归类
  意图表「限制」→ drive_restricted / voice_forbidden
  意图表「网络依赖」→ online
  意图表「多意图-指令类型」→ instruction_type
  意图表「前装公版意图」→ is_standard
  意图表「G91项目」「华宝-三一」→ projects
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
            print(f"Error fetching records: {stderr}", file=sys.stderr)
            break
        json_start = stdout.find("{")
        if json_start == -1:
            print(f"No JSON found in output", file=sys.stderr)
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


# 需要二次确认的危险对象
CONFIRM_OBJECTS = {"door_lock", "fuel_tank_cover", "charging_port", "trunk", "window"}


def parse_restrictions(raw) -> tuple[bool, bool]:
    """解析「限制」字段，返回 (drive_restricted, voice_forbidden)。"""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = [raw] if raw else []
    else:
        items = []
    drive_restricted = any("行车" in r for r in items)
    voice_forbidden = any("不支持语音" in r for r in items)
    return drive_restricted, voice_forbidden


def parse_network(raw) -> str:
    """解析「网络依赖」字段，返回 online_only / offline_ok。"""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = [raw] if raw else []
    else:
        items = []
    # "离线/在线" 表示两种都支持 → offline_ok
    # 仅 "在线" 表示仅在线 → online_only
    if any("在线" in n and "离线" not in n for n in items):
        return "online_only"
    return "offline_ok"


def parse_data_field(raw: str) -> dict:
    """解析 data JSON 字段，处理 <br> 和换行。"""
    if not raw or not isinstance(raw, str):
        return {}
    clean = raw.replace("<br>", "").replace("\n", "").strip()
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, TypeError):
        return {}


def classify_to_objects(intent_records: list[dict]) -> dict:
    """将意图表记录转换为 commands.yaml 的 objects 字典。"""
    objects = {}

    for rec in intent_records:
        intent_id = rec.get("意图 ID", "")
        obj_name_cn = rec.get("三级 对象", "")
        operate_desc = rec.get("五级 操作", "")
        domain = rec.get("domain", "")
        data = parse_data_field(rec.get("data", ""))

        obj_key = data.get("object", "")
        if not obj_key:
            continue

        operate = data.get("operate", "")
        mode = data.get("mode", "")
        unit = data.get("unit", "")
        attr = data.get("attr", "")
        positions = data.get("positions", [])

        drive_restricted, voice_forbidden = parse_restrictions(rec.get("限制", []))
        online = parse_network(rec.get("网络依赖", []))

        # 解析项目支持
        projects = []
        g91 = rec.get("G91项目", "")
        if g91 and g91 not in ("", "0", "/", "不支持"):
            projects.append("G91")
        hs = rec.get("华宝-三一", "")
        if hs and hs not in ("", "0", "/", "不支持"):
            projects.append("华宝-三一")

        # 解析指令类型
        instruction_type_raw = rec.get("多意图-指令类型", [])
        if isinstance(instruction_type_raw, list):
            instruction_type = instruction_type_raw
        elif isinstance(instruction_type_raw, str) and instruction_type_raw:
            instruction_type = [instruction_type_raw]
        else:
            instruction_type = []

        is_standard = rec.get("前装公版意图", False)

        # 初始化 object 条目
        if obj_key not in objects:
            objects[obj_key] = {
                "label": obj_name_cn.split("/")[0] if obj_name_cn else obj_key,
                "operates": [],
                "attrs": [],
                "modes": [],
                "positions": False,
                "units": [],
                "online": online,
                "drive_restricted": drive_restricted,
                "require_confirm": obj_key in CONFIRM_OBJECTS,
                "voice_forbidden": voice_forbidden,
                "projects": [],
                "domains": [],
                "intents": [],
            }

        obj = objects[obj_key]

        # 合并 operate
        if operate and operate not in obj["operates"]:
            obj["operates"].append(operate)

        # 合并 mode
        if mode and mode not in obj["modes"]:
            obj["modes"].append(mode)

        # 合并 attr
        if attr and attr not in obj["attrs"]:
            obj["attrs"].append(attr)

        # 合并 unit
        if unit and unit not in obj["units"]:
            obj["units"].append(unit)

        # 检查 positions
        if positions:
            obj["positions"] = True

        # 合并 projects
        for p in projects:
            if p not in obj["projects"]:
                obj["projects"].append(p)

        # 合并 domain
        if domain and domain not in obj["domains"]:
            obj["domains"].append(domain)

        # 合并 online（取更严格的限制）
        if online == "online_only":
            obj["online"] = "online_only"

        # 合并 drive_restricted
        if drive_restricted:
            obj["drive_restricted"] = True

        # 合并 voice_forbidden
        if voice_forbidden:
            obj["voice_forbidden"] = True

        # 记录 intent 条目
        intent_entry = {
            "id": intent_id,
            "operate": operate,
            "mode": mode,
        }
        if unit:
            intent_entry["unit"] = unit
        if attr:
            intent_entry["attr"] = attr
        if instruction_type:
            intent_entry["instruction_type"] = instruction_type
        if is_standard:
            intent_entry["is_standard"] = True
        obj["intents"].append(intent_entry)

    return objects


def main():
    parser = argparse.ArgumentParser(description="导出 commands.yaml")
    parser.add_argument("--base-token", default="BmoybN3OnaqCLLsXygocGviknUh")
    parser.add_argument("--intent-table", default="tblN5NfQff850L5O", help="意图表 table_id")
    parser.add_argument("--output", default="orchestrator/edge/knowledge/commands.yaml")
    args = parser.parse_args()

    print(f"拉取意图表 {args.intent_table}...")
    intent_records = fetch_records(args.base_token, args.intent_table)
    print(f"  获取 {len(intent_records)} 条记录")

    objects = classify_to_objects(intent_records)

    output = {"objects": objects}
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已写入 {args.output} ({len(objects)} 个对象)")


if __name__ == "__main__":
    main()
