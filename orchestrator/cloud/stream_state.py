"""D0/T2 流式直通的共享状态判定（B5 §4）。

两条流式直通路径——`engine.py` 的 D0 单步快路径与 `loop.py` 的 T2 循环内直通
——此前各自维护一套局部布尔：D0 一个 `streamed`（speech/action 合并），
T2 三个 `did_speak`/`did_action`/`got_final`。**同一张判定表被抄了两份**，
而 B1 修的正是其中一份抄错了：`elif streamed:` 唯一置 True 的位置在
`final_sr is not None` 内部，那条分支永不可达，部分输出后必然 unary 重跑
（话术播两遍、Agent 调两遍、action 已发出时形成重复副作用）。

**教训不是「那次写错了」，是「同一个判定有两处实现」。** 本模块把判定收成
一份：状态推进（B1 那个 bug 就活在推进逻辑里，不在判定函数里）与三条决策
函数都只有一处，第三条流式路径按同款接入。

## 与 B5 §4.1 草图的两处偏差（都有理由）

1. **`FINAL_RECEIVED` 不进枚举**。草图既把它列成第四个状态、又给
   `outcome_uncertain(state, got_final)` 传一个 `got_final` 形参——同一件事
   的两份声明（B4 教训：第二份声明必然漂移）。实际上「流出过什么」与
   「拿没拿到 final」是**正交**的两维：final 可以在任何流出状态下到达，
   而「已经播过话术了吗」在拿到 final **之后**仍然要用（D0 的 escalate 抑制、
   T2 的挂起前缀不复读）。故枚举只表达流出面，`got_final` 保持独立。
2. **多一个 `StreamTracker`**。草图只给了纯函数，但 B1 的 bug 恰恰在**推进**
   而不在判定——只共享判定函数、让两处各自推进状态，等于把出过事的那一半
   留在原地。tracker 让 `speech`/`action` 事件到状态的映射也只有一份。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StreamAttempt(Enum):
    """一趟流式直通里**已经流给用户**的东西。

    取值按「不可撤回程度」排序：动作已发到 HMI 比话术已念出更不可撤回，
    故 speech+action 归 `ACTION_EMITTED`（与 B5 §4.1 注释「含话术+动作」一致）。
    """

    NO_OUTPUT = "no_output"
    SPEECH_EMITTED = "speech_emitted"
    ACTION_EMITTED = "action_emitted"


@dataclass
class StreamTracker:
    """把流事件推进成 `StreamAttempt`；两条路径共用同一份推进逻辑。

    `spoke` 与 `state` 不是重复声明：`state` 是**决策量**（能不能回退、结果确不
    确定），`spoke` 是**记账量**（T2 的挂起前缀不复读哪一条）。speech+action 并发
    时 `state` 是 ACTION_EMITTED，而话术确实播过了——用 `state` 去回答「播过没」
    会漏，这也是它们必须分开的原因。
    """

    spoke: bool = False
    acted: bool = False
    got_final: bool = False

    def on_speech(self, payload) -> None:
        """空 delta 不算流出过输出——否则一个空串就能把 unary 回退整条关掉。"""
        if payload:
            self.spoke = True

    def on_action(self) -> None:
        self.acted = True

    def on_final(self) -> None:
        self.got_final = True

    @property
    def state(self) -> StreamAttempt:
        if self.acted:
            return StreamAttempt.ACTION_EMITTED
        if self.spoke:
            return StreamAttempt.SPEECH_EMITTED
        return StreamAttempt.NO_OUTPUT


def emitted_anything(state: StreamAttempt) -> bool:
    """流出过任何东西。

    D0 用它抑制 escalate（已播报再改派 = 双重回答）；`allow_unary_fallback`
    按它取反定义，两处判定不可能漂移。
    """
    return state is not StreamAttempt.NO_OUTPUT


def allow_unary_fallback(state: StreamAttempt) -> bool:
    """允许回退到 unary 执行吗——**只有零输出才允许**。

    流出过话术再重跑 = 播两遍；流出过动作再重跑 = 副作用发两遍。
    ⚠ 调用方还要单独检查 `got_final`：拿到了 final 就已经有结果了，
    此时状态可能仍是 NO_OUTPUT（Agent 一句话没说直接给 final），
    那种情况**不该**再跑一次 unary。
    """
    return not emitted_anything(state)


def outcome_uncertain(state: StreamAttempt, got_final: bool) -> bool:
    """动作已发出而 final 丢了：这一步的**结果不确定**。

    既不能当成功（没有回执）也不能重试（重试 = 重复副作用）。唯一诚实的处置
    是查一次世界状态再定话术（`DagExecutor.stream_uncertain_result`），
    并给结果打上与 `_exec_step` 同一把指纹，防 T2 replan 重发同一动作。
    """
    return state is StreamAttempt.ACTION_EMITTED and not got_final
