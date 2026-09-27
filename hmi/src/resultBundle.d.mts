import type { Msg, ResultBundle, ResultEntry, UiCard } from './types'

export function readResultBundles(frame: unknown): ResultBundle[]
export function projectResultFinal(frame: unknown): Pick<Msg, 'text' | 'resultBundles'>
export function mergeResultMessage(previous: Msg, next: Partial<Msg>): Msg
export function resultCard(bundle: ResultBundle, entry: ResultEntry, finalCard?: UiCard): UiCard | null
export function resultDetails(message: Pick<Msg, 'resultBundles' | 'uiCard'> & { text?: string }): Array<{ key: string; answer: string; card: UiCard | null }>
