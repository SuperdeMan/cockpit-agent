export const BARGE_IN_JUDGE_PATH: string
export const BARGE_IN_JUDGE_TIMEOUT_MS: number
export function judgeBargeIn(
  apiBase: string,
  q: { utterance: string; previous?: string; assistantName?: string; sessionId?: string },
  opts?: { token?: string; timeoutMs?: number; fetchImpl?: typeof fetch },
): Promise<boolean | null>
