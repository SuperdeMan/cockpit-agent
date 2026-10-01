// Shared HMI/Android projection. No control actions, TTS calls, clocks or platform imports.
const object = (x) => x !== null && typeof x === 'object' && !Array.isArray(x)
const strings = (x) => Array.isArray(x) ? x.filter((v) => typeof v === 'string') : []
const text = (x) => typeof x === 'string' ? x : ''
const states = new Set(['inline', 'reference', 'unavailable'])
// CA2-10 evidence vocabulary (runtime/effect_evidence.py). Unknown values drop the whole record.
const acks = new Set(['acknowledged', 'unknown'])
const effectStates = new Set(['satisfied', 'unsatisfied', 'unknown'])
const observations = new Set(['attributed', 'unchanged', 'missing', 'unattributed'])

function evidence(x) {
  if (!object(x) || !acks.has(x.ack) || !effectStates.has(x.state) || !observations.has(x.observed)) return undefined
  // verified is derived, never trusted: an unchanged target is not caused by this step.
  return { ack: x.ack, state: x.state, observed: x.observed,
    verified: x.state === 'satisfied' && x.observed === 'attributed' && x.verified === true,
    reasons: strings(x.reasons), source_kind: text(x.source_kind), authenticated: x.authenticated === true }
}

export function readResultBundles(frame) {
  if (!Array.isArray(frame?.result_bundles) || frame.result_bundles.length > 8) return []
  return frame.result_bundles.filter((b) => object(b) && b.version === 1
    && typeof b.task_id === 'string' && b.task_id.length > 0 && b.task_id.length <= 128
    && Number.isInteger(b.revision) && b.revision > 0 && b.revision < 2 ** 31
    && Array.isArray(b.goals) && b.goals.length <= 32
    && Array.isArray(b.results) && b.results.length <= 128
    && b.results.every((r) => object(r) && typeof r.step_id === 'string'
      && typeof r.answer === 'string' && states.has(r.answer_state)))
    .map((b) => ({
      version: 1, task_id: b.task_id, revision: b.revision,
      goals: b.goals.filter(object).map((g) => ({
        goal_id: text(g.goal_id), origin_exchange_id: text(g.origin_exchange_id),
        source_sha256: text(g.source_sha256), start: g.start, end: g.end, coverage: text(g.coverage),
      })),
      coverage_status: text(b.coverage_status), display_text: text(b.display_text),
      cards: object(b.cards) ? b.cards : {},
      // Whitelist data fields: a tool's action/confirmed properties cannot enter this projection.
      results: b.results.map((r) => ({
        step_id: r.step_id, goal_ids: strings(r.goal_ids), intent: text(r.intent),
        status: text(r.status), answer: r.answer, card_ref: text(r.card_ref),
        operation_id: text(r.operation_id), answer_state: r.answer_state,
        result_ref: text(r.result_ref), verification: text(r.verification),
        pending_edge: r.pending_edge === true,
        ...(evidence(r.evidence) ? { evidence: evidence(r.evidence) } : {}),
      })),
    }))
}

export function projectResultFinal(frame) {
  const bundles = readResultBundles(frame)
  const full = bundles.map((b) => b.display_text).filter(Boolean)
  return { text: full.length ? full.join('\n\n') : text(frame?.speech),
    resultBundles: bundles.length ? bundles : undefined }
}

export function mergeResultMessage(previous, next) {
  const before = readResultBundles({ result_bundles: previous?.resultBundles })
  const after = readResultBundles({ result_bundles: next?.resultBundles })
  if (after.some((b) => before.some((a) => a.task_id === b.task_id && a.revision > b.revision))) return previous
  return { ...previous, ...next, resultBundles: next.resultBundles ?? previous.resultBundles }
}

export function resultCard(bundle, entry, finalCard) {
  const ref = text(entry?.card_ref)
  if (ref.startsWith('bundle:')) {
    const key = ref.slice(7)
    return object(bundle?.cards) && Object.prototype.hasOwnProperty.call(bundle.cards, key) ? bundle.cards[key] : null
  }
  if (!ref.startsWith('final:')) return null
  const path = ref.slice(6)
  if (path && !/^\d+(?:\/\d+)*$/.test(path)) return null
  let card = finalCard
  for (const index of path ? path.split('/') : []) {
    if (card?.type !== 'card_group' || !Array.isArray(card.items)) return null
    card = card.items[Number(index)]
  }
  return object(card) ? card : null
}

export function resultDetails(message) {
  const rows = []
  let extraCard = false
  for (const bundle of readResultBundles({ result_bundles: message?.resultBundles })) {
    for (const entry of bundle.results) {
      // These are completed answer details. Pending confirmation stays in the original UI.
      if (entry.answer_state !== 'inline' || ['need_confirm', 'need_slot'].includes(entry.status)) continue
      // The main card is already rendered above this fold. Only hidden resources
      // belong here; repeating a weather/map card would bury the sibling answer.
      const card = text(entry.card_ref).startsWith('bundle:') ? resultCard(bundle, entry, message.uiCard) : null
      extraCard ||= !!card
      if (entry.answer || card) rows.push({ key: entry.result_ref, answer: entry.answer, card })
    }
  }
  if (rows.length === 1 && !extraCard && text(message?.text).includes(rows[0].answer)) return []
  return rows
}
