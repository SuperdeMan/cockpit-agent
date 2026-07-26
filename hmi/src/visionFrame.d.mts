export const VISION_TRIGGER: RegExp
export const VISION_GUARD: RegExp
/** 这句话要不要抓帧（与 agents/vision/manifest.yaml 的 route_hints 同口径）。 */
export function needsFrame(text: string | null | undefined): boolean
/** 抓一帧并上传，返回 frame_id；任何失败返回空串（由 Agent 侧诚实降级）。 */
export function captureFrame(
  audioApi: string,
  deps?: { getStream?: () => Promise<MediaStream>; grab?: (s: MediaStream) => Promise<Blob | null> },
): Promise<string>
