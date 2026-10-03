"""运维工具访问 collector 的凭据（格式与校验在 `runtime/obs_access.py`，这里只负责取到一枚令牌）。

取令牌的顺序：
1. e2e 运行器传下来的 ``E2E_COLLECTOR_TOKEN``——运行器的子进程**只**走这一条（子进程不得持有签名密钥）；
2. 本进程环境里的 ``E2E_IDENTITY_SECRET`` 现签；
3. 仓库根 ``.env`` 里的同名密钥现签（手工跑的脚本与探针）。

都没有 ⇒ 空串：collector 会拒绝（503/401），调用方照常报错，不在这里兜底。

    python scripts/obs_token.py      # 打印一枚 12 小时内有效的令牌，给云上 dashboard 粘贴用
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import obs_access  # noqa: E402
from scripts.dev_stack_lib import DevStackError, read_root_env  # noqa: E402


def _secret_from_dotenv(root: Path) -> str:
    try:
        return read_root_env(root, (obs_access.SECRET_ENV,)).get(obs_access.SECRET_ENV, "")
    except DevStackError:
        return ""


def operator_token(environ: Mapping[str, str] | None = None, *, root: Path = ROOT) -> str:
    env = os.environ if environ is None else environ
    token = (env.get(obs_access.TOKEN_ENV) or "").strip()
    if token or env.get("E2E_RUN_ID"):
        return token
    key = obs_access.key_from_env(env) or obs_access.derive_key(_secret_from_dotenv(root))
    return obs_access.issue(key) if key else ""


def collector_headers(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    return obs_access.headers(operator_token(environ))


def collector_auth_frame(environ: Mapping[str, str] | None = None) -> str:
    return obs_access.auth_frame(operator_token(environ))


if __name__ == "__main__":
    token = operator_token()
    if not token:
        print(f"no {obs_access.SECRET_ENV} in the environment or the root .env", file=sys.stderr)
        raise SystemExit(2)
    print(token)
