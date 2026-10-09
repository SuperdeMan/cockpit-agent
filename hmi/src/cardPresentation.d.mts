export const CARD_NAMES: Record<string, string>
export function present(value: unknown): boolean
export function cardTitle(card: unknown): string
export function drivingCardSummary(card: unknown): {title: string; main: string; unit: string; fields: string[];button?:{label:string;text:string}} | null
