// 设置面板：座舱仪表风格的全屏覆盖层，左侧分区导航 + 右侧内容。
// 分区：语音播报 / 语音输入 / 显示主题 / 助手 / 能力开关 / 记忆。
import { useCallback, useEffect, useState } from 'react'
import { useSettings } from '../settings'
import { AGENT_CATALOG, VOICE_FALLBACK, type Voice } from '../types'
import {
  fetchVoices, fetchMemory, fetchMemoryProfile, forgetMemory, fetchPlaces, playTTS,
  type MemoryView, type MemoryProfile, type NamedPlaces,
} from '../audio'
import { PLACE_DEFS, isPlaceSet, formatPlace } from '../places.mjs'
import { Field, Toggle, Segmented, TextInput } from './controls'

type Section = 'tts' | 'asr' | 'display' | 'location' | 'places' | 'assistant' | 'agents' | 'memory'

const SECTIONS: { id: Section; label: string; icon: string }[] = [
  { id: 'tts', label: '语音播报', icon: '🔊' },
  { id: 'asr', label: '语音输入', icon: '🎤' },
  { id: 'display', label: '显示主题', icon: '🎨' },
  { id: 'location', label: '当前位置', icon: '📍' },
  { id: 'places', label: '常用地点', icon: '🏠' },
  { id: 'assistant', label: '助手', icon: '✨' },
  { id: 'agents', label: '能力开关', icon: '🧩' },
  { id: 'memory', label: '记忆', icon: '🧠' },
]

export function SettingsPanel({
  audioApi,
  sessionId,
  location,
  locationEnabled,
  locationStatus,
  onRequestLocation,
  onLocationEnabledChange,
  onClose,
}: {
  audioApi: string
  sessionId: string
  location: { lat: number; lng: number; accuracyM: number; capturedAt: number } | null
  locationEnabled: boolean
  locationStatus: string
  onRequestLocation: () => void
  onLocationEnabledChange: (enabled: boolean) => void
  onClose: () => void
}) {
  const [section, setSection] = useState<Section>('tts')

  return (
    <div className="settings-overlay" role="dialog" aria-modal="true">
      <div className="settings-shell">
        <header className="settings-head">
          <div className="settings-title">
            <span className="gear">⚙</span> 设置
          </div>
          <button className="icon-btn close" onClick={onClose} aria-label="关闭设置">
            ✕
          </button>
        </header>
        <div className="settings-body">
          <nav className="settings-nav">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={'nav-item' + (section === s.id ? ' active' : '')}
                onClick={() => setSection(s.id)}
              >
                <span className="nav-icon">{s.icon}</span>
                <span>{s.label}</span>
              </button>
            ))}
            <div className="nav-spacer" />
            <ResetButton />
          </nav>
          <div className="settings-content">
            {section === 'tts' && <TtsSection audioApi={audioApi} />}
            {section === 'asr' && <AsrSection />}
            {section === 'display' && <DisplaySection />}
            {section === 'location' && <LocationSection
              location={location}
              enabled={locationEnabled}
              status={locationStatus}
              onRequest={onRequestLocation}
              onEnabledChange={onLocationEnabledChange}
            />}
            {section === 'places' && <PlacesSection audioApi={audioApi} />}
            {section === 'assistant' && <AssistantSection />}
            {section === 'agents' && <AgentsSection />}
            {section === 'memory' && <MemorySection audioApi={audioApi} sessionId={sessionId} />}
          </div>
        </div>
      </div>
    </div>
  )
}

function LocationSection({
  location,
  enabled,
  status,
  onRequest,
  onEnabledChange,
}: {
  location: { lat: number; lng: number; accuracyM: number } | null
  enabled: boolean
  status: string
  onRequest: () => void
  onEnabledChange: (enabled: boolean) => void
}) {
  return (
    <SectionCard title="定位权限" desc="启用后，座舱助手会在后续使用时刷新当前位置，用于天气、导航和周边推荐。精确坐标不写入记忆或设置。">
      <Field label="允许使用当前位置" hint="关闭后立即停止发送位置并清除本地坐标">
        <Toggle on={enabled} onChange={onEnabledChange} />
      </Field>
      <Field label="定位状态" hint={location ? `坐标：${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}` : '未获取'}>
        <div className="location-actions">
          <button className="ghost-btn" onClick={onRequest}>{enabled ? '更新当前位置' : '申请并启用'}</button>
        </div>
      </Field>
      <div className={'location-status' + (location ? ' active' : '')}>{status}</div>
      <p className="setting-note">关闭的是座舱助手对位置的使用；如需撤销浏览器级授权，请在浏览器的站点权限中操作。</p>
    </SectionCard>
  )
}

