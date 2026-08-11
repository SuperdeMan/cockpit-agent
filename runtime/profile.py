"""DEPLOY_PROFILE：部署形态闸（B3，方案 `docs/design/2026-08-10-b3-deploy-profile-fail-closed.md`）。

当前全部安全开关是「默认关、演示翻开」的 PoC 形态（R3.1/R3.2 拍板设计）。这不是缺陷，
缺的是**第四种运行形态**：一个 `prod` 档，在其中任何 fail-open 配置都导致**服务拒绝启动**，
而不是打印一行 warning 继续跑。三档语义：

- **dev**（默认，含 ``DEPLOY_PROFILE`` 未设）：**零校验**，逐字保持现状。
- **demo**：软校验——不满足强制表时打一段**聚合** warning（一次性、显眼），不阻断。
- **prod**：硬校验——任一项不满足即以 ``EXIT_CONFIG``(78, sysexits.h ``EX_CONFIG``) 退出，
  错误信息逐项列出「哪个键、当前值、要求值、为什么」。

## 两条设计判据

**① 闸放在唯一出口。** 调用点是 ``runtime.grpcio.aio_server()``——全 Python 服务建 gRPC
server 的必经点，不逐服务改 main。同 B1「安全不变量必须放在唯一出口」：将来新增服务、
新增启动路径，不会有人「忘了加这道闸」。

**② 未知档位不静默回落 dev。** ``DEPLOY_PROFILE=production``（拼错）若回落成 dev，运维会
以为自己在跑硬校验而实际零校验——**静默回落正是本批要消灭的那个形态**，所以未知值直接拒绝启动。

## 为什么每项校验要复刻消费方的解析

强制表里的判定**不是**通用真值判断，而是逐项复刻**真正读这个键的那段代码**：

- ``AUTH_REQUIRED`` 由 Go 侧 ``strings.EqualFold(v, "true")`` 读——``AUTH_REQUIRED=1``
  对它就是**关**；一个「看起来是真」的通用真值检查会在这里报绿，而鉴权其实没开。
- ``PERMISSIONS_FAIL_OPEN`` 由 ``getenv(...,"true").lower() != "false"`` 读——只有字面
  ``false`` 才关得掉。
- ``OBS_CONTENT_CAPTURE`` 由 ``.lower() != "off"`` 读——只有字面 ``off`` 才关得掉。

判据：**校验要防到真正会被拿去判定的那个值**（同 CLAUDE.md §6 记的那一课）。

## 与 Go 网关侧的分工

Python 侧校验**整张强制表**（它的服务持有全部这些键）；Go 网关（``gateway/deployprofile``）
只校验**它自己消费的那几个键**（AUTH_REQUIRED / AUTH_TOKENS / CLOUD_CHANNEL_TOKEN(S) /
GRPC_TLS）。刻意不把 Postgres/Grafana 口令灌进网关容器只为让它「校验得全」——那是把凭据
铺得更广换一个重复的读数。整栈层面不漏：任一项不满足时，持有该键的 Python 服务会拒绝启动。

## prod-target 注释项（记档防遗忘，本批不实现）

JWT/OIDC 替换静态 token、每服务唯一 mTLS 身份、Secret Manager、WebSocket Origin 白名单、
SBOM/SAST、trust-level cap 进执行主链。触发条件：真实公网面或第三方 Agent 生态启动。
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable, Mapping

logger = logging.getLogger("runtime.profile")

PROFILE_ENV = "DEPLOY_PROFILE"
DEV = "dev"
DEMO = "demo"
PROD = "prod"
PROFILES = (DEV, DEMO, PROD)

#: sysexits.h ``EX_CONFIG``——「配置有误」的标准退出码，与「进程崩了」区分开。
EXIT_CONFIG = 78

#: ``.env.example`` 里成文的示例 token 字面。它们是公开示例、不是密钥，可以出现在
#: 报错里（配错的人需要知道自己抄了示例值）。
SAMPLE_TOKEN_LITERALS = frozenset({"demo-u1", "demo-channel-v1"})

#: compose 里 Postgres 的 PoC 默认口令。
DEFAULT_POSTGRES_PASSWORD = "cockpit"
#: `--profile observability` 的 PoC 默认 Grafana 口令。
DEFAULT_GRAFANA_PASSWORD = "admin"

#: 值本身是凭据、任何情况下都不许进日志的键。报告里只出现形状（长度/是否示例值）。
_SECRET_KEYS = frozenset({
    "AUTH_TOKENS", "CLOUD_CHANNEL_TOKEN", "CLOUD_CHANNEL_TOKENS",
    "POSTGRES_PASSWORD", "POSTGRES_DSN", "GRAFANA_ADMIN_PASSWORD",
    "REGISTRY_ADMISSION_TOKENS",
})


class DeployProfileError(RuntimeError):
    """未知 ``DEPLOY_PROFILE`` 值。"""


@dataclass(frozen=True)
class Violation:
    """强制表的一项不满足。``actual`` 已按 :func:`_show` 脱敏。"""

    idx: int
    key: str
    actual: str
    expected: str
    why: str

    def line(self) -> str:
        return (f"  [{self.idx:>2}] {self.key} = {self.actual}\n"
                f"       要求：{self.expected}\n"
                f"       原因：{self.why}")


@dataclass(frozen=True)
class Check:
    idx: int
    key: str
    expected: str
    why: str
    predicate: Callable[[Mapping[str, str]], bool]
    #: 不满足时用哪个键的值展示（默认 ``key``）——供跨键校验（如层 2 通道 token）。
    show_key: str | None = None


# ── 展示与脱敏 ───────────────────────────────────────────────────────────────

def _show(key: str, env: Mapping[str, str]) -> str:
    """把一个 env 值渲染成**可以进日志**的形状。

    凭据类键（``_SECRET_KEYS``）永不回显原值——CLAUDE.md 红线「密钥/token 不进日志」。
    但要保留足够的判别力让人能自查：未设 / 空 / 长度 / 是不是抄了示例值 / 是不是 PoC 默认口令。
    """
    if key not in env:
        return "<未设>"
    raw = env[key]
    if raw == "":
        return "<空>"
    if key not in _SECRET_KEYS:
        return repr(raw)
    if key == "POSTGRES_PASSWORD":
        # 第 8 项同时校验口令与 DSN。只回显口令的形状会让「口令换了但 DSN 没换」那种
        # 失败看起来毫无道理（口令明明是对的），所以把 DSN 那一半的状态一起带出来。
        dsn_note = ("；POSTGRES_DSN 仍内嵌 PoC 默认口令"
                    if f":{DEFAULT_POSTGRES_PASSWORD}@" in _get(env, "POSTGRES_DSN") else "")
        if raw == DEFAULT_POSTGRES_PASSWORD:
            return f"<compose PoC 默认口令{dsn_note}>"
        return f"<已设，{len(raw)} 字符{dsn_note}>"
    if key == "GRAFANA_ADMIN_PASSWORD" and raw == DEFAULT_GRAFANA_PASSWORD:
        return "<PoC 默认口令>"
    if key == "POSTGRES_DSN":
        return ("<内嵌 PoC 默认口令>" if f":{DEFAULT_POSTGRES_PASSWORD}@" in raw
                else f"<已设，{len(raw)} 字符>")
    hit = sorted(s for s in SAMPLE_TOKEN_LITERALS if s in raw)
    if hit:
        return f"<含 .env.example 示例 token：{'、'.join(hit)}>"
    return f"<已设，{len(raw)} 字符>"


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return env.get(key, default)


# ── 各消费方的解析（逐项复刻，不要在这里发明通用真值语义）─────────────────────

def _auth_required_on(env: Mapping[str, str]) -> bool:
    """复刻 ``gateway/edge/auth.go``、``gateway/cloud/main.go``：``EqualFold(v, "true")``。"""
    return _get(env, "AUTH_REQUIRED").strip().lower() == "true"


def _permissions_fail_open(env: Mapping[str, str]) -> bool:
    """复刻 ``orchestrator/cloud/context.py``：只有字面 ``false`` 关得掉。"""
    return _get(env, "PERMISSIONS_FAIL_OPEN", "true").strip().lower() != "false"


def _grpc_tls_on(env: Mapping[str, str]) -> bool:
    """复刻 ``runtime/grpcio.py::_tls_enabled`` 与 ``gateway/tlscfg::Enabled``。

    Go 侧是 **switch 精确匹配**（不 lower），Python 侧 lower 后匹配——取两者交集，
    即要求值恰好是小写的 ``on``/``true``/``1``/``yes``，两边才都认。
    """
    return _get(env, "GRPC_TLS") in ("on", "true", "1", "yes")


def _obs_content_capture_on(env: Mapping[str, str]) -> bool:
    """复刻 ``observability/redact.py``：只有字面 ``off`` 关得掉。"""
    return _get(env, "OBS_CONTENT_CAPTURE", "on").strip().lower() != "off"


def _require_real_providers_on(env: Mapping[str, str]) -> bool:
    """复刻 ``agents/_sdk/provenance.py``。"""
    return _get(env, "REQUIRE_REAL_PROVIDERS", "off").strip().lower() in (
        "on", "true", "1", "yes")


def _debug_vehicle_control_on(env: Mapping[str, str]) -> bool:
    """复刻 ``observability/collector/server.py``：缺省即 **on**。"""
    return _get(env, "DEBUG_VEHICLE_CONTROL", "true").strip().lower() == "true"


def _auth_token_ids(raw: str) -> list[str]:
    """复刻 ``gateway/edge/auth.go::parseAuthTokens`` 的取 token id 部分。"""
    ids = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 3)
        if len(parts) < 4:
            continue          # 畸形条目被消费方跳过，这里同样不计
        token = parts[0].strip()
        if token:
            ids.append(token)
    return ids


def _channel_tokens(raw: str) -> set[str]:
    """复刻 ``gateway/cloud/main.go::parseChannelTokens``。"""
    return {t.strip() for t in raw.split(",") if t.strip()}


# ── 强制表 ───────────────────────────────────────────────────────────────────

def _c4_auth_tokens(env: Mapping[str, str]) -> bool:
    ids = _auth_token_ids(_get(env, "AUTH_TOKENS"))
    return bool(ids) and not (set(ids) & SAMPLE_TOKEN_LITERALS)


def _c5_channel_identity(env: Mapping[str, str]) -> bool:
    """层 2（edge→cloud）通道身份可用且非示例值。

    §2.2 原文把第 5 项写作「匿名回退身份禁用（``AUTH_REQUIRED=true`` 时代码路径已保证，
    校验冗余声明）」。落地时把它**做实**成层 2 那一半：层 1 的匿名回退确实由第 1 项覆盖，
    但 edge→cloud 这条通道有自己的 token 面——``channelTokenAllowed`` 在
    ``AUTH_REQUIRED=false`` 时恒放行，翻 true 之后若 ``CLOUD_CHANNEL_TOKENS`` 为空则**恒
    拒绝**（端云直接断链）。两种都是 prod 不该出现的形态，一条判据同时挡住。
    """
    token = _get(env, "CLOUD_CHANNEL_TOKEN").strip()
    allowed = _channel_tokens(_get(env, "CLOUD_CHANNEL_TOKENS"))
    if not token or not allowed or token not in allowed:
        return False
    return not ({token} & SAMPLE_TOKEN_LITERALS) and not (allowed & SAMPLE_TOKEN_LITERALS)


def _c8_postgres_password(env: Mapping[str, str]) -> bool:
    pwd = _get(env, "POSTGRES_PASSWORD").strip()
    if not pwd or pwd == DEFAULT_POSTGRES_PASSWORD:
        return False
    # DSN 里内嵌的口令也得跟着换——compose 抽了 env 而 DSN 还写死默认口令，
    # 结果是容器口令改了、连接串没改，服务连不上（或者更糟：两边都还是默认值）。
    return f":{DEFAULT_POSTGRES_PASSWORD}@" not in _get(env, "POSTGRES_DSN")


def _c12_grafana_password(env: Mapping[str, str]) -> bool:
    pwd = _get(env, "GRAFANA_ADMIN_PASSWORD").strip()
    return bool(pwd) and pwd != DEFAULT_GRAFANA_PASSWORD


CHECKS: tuple[Check, ...] = (
    Check(1, "AUTH_REQUIRED", "true（字面）",
          "关着=无 token 也能连，回落默认身份 u1；量产不允许匿名会话。",
          _auth_required_on),
    Check(2, "PERMISSIONS_FAIL_OPEN", "false（字面）",
          "开着=请求无 granted_scopes 时注入 PoC 全量权限，等于没有权限系统。",
          lambda env: not _permissions_fail_open(env)),
    Check(3, "GRPC_TLS", "on / true / 1 / yes（小写，Go 侧精确匹配）",
          "关着=服务间 gRPC 明文且互不校验身份；量产必须 mTLS。",
          _grpc_tls_on),
    Check(4, "AUTH_TOKENS", "非空，且不含 .env.example 示例 token",
          "AUTH_REQUIRED=true 但 token 表为空=谁也连不上；抄示例值=公开凭据。",
          _c4_auth_tokens),
    Check(5, "CLOUD_CHANNEL_TOKEN", "非空、∈ CLOUD_CHANNEL_TOKENS、且非示例值",
          "层 2 端云通道的身份面：空允许集会让端云恒断链，示例值等于公开凭据。",
          _c5_channel_identity),
    Check(6, "OBS_CONTENT_CAPTURE", "off（字面）",
          "开着=用户原话/话术/plan/LLM 输入输出明文进采集库；量产必须只留长度+哈希。",
          lambda env: not _obs_content_capture_on(env)),
    Check(7, "REQUIRE_REAL_PROVIDERS", "on / true / 1 / yes",
          "关着=缺凭证的 provider 静默回退 mock，真栈会拿假数据当真答案（豁免走 "
          "REQUIRE_REAL_EXEMPT）。",
          _require_real_providers_on),
    Check(8, "POSTGRES_PASSWORD", f"非空且 ≠ {DEFAULT_POSTGRES_PASSWORD!r}，"
                                  "且 POSTGRES_DSN 不内嵌该默认口令",
          "compose 的 PoC 默认口令是公开的；DSN 与容器口令必须同时换。",
          _c8_postgres_password),
    # 第 9 项（LLM 等凭证）**刻意不单列检查**：它由第 7 项的既有严格闸联动——
    # REQUIRE_REAL_PROVIDERS=on 时缺凭证的 provider 决议本就 fail-fast 拒绝启动。
    # 在这里重造一份「LLM_API_KEY 非空」只会多一处会漂移的清单（provider 数量在变）。
    # 第 10 项（S2S / 视觉隐私三条件）**运行期 env 没有承载**：默认挡位写在 HMI
    # 源码的 DEFAULT_SETTINGS 里（voicePipeline='classic'、visionEnabled=false、
    # voiceprintEnabled=false），没有任何 env 能把它们翻成「默认开」。所以它由**源码级
    # 断言测试**守（runtime/tests/test_privacy_defaults.py），不在这张运行期表里。
    # 「能力从哪里声明」和「能力写在哪个文件」是两件事——检查要打在声明处。
    Check(11, "DEBUG_VEHICLE_CONTROL", "false（字面）",
          "开着=collector 暴露无鉴权的 POST /api/debug/vehicle，可经 NATS 直接改车速/"
          "档位/儿童锁——那正是 VAL 安全门控的判定输入。",
          lambda env: not _debug_vehicle_control_on(env)),
    Check(12, "GRAFANA_ADMIN_PASSWORD", f"非空且 ≠ {DEFAULT_GRAFANA_PASSWORD!r}",
          "observability 档的 PoC 默认凭证是公开的（同 Postgres 口令那一类）。",
          _c12_grafana_password),
)


# ── 判定与执行 ───────────────────────────────────────────────────────────────

def resolve_profile(env: Mapping[str, str] | None = None) -> str:
    """读 ``DEPLOY_PROFILE``。未设/空 → ``dev``；未知值 → :class:`DeployProfileError`。"""
    env = os.environ if env is None else env
    raw = _get(env, PROFILE_ENV).strip().lower()
    if not raw:
        return DEV
    if raw not in PROFILES:
        raise DeployProfileError(
            f"{PROFILE_ENV}={env.get(PROFILE_ENV)!r} 不是合法档位；"
            f"可选：{'/'.join(PROFILES)}（未设=dev）。"
            "**不回落 dev**：拼错档位却按零校验跑，正是本闸要消灭的形态。")
    return raw


def audit(env: Mapping[str, str] | None = None) -> list[Violation]:
    """按强制表逐项判定，返回不满足项（**与当前档位无关**，档位只决定怎么处置）。"""
    env = os.environ if env is None else env
    out = []
    for check in CHECKS:
        try:
            ok = check.predicate(env)
        except Exception as exc:                      # pragma: no cover - 防御
            ok = False
            logger.warning("[profile] check %s raised: %s", check.key, exc)
        if not ok:
            out.append(Violation(check.idx, check.key,
                                 _show(check.show_key or check.key, env),
                                 check.expected, check.why))
    return out


def format_report(profile: str, violations: list[Violation]) -> str:
    head = (f"DEPLOY_PROFILE={profile}：{len(violations)}/{len(CHECKS)} 项生产配置校验未通过")
    body = "\n".join(v.line() for v in violations)
    tail = ("方案与判据：docs/design/2026-08-10-b3-deploy-profile-fail-closed.md §2.2；"
            "键说明见 .env.example / docs/conventions.md §6。")
    return f"{head}\n{body}\n{tail}"


_announced = False


def _reset_announce_state() -> None:
    """仅供测试：清掉「已打印过」标记（生产不调用）。"""
    global _announced
    _announced = False


def enforce_deploy_profile(env: Mapping[str, str] | None = None) -> str:
    """按当前档位执行校验。返回生效档位。

    dev 零校验直接返回；demo 打一次聚合 warning；prod 不满足即 ``SystemExit(EXIT_CONFIG)``。

    幂等性只作用在**打印**上（同进程多次建 server 不刷屏），**判定永不缓存**——
    缓存判定就等于「第二次调用时闸是开的」。
    """
    global _announced
    try:
        profile = resolve_profile(env)
    except DeployProfileError as exc:
        _emit(str(exc), fatal=True)
        raise SystemExit(EXIT_CONFIG) from exc

    if profile == DEV:
        return DEV

    violations = audit(env)
    if not violations:
        if not _announced:
            _announced = True
            logger.info("[profile] DEPLOY_PROFILE=%s：%d 项生产配置校验全部通过",
                        profile, len(CHECKS))
        return profile

    report = format_report(profile, violations)
    if profile == PROD:
        _emit(report + "\nprod 档拒绝启动（fail-closed）。", fatal=True)
        raise SystemExit(EXIT_CONFIG)

    if not _announced:
        _announced = True
        _emit(report + "\ndemo 档只告警不阻断；prod 档下这些项会拒绝启动。", fatal=False)
    return profile


def _emit(text: str, *, fatal: bool) -> None:
    """同时走 stderr 与 logging。

    只走 logging 会在「服务尚未 basicConfig」时把这段话吞掉——而这段话正是运维唯一
    能看到的启动失败原因。
    """
    banner = "!" * 72
    print(f"\n{banner}\n{text}\n{banner}\n", file=sys.stderr, flush=True)
    (logger.error if fatal else logger.warning)("[profile] %s", text)
