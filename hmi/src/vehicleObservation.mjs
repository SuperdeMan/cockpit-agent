// Shared presentation-only projection; no permissions, commands, clocks or platform APIs.
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const id = (value) => typeof value === 'string' && /^[A-Za-z0-9_.:/-]{1,128}$/.test(value)
const copy = (value) => { try { return JSON.parse(JSON.stringify(value)) } catch { return null } }

export function emptyVehicleProjection(legacyVehicleId = 'v1') {
  return { vehicleId: legacyVehicleId, userId: '', epoch: '', revision: 0,
    state: {}, signals: {}, label: '等待车况', legacy: true }
}

export function bindVehicleIdentity(current, frame) {
  if (!object(frame) || frame.type !== 'session_identity' || !id(frame.vehicle_id)
    || !id(frame.vehicle_state_epoch) || typeof frame.user_id !== 'string') return current
  if (current.vehicleId === frame.vehicle_id && current.userId === frame.user_id
    && current.epoch === frame.vehicle_state_epoch) return current
  return { ...emptyVehicleProjection(frame.vehicle_id), userId: frame.user_id,
    epoch: frame.vehicle_state_epoch, legacy: false }
}

export function projectVehicleFrame(current, frame) {
  if (!object(frame) || frame.type !== 'vehicle_state' || !object(frame.state)) return current
  if ('version' in frame || 'observation' in frame) {
    const obs = frame.observation
    if (frame.version !== 2 || !object(obs) || obs.version !== 2
      || !object(obs.state) || !object(obs.signals)
      || !current.epoch || frame.projection_epoch !== current.epoch
      || frame.vehicle_id !== current.vehicleId || obs.vehicle_id !== current.vehicleId
      || !Number.isSafeInteger(frame.revision) || frame.revision < 1 || frame.revision < current.revision) return current
    const state = {}, signals = {}
    for (const [key, metadata] of Object.entries(obs.signals)) {
      if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(key) || !object(metadata)
        || !['good', 'stale', 'uncertain', 'unavailable'].includes(metadata.quality)
        || !['simulated', 'vehicle', 'sandbox'].includes(metadata.source_kind)
        || typeof metadata.authenticated !== 'boolean') continue
      const safeMetadata = copy(metadata)
      if (!object(safeMetadata)) continue
      signals[key] = safeMetadata
      if (metadata.quality === 'good' && Object.prototype.hasOwnProperty.call(obs.state, key) && obs.state[key] !== undefined) state[key] = copy(obs.state[key])
    }
    const kinds = new Set(Object.values(signals).map((s) => s.source_kind))
    const incomplete = Object.values(signals).some((s) => s.quality !== 'good')
    let label = kinds.size === 0 ? '等待车况' : kinds.size === 1 && kinds.has('simulated')
      ? '模拟车况' : kinds.has('simulated') || kinds.has('sandbox') ? '包含模拟数据' : '车辆状态'
    if (incomplete) label += ' · 部分状态待更新'
    return { ...current, state, signals, label, legacy: false, revision: frame.revision }
  }
  // Old frames are only the explicitly supported v1 simulator, never whatever
  // vehicle happens to be selected by the current account.
  if (current.epoch || current.vehicleId !== 'v1' || (frame.vehicle_id && frame.vehicle_id !== 'v1')) return current
  return { ...current, state: copy(frame.state), signals: {}, label: '模拟车况 · 更新时效未知', legacy: true }
}
