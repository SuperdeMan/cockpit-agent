"""LLM 运行时：多 provider 注册表 + 全局 active 切换 + 档位解析 + embedding 解耦。

gRPC 服务（server.py）与 HTTP 控制端点（http_server.py）在 llm-gateway 同一进程内，
共用本模块的进程内单例 `get_runtime()`：

- **注册表**：按 env 装配所有「已配置 key」的 chat provider（mimo/minimax/deepseek/qwen；
  legacy anthropic 特例）。一套 `OpenAICompatibleProvider` 靠 token_param/thinking_style/auth
  三个 per-provider 参数覆盖四家差异（见 providers.py）。
- **全局 active**：默认 `LLM_PROVIDER`；运行时经 HTTP `/api/llm/provider` 切换，所有服务的 LLM
  调用随之切换（座舱「单一大脑」模型）。切换**持久化到 Redis**（`llm:active`）：网关重启/重建
  读回上次选择，不再静默回落 env 默认（07-12 教训：重建后 eval 换了脑子无人知晓）；Redis
  缺包/不可达时降级为进程内存态（仅告警，不拒启）。HMI 载入时的 POST 重放保留（幂等）。
- **档位解析**：调用方传 `""`→primary、`"@fast"`→fast、`"@primary"/"@deep"`→primary；传了当前
  provider 不认识的具体模型名 → 回落 primary（防「切到 DeepSeek 却收到 chitchat 发来的 mimo 模型名」）。
- **embedding 解耦**：`embed_provider()` 独立按 `LLM_EMBED_*`（DashScope）建，**与 active chat
  provider 无关**——切到无 embedding 的 chat 服务商（如 DeepSeek）也不影响记忆语义召回。
"""
from __future__ import annotations
import json
import logging
import os
import time

import httpx

from health import health_tracker
from providers import (
    BaseProvider, MockProvider, AnthropicProvider, OpenAICompatibleProvider,
    _strict_mock_gate,
)

logger = logging.getLogger("llm.runtime")

# active 选择的持久化键（Redis）。value: {"provider": str, "model": str}
_ACTIVE_KEY = "llm:active"


def _redis_client():
    """Redis 客户端（可选依赖，仅用于 active 持久化）。缺包/URL 未配 → None，
    持久化静默降级为进程内存态（LLM 服务本身不依赖 Redis 可用）。"""
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis  # 延迟导入：精简镜像/宿主测试缺包不影响启动
        return redis.Redis.from_url(
            url, socket_connect_timeout=1.0, socket_timeout=1.0,
            decode_responses=True)
    except Exception as e:
        logger.warning("llm:active 持久化不可用（redis 缺包/URL 无效）：%s", e)
        return None

# provider id → 静态配置。endpoint/model 均 env 可覆盖（*_env 键）；key 见 _provider_key。
_PROVIDER_SPECS: dict[str, dict] = {
    "mimo": {
        "label": "MiMo · 小米", "key_env": "LLM_API_KEY",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
        "auth_style": "api-key", "token_param": "max_completion_tokens", "thinking_style": "mimo",
        "primary_env": "LLM_MODEL_PRIMARY", "primary": "mimo-v2.5-pro",
        "fast_env": "LLM_MODEL_FAST", "fast": "mimo-v2.5",
        "models": [("mimo-v2.5-pro", "MiMo 2.5 Pro"), ("mimo-v2.5", "MiMo 2.5 · 快")],
    },
    "minimax": {
        "label": "MiniMax", "key_env": "MINIMAX_API_KEY", "base_url_env": "MINIMAX_BASE_URL",
        "base_url": "https://api.minimaxi.com/v1/chat/completions",
        "auth_style": "bearer", "token_param": "max_completion_tokens", "thinking_style": "mimo",
        "primary_env": "MINIMAX_LLM_MODEL", "primary": "MiniMax-M3",
        "fast": "MiniMax-M3",
        "models": [("MiniMax-M3", "MiniMax-M3")],
    },
    "deepseek": {
        # DeepSeek v4-pro/flash 是推理模型（reasoning_content 占 completion 预算）——真栈探测确认它
        # 与 MiMo/MiniMax 同样认 thinking:{type:disabled}（reasoning_effort/enable_thinking 均无效），
        # 故 thinking_style=mimo：结构化任务关思考拿干净 content（不被 reasoning 饿空），复杂任务开。
        "label": "DeepSeek", "key_env": "DEEPSEEK_API_KEY", "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "auth_style": "bearer", "token_param": "max_tokens", "thinking_style": "mimo",
        "primary_env": "DEEPSEEK_MODEL_PRIMARY", "primary": "deepseek-v4-pro",
        "fast_env": "DEEPSEEK_MODEL_FAST", "fast": "deepseek-v4-flash",
        "models": [("deepseek-v4-pro", "DeepSeek V4 Pro"), ("deepseek-v4-flash", "DeepSeek V4 Flash")],
    },
    "qwen": {
        "label": "阿里百炼 · 通义千问", "key_env": "DASHSCOPE_LLM_KEY", "base_url_env": "QWEN_BASE_URL",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "auth_style": "bearer", "token_param": "max_tokens", "thinking_style": "qwen",
        "primary_env": "QWEN_MODEL_PRIMARY", "primary": "qwen3.7-max",
        "fast_env": "QWEN_MODEL_FAST", "fast": "qwen3.7-plus",
        "models": [("qwen3.7-max", "通义千问 3.7 Max"), ("qwen3.7-plus", "通义千问 3.7 Plus")],
    },
}


