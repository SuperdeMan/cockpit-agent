export type EndpointConfig = { threshold: number; negThreshold: number; frameMs: number; speechPadStartMs: number; minSilenceMs: number }
export const ENDPOINT_DEFAULTS: EndpointConfig
export class SileroEndpoint {
  constructor(config?: Partial<EndpointConfig>)
  cfg: EndpointConfig
  triggered: boolean
  reset(): void
  accept(probability: number): 'start' | 'end' | null
}