function ResetButton() {
  const { reset } = useSettings()
  const [confirm, setConfirm] = useState(false)
  return confirm ? (
    <div className="reset-confirm">
      <button className="danger-btn" onClick={() => { reset(); setConfirm(false) }}>确认重置</button>
      <button className="ghost-btn" onClick={() => setConfirm(false)}>取消</button>
    </div>
  ) : (
    <button className="ghost-btn reset" onClick={() => setConfirm(true)}>恢复默认设置</button>
  )
}

function SectionCard({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="sec-card">
      <h3 className="sec-title">{title}</h3>
      {desc && <p className="sec-desc">{desc}</p>}
      <div className="sec-fields">{children}</div>
    </section>
  )
}

function TtsSection({ audioApi }: { audioApi: string }) {
  const { settings, update } = useSettings()
  const [voices, setVoices] = useState<Voice[]>(VOICE_FALLBACK)
  const [previewing, setPreviewing] = useState(false)

  useEffect(() => {
    fetchVoices(audioApi)
      .then((v) => { if (v.length) setVoices(v) })
      .catch(() => {/* 离线或服务未起，用内置兜底音色 */})
  }, [audioApi])

  const preview = async (voiceId: string) => {
    setPreviewing(true)
    try {
      await playTTS(audioApi, `你好，我是${settings.assistantName}，这是${voiceId}的声音。`, voiceId)
    } catch {/* ignore */} finally {
      setPreviewing(false)
    }
  }

  return (
    <SectionCard title="语音播报" desc="助手回复的语音合成（TTS）。音色取自 /api/voices，离线时用内置列表。">
      <Field label="启用语音播报" hint="关闭后助手只显示文字">
        <Toggle on={settings.ttsEnabled} onChange={(v) => update({ ttsEnabled: v })} />
      </Field>
      <Field label="回复自动播放" hint="收到回复后自动朗读">
        <Toggle on={settings.autoplay} onChange={(v) => update({ autoplay: v })} />
      </Field>
      <div className="voice-grid">
        {voices.map((v) => (
          <button
            key={v.voice_id}
            className={'voice-card' + (settings.voiceId === v.voice_id ? ' selected' : '')}
            onClick={() => update({ voiceId: v.voice_id })}
          >
            <span className="voice-name">{v.name}</span>
            <span className="voice-tags">{(v.tags || [v.language, v.gender]).join(' · ')}</span>
            <span
              className="voice-play"
              role="button"
              aria-label={`试听 ${v.name}`}
              onClick={(e) => { e.stopPropagation(); preview(v.voice_id) }}
            >
              {previewing ? '◌' : '▸'}
            </span>
          </button>
        ))}
      </div>
    </SectionCard>
  )
}

function AsrSection() {
  const { settings, update } = useSettings()
  return (
    <SectionCard title="语音输入" desc="按住麦克风说话的识别（ASR）行为。">
      <Field label="识别语言">
        <Segmented
          value={settings.asrLanguage}
          onChange={(v) => update({ asrLanguage: v })}
          options={[
            { value: 'zh', label: '中文' },
            { value: 'en', label: '英文' },
            { value: 'auto', label: '自动' },
          ]}
        />
      </Field>
      <Field label="麦克风模式" hint="按住说话：长按录音、松开识别；点按切换：点一下开始、再点结束">
        <Segmented
          value={settings.micMode}
          onChange={(v) => update({ micMode: v })}
          options={[
            { value: 'hold', label: '按住说话' },
            { value: 'toggle', label: '点按切换' },
          ]}
        />
      </Field>
      <Field label="最长聆听时长" hint="超时自动结束录音">
        <Segmented
          value={settings.listenSeconds}
          onChange={(v) => update({ listenSeconds: v })}
          options={[
            { value: 10, label: '10s' },
            { value: 15, label: '15s' },
            { value: 30, label: '30s' },
            { value: 60, label: '60s' },
          ]}
        />
      </Field>
    </SectionCard>
  )
}

