// 设置面板（P4 · A-7「横屏侧栏式」忠实重建）：玻璃覆盖层 = 顶栏 + 左 236px 导航 + 右内容滚动区。
// 八分区：语音播报 / 语音输入 / 显示主题 / 当前位置 / 常用地点 / 助手 / 能力开关 / 记忆。
// 视觉照 Figma Make A-7（inline 样式 + --au-* token，复用 AuroraOrb / 控件库）；
// 数据/交互一字不改地沿用既有真实接线（useSettings / 音色试听 / 地点 / 记忆 / 定位）。
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { useSettings } from '../settings'
import { AGENT_CATALOG, VOICE_FALLBACK, WAKE_WORD_PRESETS, type Voice } from '../types'
import {
  fetchVoices, fetchMemory, fetchMemoryProfile, forgetMemory, fetchPlaces, playTTS,
  type MemoryView, type MemoryProfile, type NamedPlaces,
} from '../audio'
import { PLACE_DEFS, isPlaceSet, formatPlace } from '../places.mjs'
import { AuroraOrb } from './aurora'
import { Icon, type IconName } from './Icon'
import { Toggle, Segmented, TextInput, GhostBtn, DangerBtn } from './controls'

const TEAL = 'var(--au-primary)'
const FG1 = 'var(--au-text)'
const FG2 = 'var(--au-text-2)'
const FG3 = 'var(--au-text-3)'
const DIV = 'var(--au-line)'
const MONO = 'var(--au-font-mono)'

// ─── 内联线性图标 ───
function Svg({ size = 14, color = 'currentColor', sw = 2, style, children }: { size?: number; color?: string; sw?: number; style?: CSSProperties; children: ReactNode }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, ...style }}>{children}</svg>
}
const IcX = (p: { size?: number; color?: string }) => <Svg {...p}><path d="M18 6 6 18M6 6l12 12" /></Svg>
const IcPlus = (p: { size?: number; color?: string }) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>
const IcChevR = (p: { size?: number; color?: string }) => <Svg {...p}><path d="m9 18 6-6-6-6" /></Svg>
const IcCheck = (p: { size?: number; color?: string }) => <Svg {...p}><path d="M20 6 9 17l-5-5" /></Svg>
const IcPencil = (p: { size?: number; color?: string }) => <Svg {...p}><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></Svg>

type Section = 'tts' | 'asr' | 'display' | 'location' | 'places' | 'assistant' | 'agents' | 'memory'
const SECTIONS: { id: Section; label: string; icon: IconName }[] = [
  { id: 'tts', label: '语音播报', icon: 'voice-output' },
  { id: 'asr', label: '语音输入', icon: 'voice-input' },
  { id: 'display', label: '显示主题', icon: 'theme' },
  { id: 'location', label: '当前位置', icon: 'location' },
  { id: 'places', label: '常用地点', icon: 'place-home' },
  { id: 'assistant', label: '助手设置', icon: 'assistant' },
  { id: 'agents', label: '能力开关', icon: 'capability' },
  { id: 'memory', label: '记忆', icon: 'memory' },
]

// 玻璃容器（照 A-7 GlassCard，r=20）
function Glass({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{
      borderRadius: 20, overflow: 'hidden', background: 'var(--au-glass-bg)',
      WebkitBackdropFilter: 'blur(var(--au-glass-blur)) saturate(1.15)', backdropFilter: 'blur(var(--au-glass-blur)) saturate(1.15)',
      border: '1px solid var(--au-fill-2)', borderTop: '1px solid var(--au-glass-bd-top)', borderLeft: '1px solid var(--au-glass-bd-left)',
      boxShadow: 'var(--au-glass-shadow)', ...style,
    }}>{children}</div>
  )
}
const HR = () => <div style={{ height: 1, background: DIV }} />

