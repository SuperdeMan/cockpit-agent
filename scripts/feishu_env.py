"""飞书 Base app_token 的统一取值口（语料工具专用，栈运行不消费）。

标识符 2026-08-02 卫生脱敏退出源码；取值顺序＝环境变量 → 根 `.env`。
读 `.env` 是因为家规「密钥/标识符只进 .env」，而这些离线脚本不经 compose、
没人替它们做插值。脚本假定 cwd=仓库根（与各脚本相对路径的既有假设一致）。
"""

from __future__ import annotations

import os


def feishu_base_token() -> str:
    token = os.environ.get("FEISHU_BASE_TOKEN", "")
    if token:
        return token
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FEISHU_BASE_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""
