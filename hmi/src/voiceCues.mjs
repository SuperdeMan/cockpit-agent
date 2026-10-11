// 唤醒应答 / 退场应答的句子（HMI 与 App 同一份；2026-10-11 从 HMI handsFreeController 挪来，句子不变）。
// 两端各自用当前播报引擎 + 音色预合成，触发时随机播一条求变化；合成与播放是各端的事，这里只放句子。
// 设计：docs/design/2026-10-11-wake-acknowledgement.md

/** 唤醒应答候选（issue①）：短促——人在等着说话，应答越短越好。 */
export const WAKE_CUE_TEXTS = Object.freeze(['在呢', '我在', '你说', '请讲', '我在听'])

/** 退场应答候选（R4.3b P1 U3）：说「退下吧」等退出词后短促回一句再回待机（播报静音时不出声）。 */
export const EXIT_CUE_TEXTS = Object.freeze(['好的', '好嘞', '我先退下了'])
