"""飞书数据源相关取值的统一入口（语料工具专用，栈运行不消费）。

公司/项目标识符 2026-08-02 卫生脱敏退出源码；取值顺序＝环境变量 → 根 `.env`。
读 `.env` 是因为家规「密钥/标识符只进 .env」，而这些离线脚本不经 compose、
没人替它们做插值。脚本假定 cwd=仓库根（与各脚本相对路径的既有假设一致）。
"""

from __future__ import annotations

import os


def _read(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def feishu_base_token() -> str:
    """《公版语音指令表》Base app_token（FEISHU_BASE_TOKEN）。"""
    return _read("FEISHU_BASE_TOKEN")


def feishu_project_columns() -> dict[str, str]:
    """意图表 per-project 支持列名 → 脱敏项目代号（FEISHU_PROJECT_COLUMNS）。

    格式 `<列名>:project-b,<列名>:project-d`——真实列名含公司/项目名，不入库。
    缺失时导出产物的 `projects` 为空；VAL 车型裁剪闸（`vehicle_model`）默认关，
    无运行行为影响，但会与已入库 commands.yaml 的 projects 产生 diff——重导出前
    先把映射配全。
    """
    mapping: dict[str, str] = {}
    for pair in _read("FEISHU_PROJECT_COLUMNS").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        column, code = pair.split(":", 1)
        if column.strip() and code.strip():
            mapping[column.strip()] = code.strip()
    return mapping