function DisplaySection() {
  const { settings, update } = useSettings()
  const [draft, setDraft] = useState(settings.quickCommands.join('\n'))
  useEffect(() => setDraft(settings.quickCommands.join('\n')), [settings.quickCommands])

  const saveQuick = () => {
    const list = draft.split('\n').map((s) => s.trim()).filter(Boolean).slice(0, 12)
    update({ quickCommands: list })
  }

  return (
    <SectionCard title="显示与主题">
      <Field label="主题">
        <Segmented
          value={settings.theme}
          onChange={(v) => update({ theme: v })}
          options={[
            { value: 'dark', label: '深色' },
            { value: 'light', label: '浅色' },
          ]}
        />
      </Field>
      <Field label="字号">
        <Segmented
          value={settings.fontScale}
          onChange={(v) => update({ fontScale: v })}
          options={[
            { value: 'normal', label: '标准' },
            { value: 'large', label: '大字' },
          ]}
        />
      </Field>
      <Field label="大触控模式" hint="行车场景：放大按钮与点击热区">
        <Toggle on={settings.largeTouch} onChange={(v) => update({ largeTouch: v })} />
      </Field>
      <div className="quick-edit">
        <div className="field-label">快捷指令（每行一条，最多 12 条）</div>
        <textarea className="quick-textarea" value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} />
        <button className="ghost-btn" onClick={saveQuick}>保存快捷指令</button>
      </div>
    </SectionCard>
  )
}

function AssistantSection() {
  const { settings, update } = useSettings()
  return (
    <SectionCard
      title="助手"
      desc="昵称即时生效；回答长度与对话模型经会话透传给后端（后端 honor 详见设计文档）。"
    >
      <Field label="助手昵称">
        <TextInput
          value={settings.assistantName}
          onChange={(v) => update({ assistantName: v })}
          placeholder="小舟"
          maxLength={8}
        />
      </Field>
      <Field label="回答长度" hint="简短适合行车收听，详细给更多信息">
        <Segmented
          value={settings.answerLength}
          onChange={(v) => update({ answerLength: v })}
          options={[
            { value: 'short', label: '简短' },
            { value: 'standard', label: '标准' },
            { value: 'detailed', label: '详细' },
          ]}
        />
      </Field>
      <Field label="对话模型" hint="快速模型低延迟，深度推理更聪明但更慢，自动按意图择优">
        <Segmented
          value={settings.model}
          onChange={(v) => update({ model: v })}
          options={[
            { value: 'fast', label: '快速' },
            { value: 'deep', label: '深度推理' },
            { value: 'auto', label: '自动' },
          ]}
        />
      </Field>
    </SectionCard>
  )
}