def _norm_id(pid: str) -> str:
    pid = (pid or "").strip().lower()
    return {"xiaomimimo": "mimo", "mimo": "mimo"}.get(pid, pid)


def _provider_key(pid: str, spec: dict) -> str:
    """取该 provider 的 key。qwen 复用现有百炼 key（DASHSCOPE_ASR_KEY / LLM_EMBED_API_KEY，同一 DashScope 账号）。"""
    if pid == "qwen":
        return (os.getenv("DASHSCOPE_LLM_KEY") or os.getenv("DASHSCOPE_ASR_KEY")
                or os.getenv("LLM_EMBED_API_KEY", ""))
    return os.getenv(spec["key_env"], "")


def _env_or(name: str, default: str) -> str:
    return (os.getenv(name, "") if name else "") or default


def _build_embed_provider() -> BaseProvider:
    """独立 embedding provider（DashScope 百炼），与 active chat provider 解耦——**这是本次多 LLM 源
    的关键 fix**：配了 `LLM_EMBED_API_KEY` 时，把 active chat 切到无 embedding 能力的厂商（DeepSeek/
    MiniMax）也不影响记忆语义召回。无 key → MockProvider 伪向量（沿用既有「打通 pgvector 链路/测试」
    的兜底，见 providers.MockProvider._mock_embed_one）。"""
    key = os.getenv("LLM_EMBED_API_KEY", "")
    if not key:
        # 严格栈禁 embed mock：伪向量在 EMBED_DIM=384 时会撞维度混进召回（治理文档 §7 边缘）
        _strict_mock_gate("embed", "LLM_EMBED_API_KEY 未配置")
        return MockProvider()
    return OpenAICompatibleProvider(
        key, base_url="",  # chat 端点不用，仅用 embed_*
        embed_url=os.getenv("LLM_EMBED_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"),
        embed_model=os.getenv("LLM_EMBED_MODEL", "text-embedding-v4"),
        embed_api_key=key,
        embed_auth_style=os.getenv("LLM_EMBED_AUTH_STYLE", "bearer"),
        embed_dimensions=int(os.getenv("LLM_EMBED_DIMENSIONS", "0") or 0),
    )


