export const MAX_MANUAL_IMAGE_URI_CHARS: number
export const MAX_MANUAL_IMAGES: number

export type ManualImageLike = {
  asset_id?: string
  sha256?: string
  caption?: string
  description?: string
  page_start?: number
  media_type?: string
  data_uri?: string
  width?: number
  height?: number
  role?: string
  match_kind?: string
  [key: string]: unknown
}

export function manualImageUri(value: unknown): string
export function manualImages(card: { images?: unknown[] } | null | undefined): ManualImageLike[]
