// 发送前置路由（实施计划 M1-2）：候选拦截的纯函数版，分支与顺序逐条对照
// hmi/src/App.tsx:722-845（视觉抓帧属 M4，App 不移植）。判定与副作用分开：
// 这里只产决策，候选清理/翻页/占位/上行由 store 按决策执行——node 直接单测。
import { isRefreshRequest, ordinalSelectIn, poiSelectionIndex } from '@shared/nav.mjs'
import { isLocationDependent, shouldRequestLocationConsent } from '@shared/location.mjs'
import type { CandidateState } from './candidates'

/** 决策要清掉的候选槽（对照 HMI 各分支置 null 的 ref；place 分支刻意不清，与 HMI 一致） */
export type CandidateClearKey = 'poi' | 'dest' | 'waypoint' | 'intent' | 'merchant'

export interface RouteCtx {
  candidates: CandidateState
  locationEnabled: boolean
}

export type RouteDecision =
  | {
      kind: 'dispatch'
      text: string
      metaExtra?: Record<string, string>
      /** true=发送前刷新一次实时定位并带坐标（拿不到照发不带） */
      withLocation?: boolean
      clear?: CandidateClearKey[]
      /** 「换一批」翻页：category 推进到该页（关键词不变） */
      categoryPage?: number
    }
  | {
      /** 未开定位且命中位置依赖 → 本地征询条（纯前端；同意带坐标重发/拒绝照发不带） */
      kind: 'consent'
      text: string
    }

// 行程内导航/修改整句：交编排器路由到 trip.navigate/modify，不被上一条候选的「第N个」劫持
// （「第二天第一个」≠ 上一条候选第 1 个）。App.tsx:740 同款正则。
const TRIP_GUARD_RE = /下一站|下个景点|继续导航|第\s*[一二两三四五六七八九十\d]+\s*天/
// 周边发现「第N个」带导航词 → 导航；否则看详情。App.tsx:810 同款。
const PLACE_NAVIGATE_RE = /导航|带我去|开车去|送我|去第|到第/

export function routeSend(
  text: string,
  ctx: RouteCtx,
  metaExtra?: Record<string, string>,
): RouteDecision {
  const cand = ctx.candidates

  if (TRIP_GUARD_RE.test(text)) {
    return { kind: 'dispatch', text, clear: ['poi', 'dest', 'waypoint'] }
  }

  const ic = cand.intentChoice
  if (ic && ic.options.length) {
    const idx = ordinalSelectIn(text)
    const hit =
      idx >= 0 && idx < ic.options.length
        ? ic.options[idx]
        : ic.options.find((o) => o.send_text === text || o.label === text)
    if (hit) {
      return {
        kind: 'dispatch',
        text: hit.send_text,
        metaExtra: { clarify_resume: '1' },
        clear: ['intent'],
      }
    }
    // 不命中（换话题）→ 继续正常路径；卡片在下一轮 final 到达时互斥清空=自然作废
  }

  const mm = cand.merchantMenu
  if (mm && mm.options.length) {
    const mi = ordinalSelectIn(text)
    if (mi >= 0 && mi < mm.options.length) {
      return { kind: 'dispatch', text: mm.options[mi].send_text, clear: ['merchant'] }
    }
  }

  if (isRefreshRequest(text) && cand.category) {
    const page = cand.category.page + 1
    return {
      kind: 'dispatch',
      text: `导航去附近的${cand.category.keyword}`,
      metaExtra: { poi_page: String(page) },
      withLocation: true,
      categoryPage: page,
    }
  }

  const wp = cand.waypointChoice
  if (wp && wp.names.length && wp.destination) {
    const idx = poiSelectionIndex(text)
    if (idx >= 0 && idx < wp.names.length) {
      return {
        kind: 'dispatch',
        text: `导航去${wp.destination}途经${wp.names[idx]}`,
        clear: ['waypoint'],
      }
    }
  }

  const choices = cand.destChoice
  if (choices && choices.length) {
    const idx = poiSelectionIndex(text)
    if (idx >= 0 && idx < choices.length) {
      return { kind: 'dispatch', text: choices[idx], clear: ['dest'] }
    }
  }

  const placeItems = cand.placeItems
  if (placeItems && placeItems.length) {
    const idx = ordinalSelectIn(text)
    if (idx >= 0 && idx < placeItems.length) {
      const it = placeItems[idx]
      if (PLACE_NAVIGATE_RE.test(text)) {
        return { kind: 'dispatch', text: `导航去${it.name}` }
      }
      return {
        kind: 'dispatch',
        text: `看${it.name}的详情`,
        ...(it.id ? { metaExtra: { nearby_poi_id: it.id } } : {}),
      }
    }
  }

  const names = cand.poiNames
  if (names && names.length) {
    const idx = poiSelectionIndex(text)
    if (idx >= 0 && idx < names.length) {
      return { kind: 'dispatch', text: `导航去${names[idx]}`, clear: ['poi'] }
    }
  }

  if (shouldRequestLocationConsent(text, ctx.locationEnabled)) {
    return { kind: 'consent', text }
  }

  if (ctx.locationEnabled && isLocationDependent(text)) {
    return { kind: 'dispatch', text, withLocation: true, ...(metaExtra ? { metaExtra } : {}) }
  }

  return { kind: 'dispatch', text, ...(metaExtra ? { metaExtra } : {}) }
}