class LLMRuntime:
    def __init__(self):
        self._registry: dict[str, tuple[BaseProvider, dict]] = {}  # id -> (provider, cfg)
        self._catalog: list[dict] = []      # 全部（含未配置置灰），供 HMI 列表
        self._active_id = "mock"
        self._active_model = ""             # 空 = 用 active 的 primary
        self._embed: BaseProvider = MockProvider()
        self._redis = _redis_client()
        self._build()

    def _build(self):
        legacy = _norm_id(os.getenv("LLM_PROVIDER", "mimo"))
        # legacy anthropic 特例：LLM_API_KEY 是 anthropic key（非 mimo）——注册 anthropic、跳过 mimo。
        anthropic_key = os.getenv("LLM_API_KEY", "") if legacy == "anthropic" else ""

        for pid, spec in _PROVIDER_SPECS.items():
            if pid == "mimo" and anthropic_key:
                continue
            key = _provider_key(pid, spec)
            base_url = _env_or(spec.get("base_url_env", ""), spec["base_url"])
            primary = _env_or(spec.get("primary_env", ""), spec["primary"])
            fast = _env_or(spec.get("fast_env", ""), spec.get("fast") or primary)
            cfg = {
                "id": pid, "label": spec["label"], "available": bool(key),
                "primary": primary, "fast": fast,
                "models": [{"id": m, "label": lbl} for m, lbl in spec["models"]],
            }
            self._catalog.append(cfg)
            if key:
                provider = OpenAICompatibleProvider(
                    key, base_url=base_url, auth_style=spec["auth_style"],
                    disable_thinking=True,  # 全局默认关；复杂任务经 meta thinking=on 动态开
                    token_param=spec["token_param"], thinking_style=spec["thinking_style"])
                self._registry[pid] = (provider, cfg)

        if anthropic_key:
            model = os.getenv("LLM_MODEL_PRIMARY", "claude-sonnet-5")
            cfg = {"id": "anthropic", "label": "Anthropic Claude", "available": True,
                   "primary": model, "fast": os.getenv("LLM_MODEL_FAST", "") or model,
                   "models": [{"id": model, "label": model}]}
            self._catalog.insert(0, cfg)
            self._registry["anthropic"] = (AnthropicProvider(anthropic_key), cfg)

        want = "anthropic" if anthropic_key else legacy
        if want in self._registry:
            self._active_id = want
        elif self._registry:
            self._active_id = next(iter(self._registry))
        else:
            # 严格栈禁 mock 成为 active（LLM mock 话术最具欺骗性）
            _strict_mock_gate("llm", "无任何已配置的 chat 厂商 key")
            cfg = {"id": "mock", "label": "Mock（未配置 key）", "available": True,
                   "primary": "mock", "fast": "mock", "models": [{"id": "mock", "label": "Mock"}]}
            self._registry["mock"] = (MockProvider(), cfg)
            self._catalog.append(cfg)
            self._active_id = "mock"

        self._embed = _build_embed_provider()
        self._load_persisted()
        logger.info("LLM runtime: active=%s, registry=%s", self._active_id, list(self._registry))
        print(f"[llm-gateway] LLM runtime active={self._active_id} "
              f"available={[c['id'] for c in self._catalog if c['available']]}", flush=True)

    def _load_persisted(self):
        """启动时读回上次 set_active 的选择——重启/重建不再回落 env 默认（07-12 教训）。"""
        if self._redis is None:
            return
        try:
            raw = self._redis.get(_ACTIVE_KEY)
        except Exception as e:
            logger.warning("读 %s 失败（保持 env 默认 active=%s）：%s", _ACTIVE_KEY, self._active_id, e)
            return
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        pid = _norm_id(data.get("provider", "") if isinstance(data, dict) else "")
        if pid not in self._registry:
            logger.warning("持久化的 provider=%s 未配置，保持 env 默认 active=%s", pid, self._active_id)
            return
        self._active_id = pid
        model = (data.get("model") or "").strip()
        known = {m["id"] for m in self._registry[pid][1]["models"]}
        self._active_model = model if model in known else ""
        logger.info("LLM active 恢复自持久化: %s model=%s", pid, self._active_model or "(primary)")

    def _persist_active(self):
        if self._redis is None:
            return
        try:
            self._redis.set(_ACTIVE_KEY, json.dumps(
                {"provider": self._active_id, "model": self._active_model}))
        except Exception as e:
            logger.warning("写 %s 失败（本次切换仅进程内存态）：%s", _ACTIVE_KEY, e)

    # ── 服务面 ──
    @property
    def active_id(self) -> str:
        return self._active_id

    def active_provider(self) -> BaseProvider:
        return self._registry[self._active_id][0]

    def active_config(self) -> dict:
        return self._registry[self._active_id][1]

    def embed_provider(self) -> BaseProvider:
        return self._embed

    def resolve_models(self, requested: str) -> list[str]:
        """档位/具体模型 → 待尝试模型列表（含降级）。见模块 docstring。"""
        return self.resolve_models_for(self._active_id, requested)

    def provider_entry(self, pid: str):
        """注册表查询（含别名归一）。返回 (norm_pid, provider) 或 None——供请求级 pin（D2）。"""
        norm = _norm_id(pid)
        entry = self._registry.get(norm)
        return (norm, entry[0]) if entry else None

    def resolve_models_for(self, pid: str, requested: str, model_override: str = "") -> list[str]:
        """档位解析（指定 provider 版，运行时硬化 D2）。pid 须已在注册表（调用方先
        fail-closed）；model_override=meta.llm_model，须在该厂商词表内否则忽略。"""
        norm = _norm_id(pid)
        cfg = self._registry[norm][1]
        known = {m["id"] for m in cfg["models"]}
        primary = ((model_override if model_override in known else "")
                   or (self._active_model if norm == self._active_id else "")
                   or cfg["primary"])
        fast = cfg.get("fast") or primary
        r = (requested or "").strip()
        if not r or r in ("@primary", "@deep"):
            chosen = primary
        elif r in ("@fast", "@fallback"):
            chosen = fast
        else:
            chosen = r if r in known else primary  # 不认识的具体模型名 → 回落 primary
        out = [chosen]
        if fast and fast != chosen:
            out.append(fast)          # 降级链：primary 失败退 fast
        return out or ["mock"]

    def cache_scope(self) -> str:
        """缓存命名空间：并入 active provider + 具体模型，避免切换后串味。"""
        return f"{self._active_id}:{self._active_model or self.active_config()['primary']}"

    # ── 控制面（HTTP）──
    def set_active(self, provider: str, model: str = "") -> dict:
        pid = _norm_id(provider)
        if pid not in self._registry:
            raise ValueError(f"provider 未配置或不可用: {provider}")
        self._active_id = pid
        known = {m["id"] for m in self.active_config()["models"]}
        self._active_model = model if (model and model in known) else ""
        self._persist_active()
        logger.info("LLM active switched -> %s model=%s", pid, self._active_model or "(primary)")
        return self.status()

    def status(self) -> dict:
        return {
            "active": {"provider": self._active_id,
                       "model": self._active_model or self.active_config()["primary"]},
            "providers": [dict(c) for c in self._catalog],
            # 被动健康（D5）：available=配了 key，health=最近真的答得上来（滚动窗口）
            "health": health_tracker.snapshot(),
        }

    async def probe(self, provider: str = "") -> dict:
        """按需体检（D5）：对指定（缺省=active）provider 的 primary 模型发一条小请求。
        不改 active、不进缓存；结果记入 health。演示前手检用——刻意无周期探活。"""
        pid = _norm_id(provider) or self._active_id
        if pid not in self._registry:
            return {"ok": False, "provider": pid, "error": "provider 未配置或不可用"}
        prov, cfg = self._registry[pid]
        t0 = time.monotonic()
        try:
            content, used, _, _ = await prov.complete(
                [{"role": "user", "content": "ping"}], cfg["primary"], 0.1, 8,
                thinking=False, timeout_s=10)
            ms = round((time.monotonic() - t0) * 1000, 1)
            health_tracker.record(pid, True, latency_ms=ms)
            return {"ok": True, "provider": pid, "model": used, "latency_ms": ms}
        except Exception as e:
            ms = round((time.monotonic() - t0) * 1000, 1)
            kind = ("timeout" if isinstance(e, httpx.TimeoutException)
                    else "rate_limited" if getattr(e, "status_code", 0) == 429 else "")
            health_tracker.record(pid, False, kind=kind, error=str(e))
            return {"ok": False, "provider": pid, "latency_ms": ms, "error": str(e)[:300]}


_runtime: LLMRuntime | None = None


def get_runtime() -> LLMRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LLMRuntime()
    return _runtime