// ─── 布局原语（照 A-7）───
function NavItem({ icon, label, active, onClick }: { icon: IconName; label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      width: '100%', padding: '10px 12px', borderRadius: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, marginBottom: 2,
      background: active ? 'rgba(70,214,224,.10)' : 'transparent', border: `1px solid ${active ? 'rgba(70,214,224,.22)' : 'transparent'}`,
      color: active ? TEAL : FG2, textAlign: 'left', transition: 'all .18s', fontFamily: 'inherit',
    }}>
      <Icon name={icon} size={18} state={active ? 'active' : 'default'} />
      <span style={{ fontSize: 13, fontWeight: active ? 600 : 400, flex: 1 }}>{label}</span>
      {active && <IcChevR size={13} color={TEAL} />}
    </button>
  )
}
function SectionHdr({ icon, title, sub }: { icon: IconName; title: string; sub?: string }) {
  return (
    <div style={{ padding: '22px 28px 16px', borderBottom: `1px solid ${DIV}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: sub ? 5 : 0 }}>
        <Icon name={icon} size={20} state="active" />
        <h2 style={{ fontSize: 17, fontWeight: 600, margin: 0 }}>{title}</h2>
      </div>
      {sub && <div style={{ fontSize: 12.5, color: FG3, paddingLeft: 30, lineHeight: 1.6 }}>{sub}</div>}
    </div>
  )
}
function SettingGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ padding: '14px 28px 6px', fontSize: 10.5, fontWeight: 600, letterSpacing: '.09em', textTransform: 'uppercase', color: FG3 }}>{title}</div>
      <div style={{ padding: '0 28px' }}>{children}</div>
    </div>
  )
}
function SettingRow({ label, sub, children, noBorder = false }: { label: string; sub?: string; children?: ReactNode; noBorder?: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '15px 0', borderBottom: noBorder ? 'none' : `1px solid ${DIV}` }}>
      <div style={{ flex: 1, paddingRight: 20, minWidth: 0 }}>
        <div style={{ fontSize: 14, color: FG1 }}>{label}</div>
        {sub && <div style={{ fontSize: 12, color: FG3, marginTop: 3, lineHeight: 1.55 }}>{sub}</div>}
      </div>
      {children && <div style={{ flexShrink: 0 }}>{children}</div>}
    </div>
  )
}

export function SettingsPanel({
  audioApi, sessionId, location, locationEnabled, locationStatus, onRequestLocation, onLocationEnabledChange, onClose,
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
  const { settings } = useSettings()
  // 初始分区默认「语音播报」；`?settings=<id>` 可直达某分区（本地验证用，prod 无参即默认）。
  const initial = SECTIONS.find((s) => s.id === new URLSearchParams(typeof window !== 'undefined' ? window.location.search : '').get('settings'))?.id ?? 'tts'
  const [section, setSection] = useState<Section>(initial)

  return (
    <div className="au-settings-overlay" role="dialog" aria-modal="true">
      {/* 氛围底 */}
      <div aria-hidden style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 0, overflow: 'hidden' }}>
        <span style={{ position: 'absolute', bottom: '8%', left: '14%', width: 560, height: 400, borderRadius: '50%', background: 'radial-gradient(circle,rgba(91,140,255,.10),transparent 68%)', filter: 'blur(52px)' }} />
        <span style={{ position: 'absolute', top: '6%', right: '8%', width: 480, height: 360, borderRadius: '50%', background: 'radial-gradient(circle,rgba(91,233,255,.07),transparent 68%)', filter: 'blur(58px)' }} />
      </div>

      {/* 顶栏 */}
      <header style={{ position: 'relative', zIndex: 2, height: 64, padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${DIV}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <AuroraOrb size={28} state="idle" />
          <span className="au-aurora-text" style={{ fontSize: 15, fontWeight: 600 }}>设置</span>
          <span style={{ fontSize: 13, color: FG3, fontWeight: 300 }}>· {settings.assistantName}助手 · 横屏侧栏</span>
        </div>
        <button onClick={onClose} aria-label="关闭设置" style={{ width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', cursor: 'pointer', background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', color: FG2 }}>
          <IcX size={16} />
        </button>
      </header>

      {/* 主区：侧栏 + 内容 */}
      <div style={{ position: 'relative', zIndex: 2, display: 'flex', height: 'calc(100vh - 64px)', padding: '16px 24px', gap: 16, overflow: 'hidden' }}>
        {/* 左导航 */}
        <div style={{ width: 236, flexShrink: 0, height: '100%' }}>
          <Glass style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '20px 16px 14px', display: 'flex', alignItems: 'center', gap: 12 }}>
              <AuroraOrb size={40} state="idle" />
              <div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{settings.assistantName}</div>
                <div style={{ fontSize: 11, color: FG3 }}>助手设置</div>
              </div>
            </div>
            <HR />
            <div style={{ flex: 1, overflowY: 'auto', padding: '8px 10px' }}>
              {SECTIONS.map((s) => (
                <NavItem key={s.id} icon={s.icon} label={s.label} active={section === s.id} onClick={() => setSection(s.id)} />
              ))}
            </div>
            <HR />
            <div style={{ padding: '10px 10px 12px' }}><ResetButton /></div>
          </Glass>
        </div>

        {/* 右内容 */}
        <div style={{ flex: 1, height: '100%', overflowY: 'auto' }}>
          <Glass style={{ minHeight: '100%' }}>
            {section === 'tts' && <TtsSection audioApi={audioApi} />}
            {section === 'asr' && <AsrSection />}
            {section === 'display' && <DisplaySection />}
            {section === 'location' && <LocationSection location={location} enabled={locationEnabled} status={locationStatus} onRequest={onRequestLocation} onEnabledChange={onLocationEnabledChange} />}
            {section === 'places' && <PlacesSection audioApi={audioApi} />}
            {section === 'assistant' && <AssistantSection />}
            {section === 'agents' && <AgentsSection />}
            {section === 'memory' && <MemorySection audioApi={audioApi} sessionId={sessionId} />}
          </Glass>
        </div>
      </div>
    </div>
  )
}

function ResetButton() {
  const { reset } = useSettings()
  const [confirm, setConfirm] = useState(false)
  if (confirm) {
    return (
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => { reset(); setConfirm(false) }} style={{ flex: 1, padding: '9px 0', borderRadius: 12, border: '1px solid rgba(239,68,68,.28)', background: 'rgba(239,68,68,.06)', color: 'var(--au-danger)', fontSize: 12.5, cursor: 'pointer', fontFamily: 'inherit' }}>确认重置</button>
        <GhostBtn sm onClick={() => setConfirm(false)}>取消</GhostBtn>
      </div>
    )
  }
  return <DangerBtn onClick={() => setConfirm(true)}>恢复默认设置</DangerBtn>
}

// ─── 1 · 语音播报 ───
const VOICE_PALETTE = ['#5BE9FF', '#34D399', '#A3E635', '#9A6BFF', '#FF6BD6', '#5B8CFF', '#46D6E0', '#FCD34D', '#FB923C']
// 音色 → A-8 人格图标；非六大人格（Milo/Dean/MiMo 等）回落 voice-soda（气泡，中性）
const VOICE_ICON: Record<string, IconName> = {
  冰糖: 'voice-ice', 茉莉: 'voice-jasmine', 苏打: 'voice-soda', 白桦: 'voice-birch', Mia: 'voice-mia', Chloe: 'voice-chloe',
}
function voiceIcon(v: Voice): IconName { return VOICE_ICON[v.voice_id] ?? VOICE_ICON[v.name] ?? 'voice-soda' }
// Agent → 图标（A-8 集未含，icons.custom 补；端侧快系统车控/媒体用 vehicle/media）
const AGENT_ICON: Record<string, IconName> = {
  vehicle: 'vehicle', media: 'media', navigation: 'compass', info: 'info', 'trip-planner': 'itinerary',
  'deep-research': 'research', 'food-ordering': 'dining', 'parking-payment': 'parking', 'manual-rag': 'manual', chitchat: 'chat',
}
// 常用地点 → 图标（家=A-8 place-home；公司/学校 icons.custom 补）
const PLACE_ICON: Record<string, IconName> = { home: 'place-home', company: 'building', school: 'school' }

function TtsSection({ audioApi }: { audioApi: string }) {
  const { settings, update } = useSettings()
  const [voices, setVoices] = useState<Voice[]>(VOICE_FALLBACK)
  const [playing, setPlaying] = useState<string | null>(null)

  useEffect(() => {
    fetchVoices(audioApi).then((v) => { if (v.length) setVoices(v) }).catch(() => {/* 离线兜底 */})
  }, [audioApi])

  const preview = async (voiceId: string) => {
    setPlaying(voiceId)
    try { await playTTS(audioApi, `你好，我是${settings.assistantName}，这是${voiceId}的声音。`, voiceId) }
    catch {/* ignore */} finally { setPlaying(null) }
  }

  return (
    <div>
      <SectionHdr icon="voice-output" title="语音播报" sub="控制助手的语音输出方式与音色偏好" />
      <SettingGroup title="输出控制">
        <SettingRow label="启用语音播报" sub="关闭后助手仅显示文字，不朗读回答">
          <Toggle on={settings.ttsEnabled} onChange={(v) => update({ ttsEnabled: v })} />
        </SettingRow>
        <SettingRow label="自动播放回答" sub="收到回答后立即朗读，无需手动点击" noBorder>
          <Toggle on={settings.autoplay} onChange={(v) => update({ autoplay: v })} disabled={!settings.ttsEnabled} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="音色选择">
        <div style={{ paddingBottom: 20 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, paddingTop: 10 }}>
            {voices.map((v, i) => {
              const color = VOICE_PALETTE[i % VOICE_PALETTE.length]
              const selected = settings.voiceId === v.voice_id
              const isPlaying = playing === v.voice_id
              const disabled = !settings.ttsEnabled
              return (
                <div key={v.voice_id} onClick={() => !disabled && update({ voiceId: v.voice_id })} style={{
                  padding: '14px 12px', borderRadius: 16, cursor: disabled ? 'default' : 'pointer',
                  background: selected ? `${color}14` : 'var(--au-fill)',
                  border: `1px solid ${selected ? color + '50' : 'var(--au-fill-2)'}`, borderTop: `1px solid ${selected ? color + '70' : 'var(--au-line-2)'}`,
                  transition: 'all .2s', opacity: disabled ? 0.45 : 1, boxShadow: selected ? `0 0 18px ${color}18` : 'none',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Icon name={voiceIcon(v)} size={20} color={color} />
                    {selected && <div style={{ width: 16, height: 16, borderRadius: '50%', background: color, display: 'grid', placeItems: 'center' }}><IcCheck size={9} color="#06080F" /></div>}
                  </div>
                  <div style={{ fontSize: 13.5, fontWeight: 600, color: selected ? color : FG1, marginBottom: 2 }}>{v.name}</div>
                  <div style={{ fontSize: 11, color: FG3, marginBottom: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.description || (v.tags || [v.language, v.gender]).join(' · ')}</div>
                  <button onClick={(e) => { e.stopPropagation(); !disabled && preview(v.voice_id) }} aria-label={`试听 ${v.name}`} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 8,
                    background: isPlaying ? `${color}20` : 'var(--au-fill)', border: `1px solid ${isPlaying ? color + '40' : 'var(--au-fill-2)'}`,
                    fontSize: 11.5, color: isPlaying ? color : FG3, cursor: disabled ? 'default' : 'pointer', fontFamily: 'inherit', transition: 'all .18s',
                  }}>
                    {isPlaying ? <span style={{ width: 10, height: 10, border: `1.5px solid ${color}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'au-orb-spin .9s linear infinite' }} /> : <Icon name="play" size={11} color={isPlaying || !disabled ? color : 'var(--au-text-3)'} />}
                    {isPlaying ? '播放中…' : '试听'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 2 · 语音输入 ───
function AsrSection() {
  const { settings, update } = useSettings()
  const isDash = settings.asrProvider === 'dashscope'
  return (
    <div>
      <SectionHdr icon="voice-input" title="语音输入" sub="配置识别引擎、语言、模式与时长" />
      <SettingGroup title="实时识别引擎（流式上屏）">
        <SettingRow label="识别服务商" sub="实时=边说边上屏（DashScope 百炼）；分块=经典 MiMo；关闭=录完再出">
          <Segmented value={settings.asrProvider} onChange={(v) => update({ asrProvider: v })}
            options={[{ value: 'dashscope', label: '实时' }, { value: 'mimo', label: '分块' }, { value: 'off', label: '关闭' }]} />
        </SettingRow>
        <SettingRow label="识别模型" sub={isDash ? '实时 ASR 模型（同一把百炼 key）' : '分块模式用 MiMo 批 ASR，无需选模型'} noBorder>
          {isDash ? (
            <Segmented sm value={settings.asrModel} onChange={(v) => update({ asrModel: v })}
              options={[{ value: 'qwen3-asr-flash-realtime-2026-02-10', label: 'Qwen3-ASR' }, { value: 'fun-asr-realtime', label: 'Fun-ASR' }]} />
          ) : (
            <span style={{ fontSize: 13, color: 'var(--au-text-3)' }}>—</span>
          )}
        </SettingRow>
      </SettingGroup>
      <SettingGroup title="识别设置">
        <SettingRow label="识别语言" sub="选择主要识别语言">
          <Segmented value={settings.asrLanguage} onChange={(v) => update({ asrLanguage: v })}
            options={[{ value: 'zh', label: '中文' }, { value: 'en', label: '英文' }, { value: 'auto', label: '自动' }]} />
        </SettingRow>
        <SettingRow label="麦克风模式" sub="按住说话或单次点击激活">
          <Segmented value={settings.micMode} onChange={(v) => update({ micMode: v })}
            options={[{ value: 'hold', label: '按住' }, { value: 'toggle', label: '点按' }]} />
        </SettingRow>
        <SettingRow label="最长聆听时长" sub="超时后自动停止录音" noBorder>
          <Segmented sm value={settings.listenSeconds} onChange={(v) => update({ listenSeconds: v })}
            options={[{ value: 10, label: '10s' }, { value: 15, label: '15s' }, { value: 30, label: '30s' }, { value: 60, label: '60s' }]} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="语音唤醒 · 连续对话">
        <SettingRow label="免唤醒连续对话" sub="回复播完后保持聆听窗，接着说即自动断句发送，无需再按光球。说「退下吧 / 没事了」可随时退出聆听。唤醒前音频仅在浏览器本地检测、不上传。默认关。">
          <Toggle on={settings.handsFree} onChange={(v) => update({ handsFree: v })} />
        </SettingRow>
        <SettingRow label="唤醒词" sub="待机时说唤醒词进入聆听，全程免触屏。需先下载本地语音模型（见 README 的 fetch-voice-models）。" noBorder={!settings.handsFree}>
          <Toggle on={settings.wakeWordEnabled} onChange={(v) => update({ wakeWordEnabled: v })} disabled={!settings.handsFree} />
        </SettingRow>
        {settings.handsFree && settings.wakeWordEnabled && (
          <SettingRow label="选择唤醒词" sub="换词后直接说新唤醒词即可生效；命中率以真机为准">
            <Segmented sm value={settings.wakeWord} onChange={(v) => update({ wakeWord: v })}
              options={WAKE_WORD_PRESETS.map((p) => ({ value: p.word, label: p.word }))} />
          </SettingRow>
        )}
        {settings.handsFree && (
          <>
            <SettingRow label="续问聆听窗" sub="回复播完后等待你接话的时长">
              <Segmented sm value={settings.followupWindowS} onChange={(v) => update({ followupWindowS: v })}
                options={[{ value: 5, label: '5s' }, { value: 8, label: '8s' }, { value: 15, label: '15s' }]} />
            </SettingRow>
            <SettingRow label="静音断句" sub="停顿多久判定说完并发送（VAD 静音尾）：0.5s 敏捷 / 0.8s 均衡 / 1.2s 从容。长句易停顿可调大。" noBorder>
              <Segmented sm value={settings.silenceTailMs} onChange={(v) => update({ silenceTailMs: v })}
                options={[{ value: 500, label: '0.5s 敏捷' }, { value: 800, label: '0.8s 均衡' }, { value: 1200, label: '1.2s 从容' }]} />
            </SettingRow>
          </>
        )}
      </SettingGroup>
    </div>
  )
}

// ─── 3 · 显示主题 ───
function DisplaySection() {
  const { settings, update } = useSettings()
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')

  const removeCmd = (i: number) => update({ quickCommands: settings.quickCommands.filter((_, j) => j !== i) })
  const addCmd = () => {
    const t = draft.trim()
    if (t && settings.quickCommands.length < 8) update({ quickCommands: [...settings.quickCommands, t] })
    setDraft(''); setAdding(false)
  }

  return (
    <div>
      <SectionHdr icon="theme" title="显示主题" sub="界面外观、字号与快捷指令定制" />
      <SettingGroup title="外观">
        <SettingRow label="主题" sub="深色适合夜间驾驶，浅色适合晴天">
          <Segmented value={settings.theme} onChange={(v) => update({ theme: v })}
            options={[{ value: 'dark', label: '深色' }, { value: 'light', label: '浅色' }]} />
        </SettingRow>
        <SettingRow label="字号" sub="大字模式放大所有文本，提升行车可读性">
          <Segmented value={settings.fontScale} onChange={(v) => update({ fontScale: v })}
            options={[{ value: 'normal', label: '标准' }, { value: 'large', label: '大字' }]} />
        </SettingRow>
        <SettingRow label="大触控模式" sub="行车时放大按钮与点击热区至 56px（§11 车规）" noBorder>
          <Toggle on={settings.largeTouch} onChange={(v) => update({ largeTouch: v })} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="快捷指令">
        <div style={{ paddingTop: 10, paddingBottom: 16 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
            {settings.quickCommands.map((cmd, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 20, background: 'var(--au-fill)', border: '1px solid var(--au-line-2)' }}>
                <span style={{ fontSize: 12.5, color: FG2 }}>{cmd}</span>
                <button onClick={() => removeCmd(i)} aria-label="删除指令" style={{ cursor: 'pointer', background: 'none', border: 'none', padding: 0, display: 'flex', lineHeight: 1 }}>
                  <IcX size={11} color={FG3} />
                </button>
              </div>
            ))}
            {adding ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addCmd(); if (e.key === 'Escape') { setDraft(''); setAdding(false) } }}
                  placeholder="新指令…" maxLength={16}
                  style={{ width: 130, height: 30, padding: '0 10px', borderRadius: 20, background: 'var(--au-fill)', border: `1px solid ${TEAL}`, color: FG1, fontSize: 12.5, fontFamily: 'inherit', outline: 'none', caretColor: TEAL }} />
                <button onClick={addCmd} aria-label="确认添加" style={{ width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', cursor: 'pointer', background: 'rgba(70,214,224,.14)', border: `1px solid ${TEAL}`, color: TEAL }}><IcCheck size={13} color={TEAL} /></button>
              </span>
            ) : settings.quickCommands.length < 8 ? (
              <button onClick={() => setAdding(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 20, border: '1px dashed var(--au-text-3)', background: 'transparent', color: FG3, fontSize: 12.5, cursor: 'pointer', fontFamily: 'inherit' }}>
                <IcPlus size={11} color={FG3} />添加
              </button>
            ) : null}
          </div>
          <div style={{ fontSize: 11, color: FG3 }}>最多 8 条 · 显示在输入框上方的指令轨</div>
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 4 · 当前位置 ───
function relTime(ts?: number): string {
  if (!ts) return '—'
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  return s < 60 ? `${s}s前` : s < 3600 ? `${Math.round(s / 60)}分前` : `${Math.round(s / 3600)}小时前`
}
function LocationSection({ location, enabled, status, onRequest, onEnabledChange }: {
  location: { lat: number; lng: number; accuracyM: number; capturedAt: number } | null
  enabled: boolean; status: string; onRequest: () => void; onEnabledChange: (e: boolean) => void
}) {
  return (
    <div>
      <SectionHdr icon="location" title="当前位置" sub="位置权限与精度设置。精确坐标仅用于导航/就近/天气，不上传服务器、不写入记忆。" />
      <SettingGroup title="权限">
        <SettingRow label="启用位置服务" sub="关闭后立即停止发送位置并清除本地坐标，导航/充电站等将不可用" noBorder>
          <Toggle on={enabled} onChange={onEnabledChange} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="当前位置">
        <div style={{ paddingTop: 10, paddingBottom: 16 }}>
          {enabled && location ? (
            <div style={{ padding: '14px 16px', borderRadius: 14, background: 'var(--au-fill)', border: '1px solid var(--au-fill-2)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--au-online)', boxShadow: '0 0 6px var(--au-online)' }} />
                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--au-online)' }}>定位已开启</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[['纬度', `${location.lat.toFixed(4)}° N`], ['经度', `${location.lng.toFixed(4)}° E`], ['精度', `±${Math.round(location.accuracyM)}m`], ['更新', relTime(location.capturedAt)]].map(([l, v]) => (
                  <div key={l}>
                    <div style={{ fontSize: 10.5, color: FG3 }}>{l}</div>
                    <div style={{ fontFamily: MONO, fontSize: 12.5, color: FG1, marginTop: 2 }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 0' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: FG3 }} />
              <span style={{ fontSize: 13, color: FG3 }}>{enabled ? '定位已开启，尚未获取坐标' : '位置服务已关闭'}</span>
            </div>
          )}
          <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
            <GhostBtn onClick={onRequest}>{enabled ? '更新当前位置' : '申请并启用'}</GhostBtn>
            <span style={{ fontSize: 11.5, color: FG3, flex: 1, lineHeight: 1.5 }}>{status}</span>
          </div>
          <div style={{ fontSize: 11, color: FG3, marginTop: 12, lineHeight: 1.6 }}>关闭的是座舱助手对位置的使用；如需撤销浏览器级授权，请在浏览器站点权限中操作。</div>
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 5 · 常用地点 ───
function PlacesSection({ audioApi }: { audioApi: string }) {
  const [places, setPlaces] = useState<NamedPlaces>({})
  const [loading, setLoading] = useState(false)
  const load = useCallback(() => {
    setLoading(true)
    fetchPlaces(audioApi).then(setPlaces).catch(() => {/* 离线 */}).finally(() => setLoading(false))
  }, [audioApi])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <SectionHdr icon="place-home" title="常用地点" sub="家、公司等常用目的地。说『我家在XX』『把公司设成XX』设置，导航说『回家』『导航去公司』直达。" />
      <SettingGroup title="地点设置">
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 8 }}>
          <GhostBtn sm onClick={load}>{loading ? '刷新中…' : '刷新'}</GhostBtn>
        </div>
        {PLACE_DEFS.map(({ key, label, icon, hint }: { key: string; label: string; icon: string; hint: string }, i: number) => {
          const place = places[key]
          const set = isPlaceSet(place)
          return (
            <div key={key} style={{ padding: '16px 0', borderBottom: i < PLACE_DEFS.length - 1 ? `1px solid ${DIV}` : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: set ? 'rgba(70,214,224,.12)' : 'var(--au-fill)', border: `1px solid ${set ? 'rgba(70,214,224,.25)' : 'var(--au-line-2)'}`, display: 'grid', placeItems: 'center', flexShrink: 0 }}><Icon name={PLACE_ICON[key] ?? 'pin'} size={18} color={set ? 'var(--au-primary)' : 'var(--au-text-2)'} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600, color: FG1, marginBottom: 4 }}>{label}</div>
                  {set ? (
                    <div style={{ fontSize: 12.5, color: FG2 }}>{formatPlace(place)}</div>
                  ) : (
                    <div style={{ fontSize: 12.5, color: FG3 }}>未设置 · 说『{hint}』即可设置</div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </SettingGroup>
    </div>
  )
}

// ─── 6 · 助手设置 ───
function AssistantSection() {
  const { settings, update } = useSettings()
  const models = [
    { value: 'fast' as const, name: '快速', desc: '低延迟，适合导航/天气/音乐等实时任务', latency: '<0.5s' },
    { value: 'deep' as const, name: '深度推理', desc: '复杂推理，适合行程规划、调研报告', latency: '1-3s' },
    { value: 'auto' as const, name: '自动', desc: '根据任务复杂度智能切换，推荐日常使用', latency: '智能' },
  ]
  return (
    <div>
      <SectionHdr icon="assistant" title="助手设置" sub={`个性化${settings.assistantName}的回答风格与底层模型（昵称即时生效，长度/模型经会话透传后端）`} />
      <SettingGroup title="个性化">
        <SettingRow label="助手昵称" sub="你对助手的称呼（显示用；唤醒词在语音设置中单独选择）">
          <TextInput value={settings.assistantName} onChange={(v) => update({ assistantName: v })} placeholder="小舟" maxLength={8} width={180} />
        </SettingRow>
        <SettingRow label="回答长度" sub="简短适合行车；详细适合泊车深度调研">
          <Segmented value={settings.answerLength} onChange={(v) => update({ answerLength: v })}
            options={[{ value: 'short', label: '简短' }, { value: 'standard', label: '标准' }, { value: 'detailed', label: '详细' }]} />
        </SettingRow>
        <SettingRow label="对话模型" sub="快速 = 低延迟；深度推理 = 复杂任务更准" noBorder>
          <Segmented sm value={settings.model} onChange={(v) => update({ model: v })}
            options={[{ value: 'fast', label: '快速' }, { value: 'deep', label: '深度推理' }, { value: 'auto', label: '自动' }]} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="模型说明">
        <div style={{ paddingTop: 8, paddingBottom: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {models.map((m) => {
            const on = settings.model === m.value
            return (
              <div key={m.value} style={{ display: 'flex', gap: 12, padding: '10px 14px', borderRadius: 12, background: on ? 'rgba(70,214,224,.07)' : 'var(--au-fill)', border: `1px solid ${on ? 'rgba(70,214,224,.20)' : 'var(--au-fill)'}` }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: on ? TEAL : FG2, marginBottom: 2 }}>{m.name}</div>
                  <div style={{ fontSize: 11.5, color: FG3 }}>{m.desc}</div>
                </div>
                <span style={{ fontFamily: MONO, fontSize: 11, color: FG3, flexShrink: 0, marginTop: 2 }}>{m.latency}</span>
              </div>
            )
          })}
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 7 · 能力开关 ───
function AgentsSection() {
  const { settings, toggleAgent } = useSettings()
  return (
    <div>
      <SectionHdr icon="capability" title="能力开关" sub="控制各 Agent 的启用状态。核心能力不可关闭（经会话透传，后端按 disabled_agents 过滤）。" />
      <SettingGroup title="Agent 列表">
        <div style={{ paddingBottom: 8 }}>
          {AGENT_CATALOG.map((a, i) => {
            const on = settings.agents[a.id] ?? true
            return (
              <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 0', borderBottom: i < AGENT_CATALOG.length - 1 ? `1px solid ${DIV}` : 'none' }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: on ? 'rgba(70,214,224,.10)' : 'var(--au-fill)', border: `1px solid ${on ? 'rgba(70,214,224,.22)' : 'var(--au-line-2)'}`, display: 'grid', placeItems: 'center', flexShrink: 0, transition: 'all .2s' }}><Icon name={AGENT_ICON[a.id] ?? 'info'} size={19} state={on ? 'active' : 'default'} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, color: on ? FG1 : FG3, transition: 'color .2s' }}>{a.label}</div>
                  <div style={{ fontSize: 11.5, color: FG3, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.desc}</div>
                </div>
                {a.core && <span style={{ padding: '2px 8px', borderRadius: 6, background: 'rgba(70,214,224,.10)', border: '1px solid rgba(70,214,224,.22)', fontSize: 10, fontWeight: 700, color: TEAL, flexShrink: 0 }}>核心</span>}
                <Toggle on={on} onChange={() => !a.core && toggleAgent(a.id)} disabled={a.core} />
              </div>
            )
          })}
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 8 · 记忆 ───
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
      .catch(() => {/* 离线 */}).finally(() => setLoading(false))
  }, [audioApi, sessionId])
  useEffect(() => { load() }, [load])

  const forget = useCallback(async (scope: string) => { await forgetMemory(audioApi, 'u1', scope); load() }, [audioApi, load])
  const clearLocal = () => {
    try { Object.keys(localStorage).filter((k) => k.startsWith('cockpit.') && k !== 'cockpit.settings.v1').forEach((k) => localStorage.removeItem(k)) } catch {/* ignore */}
  }
  const hasProfile = profile.preferences.length + profile.places.length + profile.episodes.length > 0

  return (
    <div>
      <SectionHdr icon="memory" title="记忆" sub={`${settings.assistantName}记住的会话对话，与从交流中学到的偏好/常去地点/经历（云端硬删，不可恢复）`} />
      <SettingGroup title="记忆开关">
        <SettingRow label="启用个性化记忆" sub="记住偏好与历史以贴合回复；关闭后本轮不读写记忆，已有记忆保留" noBorder>
          <Toggle on={settings.memoryEnabled} onChange={(v) => update({ memoryEnabled: v })} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="会话对话记忆">
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 8 }}>
          <GhostBtn sm onClick={load}>{loading ? '刷新中…' : '刷新'}</GhostBtn>
        </div>
        <div style={{ paddingBottom: 8 }}>
          {mem.turns.length === 0 ? (
            <div style={{ padding: '14px 0', fontSize: 13, color: FG3 }}>暂无对话记忆。和助手聊几句后回来看看。</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 4 }}>
              {mem.turns.map((t, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 12px', borderRadius: 10, background: 'var(--au-fill)', border: '1px solid var(--au-fill)' }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: t.role === 'user' ? FG2 : TEAL, flexShrink: 0, width: 32 }}>{t.role === 'user' ? '你' : settings.assistantName}</span>
                  <span style={{ flex: 1, fontSize: 12.5, color: FG2, lineHeight: 1.55 }}>{t.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </SettingGroup>
      <HR />
      <SettingGroup title="学到的画像">
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 8 }}>
          {hasProfile && <GhostBtn sm onClick={() => forget('')}><IcX size={10} /> 清空全部</GhostBtn>}
        </div>
        <div style={{ paddingBottom: 16 }}>
          {!hasProfile ? (
            <div style={{ padding: '14px 0', fontSize: 13, color: FG3 }}>还没记住什么。多聊聊偏好（如「我不吃辣」），助手会慢慢学到。</div>
          ) : (
            <>
              {profile.preferences.length > 0 && <MemCat title="偏好" items={profile.preferences.map((p) => ({ text: p.text, meta: _PROV_LABEL[p.provenance] || p.provenance, onDel: () => forget(p.scope) }))} />}
              {profile.places.length > 0 && <MemCat title="常去地点" items={profile.places.map((pl) => ({ text: `${_PLACE_LABEL[pl.key] || pl.key}：${pl.name}`, meta: '高敏', onDel: () => forget(pl.scope || 'profile.places') }))} />}
              {profile.episodes.length > 0 && <MemCat title="经历" items={profile.episodes.map((ep) => ({ text: `📍 ${ep.text}`, meta: '经历', onDel: () => forget('episodic.general') }))} />}
            </>
          )}
        </div>
      </SettingGroup>
      <HR />
      <div style={{ padding: '14px 28px', display: 'flex', gap: 12, alignItems: 'center' }}>
        <GhostBtn onClick={clearLocal}>清除本机缓存</GhostBtn>
        <span style={{ fontSize: 11.5, color: FG3 }}>仅清空本地缓存，不含设置项与服务端记忆</span>
      </div>
    </div>
  )
}

function MemCat({ title, items }: { title: string; items: { text: string; meta: string; onDel: () => void }[] }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 11, color: FG3, letterSpacing: '.06em', marginBottom: 7 }}>{title}</div>
      {items.map((m, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 10, background: 'var(--au-fill)', border: '1px solid var(--au-line)', marginBottom: 6 }}>
          <span style={{ flex: 1, fontSize: 13, color: FG2, lineHeight: 1.5 }}>{m.text}</span>
          <span style={{ fontSize: 10.5, color: FG3, flexShrink: 0 }}>{m.meta}</span>
          <button onClick={m.onDel} aria-label="删除" title="删除" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, display: 'flex', flexShrink: 0 }}><IcX size={12} color={FG3} /></button>
        </div>
      ))}
    </div>
  )
}