function AgentsSection() {
  const { settings, toggleAgent } = useSettings()
  return (
    <SectionCard title="能力开关" desc="关闭的能力不参与意图编排（经会话透传，后端按 disabled_agents 过滤）。">
      <div className="agent-list">
        {AGENT_CATALOG.map((a) => (
          <div key={a.id} className="agent-row">
            <span className="agent-icon">{a.icon}</span>
            <div className="agent-text">
              <div className="agent-label">
                {a.label}
                {a.core && <span className="agent-core">核心</span>}
              </div>
              <div className="agent-desc">{a.desc}</div>
            </div>
            <Toggle on={settings.agents[a.id] ?? true} onChange={() => toggleAgent(a.id)} />
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

function PlacesSection({ audioApi }: { audioApi: string }) {
  const [places, setPlaces] = useState<NamedPlaces>({})
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    fetchPlaces(audioApi)
      .then(setPlaces)
      .catch(() => {/* 服务未起/离线 */})
      .finally(() => setLoading(false))
  }, [audioApi])

  useEffect(() => { load() }, [load])

  return (
    <SectionCard
      title="常用地点"
      desc="家、公司等常用目的地。说『我家在XX』『把公司设成XX』即可设置或修改，导航说『回家』『导航去公司』直达；这里实时回显。"
    >
      <div className="mem-head">
        <span className="field-label">已保存地点</span>
        <button className="ghost-btn sm" onClick={load}>{loading ? '刷新中…' : '刷新'}</button>
      </div>
      <ul className="places-list">
        {PLACE_DEFS.map(({ key, label, icon, hint }) => {
          const place = places[key]
          const set = isPlaceSet(place)
          return (
            <li key={key} className={'place-row' + (set ? '' : ' unset')}>
              <span className="place-icon" aria-hidden>{icon}</span>
              <div className="place-main">
                <div className="place-label">{label}</div>
                {set ? (
                  <div className="place-addr">{formatPlace(place)}</div>
                ) : (
                  <div className="place-unset">未设置 · 说『{hint}』即可设置</div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </SectionCard>
  )
}

const _PLACE_LABEL: Record<string, string> = { home: '家', company: '公司', school: '学校' }
const _PROV_LABEL: Record<string, string> = { user_stated: '你说的', agent_inferred: '推断' }
const _EMPTY_PROFILE: MemoryProfile = { preferences: [], places: [], episodes: [] }

function MemorySection({ audioApi, sessionId }: { audioApi: string; sessionId: string }) {
  const { settings, update } = useSettings()
  const [mem, setMem] = useState<MemoryView>({ turns: [] })
  const [profile, setProfile] = useState<MemoryProfile>(_EMPTY_PROFILE)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchMemory(audioApi, sessionId), fetchMemoryProfile(audioApi)])
      .then(([m, p]) => { setMem(m); setProfile(p) })
      .catch(() => {/* 服务未起/离线 */})
      .finally(() => setLoading(false))
  }, [audioApi, sessionId])

  useEffect(() => { load() }, [load])

  const forget = useCallback(async (scope: string) => {
    await forgetMemory(audioApi, 'u1', scope)
    load()
  }, [audioApi, load])

  const clearLocal = () => {
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith('cockpit.') && k !== 'cockpit.settings.v1')
        .forEach((k) => localStorage.removeItem(k))
    } catch {/* ignore */}
  }

  const hasProfile = profile.preferences.length + profile.places.length + profile.episodes.length > 0

  return (
    <SectionCard
      title="记忆"
      desc="助手记住的会话对话，与从交流中学到的偏好/常去地点/经历。可随时删除（云端硬删，不可恢复）。"
    >
      <Field label="启用个性化记忆" hint="记住偏好与历史，提供更贴合的回复（关闭后本轮不读写记忆）">
        <Toggle on={settings.memoryEnabled} onChange={(v) => update({ memoryEnabled: v })} />
      </Field>

      <div className="mem-block">
        <div className="mem-head">
          <span className="field-label">会话对话记忆</span>
          <button className="ghost-btn sm" onClick={load}>{loading ? '刷新中…' : '刷新'}</button>
        </div>
        {mem.turns.length === 0 ? (
          <div className="mem-empty">暂无对话记忆。和助手聊几句后回来看看。</div>
        ) : (
          <ul className="mem-turns">
            {mem.turns.map((t, i) => (
              <li key={i} className={'mem-turn ' + t.role}>
                <span className="mem-role">{t.role === 'user' ? '你' : settings.assistantName}</span>
                <span className="mem-text">{t.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mem-block">
        <div className="mem-head">
          <span className="field-label">助手学到的记忆</span>
          {hasProfile && (
            <button className="ghost-btn sm" onClick={() => forget('')}>清空全部</button>
          )}
        </div>
        {!hasProfile ? (
          <div className="mem-empty">还没记住什么。多聊聊偏好（如「我不吃辣」），助手会慢慢学到。</div>
        ) : (
          <ul className="mem-profile">
            {profile.preferences.map((p, i) => (
              <li key={'pref' + i} className="mem-item">
                <span className="mem-item-text">{p.text}</span>
                <span className="mem-item-meta">{_PROV_LABEL[p.provenance] || p.provenance}</span>
                <button className="mem-del" title="删除这条偏好" onClick={() => forget(p.scope)}>✕</button>
              </li>
            ))}
            {profile.places.map((pl, i) => (
              <li key={'place' + i} className="mem-item">
                <span className="mem-item-text">
                  常去地点 · {_PLACE_LABEL[pl.key] || pl.key}：{pl.name}
                </span>
                <span className="mem-item-meta">高敏</span>
                <button className="mem-del" title="清除常去地点"
                        onClick={() => forget(pl.scope || 'profile.places')}>✕</button>
              </li>
            ))}
            {profile.episodes.map((ep, i) => (
              <li key={'epi' + i} className="mem-item">
                <span className="mem-item-text">📍 {ep.text}</span>
                <span className="mem-item-meta">经历</span>
                <button className="mem-del" title="清除经历"
                        onClick={() => forget('episodic.general')}>✕</button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <Field label="清除本机缓存" hint="清空本地缓存（不含设置项与服务端记忆）">
        <button className="ghost-btn" onClick={clearLocal}>清除本机缓存</button>
      </Field>
    </SectionCard>
  )
}
