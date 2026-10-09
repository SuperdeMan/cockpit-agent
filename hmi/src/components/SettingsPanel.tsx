import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useSettings } from '../settings'
import { useDriving } from '../DrivingContext'
import {
  AGENT_CATALOG, VOICE_FALLBACK, WAKE_WORD_PRESETS, TTS_PROVIDER_FALLBACK, LLM_PROVIDER_FALLBACK,
  S2S_VOICES, ASR_MODES, ASR_PROVIDER_FALLBACK, DEFAULT_SETTINGS, asrEngineOptions, asrModeOf, pickAsrEngine,
  type Voice, type TtsProviderInfo, type TtsProvider, type LlmProviderInfo, type LlmStatus,
  type AsrMode, type AsrProvider, type AsrProviderInfo, type Msg,
} from '../types'
import {
  fetchVoices, fetchTtsProviders, fetchAsrProviders, fetchLlmProviders, setLlmProvider,
  fetchMemory, fetchMemoryProfile, forgetMemory, deleteMemoryItem, fetchPlaces, playTTS,
  fetchVoiceprints, enrollVoiceprint, deleteVoiceprint, identifySpeaker,
  renameVoiceprint,
  type MemoryView, type MemoryProfile, type NamedPlaces, type VoiceprintInfo,
} from '../audio'
import { PLACE_DEFS, isPlaceSet, formatPlace } from '../places.mjs'
// 声纹录音走与主链路识别**同一条音频通路**（16k PCM + 同一组 EC/NS/AGC）：
// 走 MediaRecorder/webm 会让模板落在另一个信道上，主链路的 PCM 探针比不上去（真机实测差 0.2）。
import { PcmRecorder } from '../pcmRecorder.mjs'
import { Icon, type IconName } from './Icon'
import { Toggle, Segmented, Select, ListItem, VoiceTile, ConfirmDialog, TextInput, GhostBtn, DangerBtn } from './controls'

const TEAL = 'var(--au-primary)'
const FG1 = 'var(--au-text)'
const FG2 = 'var(--au-text-2)'
const FG3 = 'var(--au-text-3)'
const DIV = 'var(--au-line)'
const MONO = 'var(--au-font-mono)'
// PoC 单用户：与 MemorySection 的 forgetMemory(audioApi,'u1') 同口径（真实身份由网关注入）
const USER_ID = 'u1'

// Icons and sizing come from the shared registry and Visual v2 tokens.
const IcX = () => <Icon name="close" size={28} />
const IcCheck = () => <Icon name="check" size={28} />
const SettingRow = ListItem
const HR = () => <hr className="au-settings-rule" />
type Section = 'tts' | 'asr' | 'wake' | 'pipeline' | 'occupants' | 'vision' | 'display' | 'location' | 'assistant' | 'agents' | 'memory' | 'developer'
const SECTIONS: { id: Section; label: string; icon: IconName }[] = [
  {id:'tts',label:'语音播报',icon:'voice-output'},
  {id:'asr',label:'语音输入',icon:'voice-input'},
  {id:'wake',label:'唤醒与连续对话',icon:'chat'},
  {id:'pipeline',label:'语音链路',icon:'layers'},
  {id:'occupants',label:'乘员与声纹',icon:'voice-birch'},
  {id:'vision',label:'看一看',icon:'camera'},
  {id:'display',label:'显示',icon:'theme'},
  {id:'location',label:'位置与常用地点',icon:'location'},
  {id:'assistant',label:'助手',icon:'assistant'},
  {id:'agents',label:'能力开关',icon:'capability'},
  {id:'memory',label:'记忆',icon:'memory'},
  {id:'developer',label:'开发者',icon:'developer'},
]
function SectionHdr({icon,title,sub}:{icon:IconName;title:string;sub?:string}) {
  return <header className="au-section-header"><span className="au-section-icon"><Icon name={icon} size={32} state="active" /></span>
    <div><h2>{title}</h2>{sub && <p>{sub}</p>}</div></header>
}
function SettingGroup({title,children}:{title:string;children:ReactNode}) {
  return <section className="au-setting-group"><h3>{title}</h3>{children}</section>
}
function ReadStatus({ state, onRetry }: { state: 'loading' | 'ready' | 'fallback'; onRetry: () => void }) {
  if (state === 'ready') return null
  return <div className="au-settings-read-state" role="status"><Icon name={state === 'loading' ? 'refresh' : 'warning'} size={28} />
    <div>{state === 'loading' ? '正在读取设置…' : '暂时未能读取服务配置'}
      {state === 'fallback' && <p>当前沿用已有选项，已保存的设置没有改动。</p>}</div>
    {state === 'fallback' && <GhostBtn onClick={onRetry}>重试</GhostBtn>}</div>
}
export function SettingsPanel({audioApi,sessionId,occupantId,location,locationEnabled,locationStatus,onRequestLocation,onLocationEnabledChange,onClose,messages=[]}: {
  audioApi:string;sessionId:string;occupantId?:string;
  location:{lat:number;lng:number;accuracyM:number;capturedAt:number}|null;
  locationEnabled:boolean;locationStatus:string;onRequestLocation:()=>void;onLocationEnabledChange:(enabled:boolean)=>void;onClose:()=>void;messages?:Msg[];
}) {
  const query = new URLSearchParams(window.location.search).get('settings')
  const initial = query === 'places' ? 'location' : SECTIONS.find(s=>s.id===query)?.id ?? 'tts'
  const [section,setSection]=useState<Section>(initial)
  const dialog=useRef<HTMLDivElement>(null)
  const content=useRef<HTMLDivElement>(null)
  useEffect(()=>{const previous=document.activeElement as HTMLElement;dialog.current?.querySelector<HTMLButtonElement>('[aria-label="关闭设置"]')?.focus();return()=>previous?.focus()},[])
  useEffect(()=>{content.current?.scrollTo(0,0)},[section])
  return <div ref={dialog} className="au-settings-overlay" role="dialog" aria-modal="true" aria-labelledby="settings-title"
    onKeyDown={e=>{
      if(e.key==='Escape'){e.stopPropagation();onClose()}
      if(e.key==='Tab'){
        const items=Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href],[tabindex="0"]')??[]).filter(el=>el.offsetParent!==null)
        const first=items[0],last=items[items.length-1]
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
      }
    }}>
    <header className="au-settings-header"><h1 id="settings-title">设置</h1><button type="button" className="au-icon-btn" onClick={onClose} aria-label="关闭设置"><IcX /></button></header>
    <div className="au-settings-body">
      <nav className="au-settings-nav" aria-label="设置分区">{SECTIONS.map(s=><button key={s.id} type="button" className="au-settings-nav-item" aria-current={section===s.id?'page':undefined} onClick={()=>setSection(s.id)}>
        <Icon name={s.icon} size={28} state={section===s.id?'active':'default'} /><span>{s.label}</span></button>)}</nav>
      <div ref={content} className="au-settings-scroll"><main className="au-settings-content" data-section={section}>
        {section==='tts'&&<TtsSection audioApi={audioApi} />}
        {section==='asr'&&<AsrSection audioApi={audioApi} />}
        {section==='wake'&&<WakeSection />}
        {section==='pipeline'&&<PipelineSection />}
        {section==='occupants'&&<><SectionHdr icon="voice-birch" title="乘员与声纹" sub="录入、辨认和管理常用乘员的声音" /><OccupantSection audioApi={audioApi} /></>}
        {section==='vision'&&<><SectionHdr icon="camera" title="看一看" sub="管理问答时的画面采集" /><VisionSection /></>}
        {section==='display'&&<DisplaySection />}
        {section==='location'&&<><LocationSection location={location} enabled={locationEnabled} status={locationStatus} onRequest={onRequestLocation} onEnabledChange={onLocationEnabledChange} /><PlacesSection audioApi={audioApi} /></>}
        {section==='assistant'&&<AssistantSection audioApi={audioApi} />}
        {section==='agents'&&<AgentsSection />}
        {section==='memory'&&<MemorySection audioApi={audioApi} sessionId={sessionId} occupantId={occupantId||'primary'} />}
        {section==='developer'&&<DeveloperSection audioApi={audioApi} messages={messages} />}
      </main></div>
    </div>
  </div>
}

function ResetButton() {
  const { reset } = useSettings()
  const [confirm, setConfirm] = useState(false)
  if (confirm) {
    return (
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => { reset(); setConfirm(false) }} style={{ flex: 1, padding: '9px 0', borderRadius: 12, border: '1px solid rgba(239,68,68,.28)', background: 'rgba(239,68,68,.06)', color: 'var(--au-danger)', fontSize: 'var(--au-type-caption-size)', cursor: 'pointer', fontFamily: 'inherit' }}>确认重置</button>
        <GhostBtn sm onClick={() => setConfirm(false)}>取消</GhostBtn>
      </div>
    )
  }
  return <DangerBtn onClick={() => setConfirm(true)}>恢复默认设置</DangerBtn>
}

// ─── 1 · 语音播报 ───
// 音色 → A-8 人格图标；非六大人格（Milo/Dean/MiMo 等）回落 voice-soda（气泡，中性）
const VOICE_ICON: Record<string, IconName> = {
  冰糖: 'voice-ice', 茉莉: 'voice-jasmine', 苏打: 'voice-soda', 白桦: 'voice-birch', Mia: 'voice-mia', Chloe: 'voice-chloe',
}
function voiceIcon(v: Voice): IconName {
  // MiMo 六大人格有专属图标；cosyvoice/qwen 音色按性别回落（女=茉莉气泡、男=白桦、中性=苏打）
  return VOICE_ICON[v.voice_id] ?? VOICE_ICON[v.name] ??
    (v.gender === 'female' ? 'voice-jasmine' : v.gender === 'male' ? 'voice-birch' : 'voice-soda')
}
// Agent → 图标（A-8 集未含，icons.custom 补；端侧快系统车控/媒体用 vehicle/media）
const AGENT_ICON: Record<string, IconName> = {
  vehicle: 'vehicle', media: 'media', navigation: 'compass', info: 'info', 'trip-planner': 'itinerary',
  'deep-research': 'research', nearby: 'dining', 'parking-payment': 'parking', 'manual-rag': 'manual', chitchat: 'chat',
}
// 常用地点 → 图标（家=A-8 place-home；公司/学校 icons.custom 补）
const PLACE_ICON: Record<string, IconName> = { home: 'place-home', company: 'building', school: 'school' }

function TtsSection({ audioApi }: { audioApi: string }) {
  const { settings, update } = useSettings()
  const [providers, setProviders] = useState<TtsProviderInfo[]>(TTS_PROVIDER_FALLBACK)
  const [playing, setPlaying] = useState<string | null>(null)
  const [readState, setReadState] = useState<'loading' | 'ready' | 'fallback'>('loading')

  // 探测后端引擎清单（含各引擎音色 + 可用性）；失败留离线兜底
  const load = useCallback(async () => {
    setReadState('loading')
    const ps = await fetchTtsProviders(audioApi)
    if (ps.length) { setProviders(ps); setReadState('ready') } else setReadState('fallback')
  }, [audioApi])
  useEffect(() => { void load() }, [load])

  const cur = providers.find((p) => p.id === settings.ttsProvider) ?? providers[0]
  const voices = cur?.voices ?? VOICE_FALLBACK
  const disabled = !settings.ttsEnabled
  const ProviderChoice = providers.length > 3 ? Select : Segmented

  // 切引擎：换音色集，若当前音色不在新引擎里则回落该引擎默认（首个音色）
  const selectProvider = (pid: string) => {
    const p = providers.find((x) => x.id === pid)
    if (!p) return
    const patch: { ttsProvider: TtsProvider; voiceId?: string } = { ttsProvider: pid as TtsProvider }
    if (!p.voices.some((v) => v.voice_id === settings.voiceId)) patch.voiceId = p.voices[0]?.voice_id
    update(patch)
  }

  const preview = async (voiceId: string) => {
    setPlaying(voiceId)
    try { await playTTS(audioApi, `你好，我是${settings.assistantName}，这是我的声音。`, voiceId, settings.ttsProvider) }
    catch {/* ignore */} finally { setPlaying(null) }
  }

  return (
    <div>
      <SectionHdr icon="voice-output" title="语音播报" sub="控制助手的语音输出方式、引擎与音色偏好" />
      <ReadStatus state={readState} onRetry={()=>void load()} />
      <SettingGroup title="输出控制">
        <SettingRow label="启用语音播报" sub="关闭后助手仅显示文字，不朗读回答">
          <Toggle on={settings.ttsEnabled} onChange={(v) => update({ ttsEnabled: v })} />
        </SettingRow>
        <SettingRow label="自动播放回答" sub="收到回答后立即朗读，无需手动点击" noBorder>
          <Toggle on={settings.autoplay} onChange={(v) => update({ autoplay: v })} disabled={!settings.ttsEnabled} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="语音引擎">
        <SettingRow label="播报引擎" sub="流式引擎边合成边出声、首音更快；经典引擎整句合成后播放" noBorder={!cur || cur.streaming}>
          <ProviderChoice value={settings.ttsProvider} onChange={selectProvider}
            options={providers.map((p) => ({ value: p.id, label: p.label.split('·')[0] }))} />
        </SettingRow>
      </SettingGroup>
      <SettingGroup title={`音色选择（${cur?.label ?? ''}）`}>
        <div className="au-voice-grid">{voices.map(v => <VoiceTile key={v.voice_id} name={v.name}
          description={v.description || (v.tags || [v.language,v.gender]).join(' · ')} icon={voiceIcon(v)}
          selected={settings.voiceId===v.voice_id} playing={playing===v.voice_id} disabled={disabled}
          onSelect={()=>update({voiceId:v.voice_id})} onPreview={()=>void preview(v.voice_id)} />)}</div>
      </SettingGroup>
    </div>
  )
}

// ─── 2 · 语音输入 ───
// 「方式 → 引擎」两级（2026-09-14，与 Android 设置页同一份契约 types.ASR_*）：一级按用户能感知的形态
// （实时=边说边上屏 / 整句=松手后出字），二级是 (provider, model) 对；目录来自网关 /api/asr/stream/info，
// 离线落回 ASR_PROVIDER_FALLBACK。以前的「分块 / 关闭」两档并进「整句」。
function AsrSection({ audioApi }: { audioApi: string }) {
  const { settings, update } = useSettings()
  const [providers, setProviders] = useState<AsrProviderInfo[]>(ASR_PROVIDER_FALLBACK)
  const [readState, setReadState] = useState<'loading' | 'ready' | 'fallback'>('loading')
  const load = useCallback(async () => {
    setReadState('loading')
    const ps = await fetchAsrProviders(audioApi)
    setProviders(ps); setReadState(ps === ASR_PROVIDER_FALLBACK ? 'fallback' : 'ready')
  }, [audioApi])
  useEffect(() => { void load() }, [load])

  const mode = asrModeOf(providers, settings.asrProvider)
  const engines = asrEngineOptions(providers, mode)
  const modeMeta = ASR_MODES.find((m) => m.id === mode) ?? ASR_MODES[0]
  const current = engines.find((e) => e.provider === settings.asrProvider && e.model === settings.asrModel)
  const engineKey = (e: { provider: string; model: string }) => `${e.provider}/${e.model}`

  const selectMode = (next: AsrMode) => {
    if (next === mode) return
    // 换方式：优先本端默认那一对（DEFAULT_SETTINGS），其次该方式下首个可用引擎
    const picked = pickAsrEngine(asrEngineOptions(providers, next), {
      provider: DEFAULT_SETTINGS.asrProvider, model: DEFAULT_SETTINGS.asrModel,
    })
    if (picked) update({ asrProvider: picked.provider as AsrProvider, asrModel: picked.model })
  }
  const selectEngine = (key: string) => {
    const e = engines.find((x) => engineKey(x) === key)
    if (e) update({ asrProvider: e.provider as AsrProvider, asrModel: e.model })
  }

  return (
    <div>
      <SectionHdr icon="voice-input" title="语音输入" sub="配置识别方式、引擎、语言、模式与时长" />
      <ReadStatus state={readState} onRetry={()=>void load()} />
      <SettingGroup title="识别引擎">
        <SettingRow label="识别方式" sub="实时是边说边显示；整句是松手后显示识别结果">
          <Segmented value={mode} onChange={selectMode}
            options={ASR_MODES.map((m) => ({ value: m.id, label: m.label }))} />
        </SettingRow>
        <SettingRow label={`${modeMeta.label}引擎`} sub={current && !current.available ? '该引擎暂不可用，按住说话会回退为整句识别' : '选择识别引擎，暂不可用的选项已停用'} noBorder>
          {engines.length ? (
            <Select value={current ? engineKey(current) : ''} onChange={selectEngine}
              options={engines.map((e) => ({ value: engineKey(e), label: e.label, disabled: !e.available }))} />
          ) : (
            <span style={{ fontSize: 'var(--au-type-body-size)', color: 'var(--au-text-3)' }}>—</span>
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
          <Select value={settings.listenSeconds} onChange={(v) => update({ listenSeconds: v })}
            options={[{ value: 10, label: '10s' }, { value: 15, label: '15s' }, { value: 30, label: '30s' }, { value: 60, label: '60s' }]} />
        </SettingRow>
      </SettingGroup>
      <HR />
    </div>
  )
}

function WakeSection(){
  const {settings,update}=useSettings()
  return <div><SectionHdr icon="chat" title="唤醒与连续对话" sub="管理唤醒词和回答后的聆听方式" />
      <SettingGroup title="语音唤醒 · 连续对话">
        <SettingRow label="免唤醒连续对话" sub="回复播完后保持聆听窗，接着说即自动断句发送，无需再按光球。说「退下吧 / 没事了」可随时退出聆听。唤醒前音频仅在浏览器本地检测、不上传。默认关。">
          <Toggle on={settings.handsFree} onChange={(v) => update({ handsFree: v })} />
        </SettingRow>
        <SettingRow label="唤醒词" sub="待机时说唤醒词进入聆听，全程免触屏。需要设备已准备好语音唤醒资源。" noBorder={!settings.handsFree}>
          <Toggle on={settings.wakeWordEnabled} onChange={(v) => update({ wakeWordEnabled: v })} disabled={!settings.handsFree} />
        </SettingRow>
        {settings.handsFree && settings.wakeWordEnabled && (
          <SettingRow label="选择唤醒词" sub="换词后直接说新唤醒词即可生效；命中率以真机为准">
            <Select value={settings.wakeWord} onChange={(v) => update({ wakeWord: v })}
              options={WAKE_WORD_PRESETS.map((p) => ({ value: p.word, label: p.word }))} />
          </SettingRow>
        )}
        {settings.handsFree && (
          <>
            <SettingRow label="续问聆听窗" sub="回复播完后等待你接话的时长">
              <Segmented sm value={settings.followupWindowS} onChange={(v) => update({ followupWindowS: v })}
                options={[{ value: 5, label: '5s' }, { value: 8, label: '8s' }, { value: 15, label: '15s' }]} />
            </SettingRow>
          </>
        )}
      </SettingGroup>
  </div>
}
function PipelineSection(){
  const {settings,update}=useSettings()
  return <div><SectionHdr icon="layers" title="语音链路" sub="选择语音问答的处理方式，查看原始语音的使用范围" />
      <SettingGroup title="语音链路">
        <SettingRow
          label="端到端语音直连"
          sub={'闲聊与常识问答由语音大模型直接听、直接答。'
            + '需要执行的事（车控、导航、提醒、支付）和查实时信息的事，一律自动交回常规链路——车辆动作永不经此下发。'
            + '开启后唤醒到说完这段窗口内的原始语音会上传云端处理（常规链路只上传识别后的文字）；未唤醒时不采集。'
            + '切换在下次开启连续对话时生效。默认关。'}
          noBorder={settings.voicePipeline !== 's2s'}
        >
          <Toggle on={settings.voicePipeline === 's2s'}
            onChange={(v) => update({ voicePipeline: v ? 's2s' : 'classic' })} />
        </SettingRow>
        {settings.voicePipeline === 's2s' && (
          <SettingRow label="直连音色" sub="端到端链路的说话人（与上方播报音色是两套引擎；选相近的可减少切回常规链路时的音色差）" noBorder>
            <Select value={settings.s2sVoice} onChange={(v) => update({ s2sVoice: v })}
              options={S2S_VOICES.map((v) => ({ value: v, label: v }))} />
          </SettingRow>
        )}
      </SettingGroup>
  </div>
}

// ─── 2.5 · 乘员与声纹（M4 P4）───
// 每个乘员各自的口味/习惯/常去地点互相独立；认不出时一律按主驾走（= 开这个开关之前的行为）。
//
// 交互上最要紧的两件事（首版都缺，泓舟真机反馈）：
// ① **录音过程要可见**——用户对着麦克风说话时屏幕上必须有「正在录、还剩几秒」，
//    否则不知道该说多久、说完了没有，只能靠猜；
// ② **录完要能当场验证**——「试一试」录 2 秒立刻回答「听出来是谁」。不然用户唯一的
//    验证手段是去对话框问一句，失败了也不知道是哪一环出的问题。
//
// 第二轮真机反馈（2026-07-26）又补了两条，都属于「静默地把事情办错」：
// ③ **称呼必填**——原来空着就悄悄写死「乘客」，重录一次就把上次填对的名字冲掉（库里
//    留下 4 条 superseded 的「泓舟」和 1 条现行的「乘客」就是这么来的）；
// ④ **改名不该重录三段**——名字是元数据。没有改名入口正是用户反复重录的原因。
const ENROLL_PROMPTS = [
  '你好，我是这辆车的常用乘客',
  '今天天气不错，路上应该不太堵',
  '帮我把空调调到二十四度',
]
const ENROLL_SECONDS = 4
// 「试一试」原为 2 秒：切掉头尾静音后常常不足网关要求的 1.5 秒**有效语音**，
// 于是一律回 too_short——自证功能永远只会说「太短了」。与注册同长。
const TRY_SECONDS = 4

function OccupantSection({ audioApi }: { audioApi: string }) {
  const { settings, update, developerMode, developerOptions } = useSettings()
  const [info, setInfo] = useState<VoiceprintInfo | null>(null)
  const [name, setName] = useState('')
  const [open, setOpen] = useState(false)                 // 录入面板是否展开
  const [clips, setClips] = useState<(Int16Array | null)[]>([null, null, null])
  const [recIdx, setRecIdx] = useState(-1)                // 正在录第几段；-2=试一试；-1=空闲
  const [left, setLeft] = useState(0)                     // 倒计时秒
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [trying, setTrying] = useState(false)
  const [editing, setEditing] = useState('')                // 正在改名的 occupant_id
  // 本次录入绑定的乘员（空=新增一位）。重录必须带原 occupant_id，否则会分到新身份、记忆分家。
  const [targetOcc, setTargetOcc] = useState('')
  const [draftName, setDraftName] = useState('')
  const [mic] = useState(() => new PcmRecorder())

  const refresh = useCallback(async () => {
    setInfo(await fetchVoiceprints(audioApi, USER_ID))
  }, [audioApi])
  useEffect(() => { void refresh() }, [refresh])

  // 录音倒计时：这是「正在录」的唯一可见反馈（PcmRecorder 到点自动停）
  useEffect(() => {
    if (recIdx === -1 || left <= 0) return
    const t = setTimeout(() => setLeft((n) => n - 1), 1000)
    return () => clearTimeout(t)
  }, [recIdx, left])

  const done = clips.filter(Boolean).length
  const ready = done === ENROLL_PROMPTS.length
  const canSave = ready && !!name.trim()

  const recordAt = async (i: number) => {
    if (mic.active || recIdx !== -1) return
    setMsg(''); setRecIdx(i); setLeft(ENROLL_SECONDS)
    try {
      await mic.start(ENROLL_SECONDS * 1000, (r) => {
        setRecIdx(-1); setLeft(0)
        // 报的是**去静音后的人声时长**：录了 4 秒但只说了 0.8 秒，用户必须当场知道，
        // 否则他会以为录好了，直到三段自洽度不够被拒才发现，而那时看不出是哪一段的问题。
        if (!r?.pcm || r.durationMs < 1200) {
          setMsg(r?.pcm
            ? `这段只听到 ${(r.durationMs / 1000).toFixed(1)} 秒人声——请在提示出现后马上开始念，念满约 ${ENROLL_SECONDS} 秒。`
            : '这段没录到声音——确认麦克风可用、离近一点再试')
          return
        }
        setClips((c) => c.map((v, k) => (k === i ? r.pcm : v)))
      })
    } catch {
      setRecIdx(-1); setLeft(0)
      setMsg('拿不到麦克风权限：请在浏览器地址栏允许本站使用麦克风')
    }
  }

  const submit = async () => {
    // 称呼必填：空名兜底成「乘客」等于替用户瞎起一个名，而且会覆盖上次填对的。
    const who = name.trim()
    if (!who) { setMsg('先填一个称呼——助手要靠它称呼你、也靠它回答「你知道我是谁」。'); return }
    setBusy(true)
    const r = await enrollVoiceprint(audioApi, USER_ID, who,
                                     clips.filter(Boolean) as Int16Array[], 'pcm16le', targetOcc)
    setBusy(false)
    if (r.ok) {
      // 首个注册者由服务端分配到 primary——他继承全部既有记忆（不然一注册就全失联）。
      setMsg(r.occupant_id === 'primary'
        ? `已记住「${who}」，作为主驾——你之前的记忆一条都没丢。可以点上面的「试一试」验证。`
        : `已记住「${who}」。可以点上面的「试一试」验证。`)
      setOpen(false); setClips([null, null, null]); setName(''); setTargetOcc('')
      void refresh()
    } else if (r.error === 'low_consistency') {
      // 宁可不建也不建坏模板：建出来此后谁都认不准。
      setMsg(`这三段听起来不像同一个人（相似度 ${(r.self_consistency ?? 0).toFixed(2)}）。`
        + '请在安静环境里、由同一个人重录三段。')
      setClips([null, null, null])
    } else if (r.error === 'no_valid_samples') {
      setMsg(`每段都要说满约 ${ENROLL_SECONDS} 秒——太短提不出稳定声纹，请重录。`)
      setClips([null, null, null])
    } else {
      setMsg(developerMode && developerOptions.rawErrors ? '注册失败：' + (r.error || '未知原因') : '注册失败，请稍后重试')
    }
  }

  // 「试一试」：录 2 秒立刻问后端「这是谁」——用户验证声纹真的生效的唯一直接手段。
  const tryIt = async () => {
    if (mic.active || recIdx !== -1) return
    setMsg(''); setTrying(true); setRecIdx(-2); setLeft(TRY_SECONDS)
    try {
      await mic.start(TRY_SECONDS * 1000, async (r) => {
        setRecIdx(-1); setLeft(0)
        if (!r?.pcm) { setTrying(false); setMsg('没录到声音，再试一次'); return }
        if (r.durationMs < 800) { setTrying(false); setMsg('只听到不到 1 秒人声，多说一句再试'); return }
        const res = await identifySpeaker(audioApi, USER_ID, r.pcm)
        setTrying(false)
        const who = res.display_name || res.occupant_id
        if (res.decision === 'accept') setMsg(`听出来了：这是「${who}」`)
        else if (res.decision === 'too_short') setMsg('说得太短了，多说一句再试')
        else if (res.decision === 'ambiguous') setMsg('两位乘员的声音太接近，这次没敢下结论——按主驾处理')
        else if (res.decision === 'no_templates') setMsg('还没有人录过声纹')
        else setMsg('没认出来，按主驾处理（认不出时不会乱猜）')
      })
    } catch {
      setTrying(false); setRecIdx(-1); setLeft(0)
      setMsg('拿不到麦克风权限')
    }
  }

  // 删除。**文案按 primary 与否分开写**：服务端对 primary 永不 purge 记忆（删单个乘员不该
  // 有清空全车的爆炸半径），原来一律说「并忘掉 TA 的全部记忆」是在承诺一件不会发生的事。
  const remove = async (occ: string, display: string) => {
    const who = display || occ
    const ask = occ === 'primary'
      ? `删除「${who}」的声纹？记忆会保留（主驾名下就是全车既有记忆，清空请用「忘掉全部记忆」），`
        + '删除后将认不出说话人，一律按主驾处理。'
      : `删除「${who}」并忘掉 TA 的全部记忆？此操作不可撤销。`
    if (!window.confirm(ask)) return
    const r = await deleteVoiceprint(audioApi, USER_ID, occ, true)
    if (!r.ok) setMsg('删除失败')
    else if (!r.deleted_templates) setMsg('没有找到这条声纹记录（可能已经被删掉了）')
    else setMsg(r.deleted_memories
      ? `已删除「${who}」，同时忘掉 ${r.deleted_memories} 条记忆`
      : `已删除「${who}」的声纹，记忆保留`)
    void refresh()
  }

  // 改名：只改称呼不动模板。没有这个入口，用户改个名字就得重录三段——正是名字被冲掉的成因。
  const saveName = async (occ: string) => {
    const next = draftName.trim()
    if (!next) { setMsg('称呼不能为空'); return }
    const r = await renameVoiceprint(audioApi, USER_ID, occ, next)
    setEditing(''); setDraftName('')
    setMsg(r.ok ? `已改名为「${next}」` : developerMode && developerOptions.rawErrors ? `改名失败：${r.error || '未知原因'}` : '改名失败，请稍后重试')
    void refresh()
  }

  if (info && !info.enabled) {
    return (
      <SettingGroup title="乘员与声纹">
        <SettingRow label="声纹识别不可用" sub={'服务端未加载声纹模型，本功能已自动停用（其余语音功能不受影响）。'
} noBorder>
          <span style={{ color: FG3, fontSize: 'var(--au-type-caption-size)' }}>未启用</span>
        </SettingRow>
      </SettingGroup>
    )
  }

  const occupants = info?.occupants ?? []

  return (
    <SettingGroup title="乘员与声纹">
      <SettingRow
        label="按声音区分乘员"
        sub={'开启后，唤醒时的第一句话用来判断是谁在说话，每个人的口味、习惯、常去地点各自独立。'
          + '认不出时一律按主驾处理（和不开这个开关一样）。'
          + '声纹只用于区分记忆，不作为任何权限或支付的凭证。'}
      >
        <Toggle on={settings.voiceprintEnabled}
          onChange={(v) => update({ voiceprintEnabled: v })} />
      </SettingRow>

      {occupants.map((o) => (
        <SettingRow key={o.occupant_id}
          label={editing === o.occupant_id ? '改称呼' : (o.display_name || o.occupant_id)}
          sub={(o.occupant_id === 'primary' ? '主驾 · ' : '')
            + `${o.sample_count} 段样本`
            + (o.stale ? ' · 模型已更新，建议重录' : '')}>
          {editing === o.occupant_id ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <TextInput value={draftName} onChange={setDraftName} width={140}
                placeholder={o.display_name || '如「阿段」'} />
              <GhostBtn sm onClick={() => void saveName(o.occupant_id)}>保存</GhostBtn>
              <GhostBtn sm onClick={() => { setEditing(''); setDraftName('') }}>取消</GhostBtn>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <GhostBtn sm onClick={() => {
                setMsg(''); setEditing(o.occupant_id); setDraftName(o.display_name || '')
              }}>改名</GhostBtn>
              {/* 重录 = 用**原身份**更新模板。走「添加乘员」会分到新的 occ-N，
                  这个人的记忆当场分家成两半——重录与新增必须是两个动作。 */}
              <GhostBtn sm onClick={() => {
                setMsg(''); setEditing(''); setTargetOcc(o.occupant_id)
                setName(o.display_name || ''); setClips([null, null, null]); setOpen(true)
              }}>重录</GhostBtn>
              <div>
                <DangerBtn onClick={() => remove(o.occupant_id, o.display_name)}>删除</DangerBtn>
              </div>
            </div>
          )}
        </SettingRow>
      ))}

      {occupants.length > 0 && (
        <SettingRow label="试一试"
          sub={recIdx === -2 ? `正在听…还剩 ${left} 秒，请一直说`
            : `连着说一句话（约 ${TRY_SECONDS} 秒），看它能不能认出你是谁`}>
          <GhostBtn onClick={tryIt}>{trying ? '识别中…' : '开始'}</GhostBtn>
        </SettingRow>
      )}

      {!open && (
        <SettingRow label="添加乘员"
          sub={occupants.length === 0
            ? '第一个录入的人会被当作主驾，保留目前已有的全部记忆。填个称呼、念 3 句话即可。'
            : `填个称呼，在安静环境里念 3 句话，每句约 ${ENROLL_SECONDS} 秒。`}
          noBorder={!msg}>
          <GhostBtn onClick={() => {
            setOpen(true); setTargetOcc(''); setName(''); setClips([null, null, null]); setMsg('')
          }}>
            开始录入
          </GhostBtn>
        </SettingRow>
      )}

      {open && (
        <>
          <SettingRow label={targetOcc ? `称呼（重录「${name || targetOcc}」）` : '称呼（必填）'}
            sub={targetOcc
              ? '重录会更新这位乘员的声纹模板，身份与记忆都保留（不会变成新的一个人）。'
              : '助手会这样叫你；问「你知道我是谁」时也用它回答。录完之后随时可以改。'}>
            <TextInput value={name} onChange={setName} placeholder="如「阿段」" maxLength={16} />
          </SettingRow>
          {ENROLL_PROMPTS.map((line, i) => (
            <SettingRow key={i}
              label={`${clips[i] ? '✓' : `${i + 1}.`} 「${line}」`}
              sub={recIdx === i ? `正在录…还剩 ${left} 秒，请照着念`
                : clips[i] ? '已录好，可以重录' : `点右侧按钮后照着念，约 ${ENROLL_SECONDS} 秒`}>
              <GhostBtn onClick={() => recordAt(i)}>
                {recIdx === i ? `${left}s` : clips[i] ? '重录' : '录这句'}
              </GhostBtn>
            </SettingRow>
          ))}
          <SettingRow label={`已录 ${done} / ${ENROLL_PROMPTS.length} 段`}
            sub={!ready ? '三段都录完才能保存'
              : !name.trim() ? '还差一个称呼' : '可以保存了'} noBorder={!msg}>
            <div style={{ display: 'flex', gap: 8 }}>
              <GhostBtn onClick={() => { if (canSave && !busy) void submit() }}
                style={canSave ? undefined : { opacity: 0.45 }}>
                {busy ? '处理中…' : '保存'}
              </GhostBtn>
              <GhostBtn onClick={() => {
                setOpen(false); setClips([null, null, null]); setTargetOcc('')
              }}>取消</GhostBtn>
            </div>
          </SettingRow>
        </>
      )}

      {msg && <SettingRow label="" sub={msg} noBorder><span /></SettingRow>}
    </SettingGroup>
  )
}

// ─── 2.6 · 看一看（M4 P4 视觉入口）───
function VisionSection() {
  const { settings, update } = useSettings()
  return (
    <SettingGroup title="看一看">
      <SettingRow
        label="问「那是什么」时看一眼"
        sub={'开启后，当你说「那是什么」「这是什么车」这类话时，会拍下当前画面的一帧交给 AI 识别。'
          // sub 是纯文本直出，不渲染 markdown——星号会原样显示给用户（2026-07-26 截图发现）
          + '只在说这类话时拍，其余时候一帧都不采集；画面用完即弃，不保存、不进记忆。'
          + '当前演示环境没有车外摄像头，用设备摄像头代替，结果卡片会标注「模拟车外摄像头」。默认关。'}
        noBorder
      >
        <Toggle on={settings.visionEnabled} onChange={(v) => update({ visionEnabled: v })} />
      </SettingRow>
    </SettingGroup>
  )
}

// ─── 3 · 显示主题 ───
function DisplaySection() {
  const { settings, update } = useSettings()
  const { driving, setDriving } = useDriving()
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
      <SectionHdr icon="theme" title="显示" sub="界面外观、字号与快捷指令定制" />
      <SettingGroup title="外观">
        <SettingRow label="主题" sub="深色适合夜间驾驶，浅色适合晴天">
          <Segmented value={settings.theme} onChange={(v) => update({ theme: v })}
            options={[{ value: 'dark', label: '深色' }, { value: 'light', label: '浅色' }]} />
        </SettingRow>
        <SettingRow label="字号" sub="大字模式放大所有文本，提升行车可读性">
          <Segmented value={settings.fontScale} onChange={(v) => update({ fontScale: v })}
            options={[{ value: 'normal', label: '标准' }, { value: 'large', label: '大字' }]} />
        </SettingRow>
        <SettingRow label="大触控模式" sub="按当前行车档放大按钮与点击热区">
          <Toggle on={settings.largeTouch} onChange={(v) => update({ largeTouch: v })} />
        </SettingRow>
        <SettingRow label="行车模式" sub="简化显示；关闭仅退出当前行车段，不改变车辆状态" noBorder>
          <Toggle on={driving} onChange={setDriving} />
        </SettingRow>
      </SettingGroup>
      <HR />
      <SettingGroup title="快捷指令">
        <div style={{ paddingTop: 10, paddingBottom: 16 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
            {settings.quickCommands.map((cmd, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 20, background: 'var(--au-fill)', border: '1px solid var(--au-line-2)' }}>
                <span style={{ fontSize: 'var(--au-type-caption-size)', color: FG2 }}>{cmd}</span>
                <button onClick={() => removeCmd(i)} aria-label="删除指令" style={{ cursor: 'pointer', background: 'none', border: 'none', padding: 0, display: 'flex', lineHeight: 1 }}>
                  <IcX />
                </button>
              </div>
            ))}
            {adding ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <input autoFocus={!driving} readOnly={driving} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addCmd(); if (e.key === 'Escape') { setDraft(''); setAdding(false) } }}
                  placeholder="新指令…" maxLength={16}
                  style={{ width: 130, height: 30, padding: '0 10px', borderRadius: 20, background: 'var(--au-fill)', border: `1px solid ${TEAL}`, color: FG1, fontSize: 'var(--au-type-caption-size)', fontFamily: 'inherit', outline: 'none', caretColor: TEAL }} />
                <button onClick={addCmd} aria-label="确认添加" style={{ width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', cursor: 'pointer', background: 'var(--au-primary-soft)', border: `1px solid ${TEAL}`, color: TEAL }}><IcCheck /></button>
              </span>
            ) : settings.quickCommands.length < 8 ? (
              <button onClick={() => setAdding(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 20, border: '1px dashed var(--au-text-3)', background: 'transparent', color: FG3, fontSize: 'var(--au-type-caption-size)', cursor: 'pointer', fontFamily: 'inherit' }}>
                添加
              </button>
            ) : null}
          </div>
          <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3 }}>最多 8 条 · 显示在输入框上方的指令轨</div>
        </div>
      </SettingGroup>
      <SettingGroup title="本机设置"><SettingRow label="恢复默认设置" sub="恢复此设备的界面与语音偏好，不删除服务端记忆" noBorder><ResetButton /></SettingRow></SettingGroup>
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
      <SectionHdr icon="location" title="位置与常用地点" sub="管理位置权限、定位状态和常用目的地" />
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
                <span style={{ fontSize: 'var(--au-type-caption-size)', fontWeight: 600, color: 'var(--au-online)' }}>定位已开启</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[['纬度', `${location.lat.toFixed(4)}° N`], ['经度', `${location.lng.toFixed(4)}° E`], ['精度', `±${Math.round(location.accuracyM)}m`], ['更新', relTime(location.capturedAt)]].map(([l, v]) => (
                  <div key={l}>
                    <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3 }}>{l}</div>
                    <div style={{ fontFamily: MONO, fontSize: 'var(--au-type-caption-size)', color: FG1, marginTop: 2 }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 0' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: FG3 }} />
              <span style={{ fontSize: 'var(--au-type-body-size)', color: FG3 }}>{enabled ? '定位已开启，尚未获取坐标' : '位置服务已关闭'}</span>
            </div>
          )}
          <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
            <GhostBtn onClick={onRequest}>{enabled ? '更新当前位置' : '申请并启用'}</GhostBtn>
            <span style={{ fontSize: 'var(--au-type-caption-size)', color: FG3, flex: 1, lineHeight: 1.5 }}>{status}</span>
          </div>
          <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3, marginTop: 12, lineHeight: 1.6 }}>关闭的是座舱助手对位置的使用；如需撤销浏览器级授权，请在浏览器站点权限中操作。</div>
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
      <p className="au-setting-note">说「我家在 XX」「把公司设成 XX」设置常用地点；说「回家」「导航去公司」直达。</p>
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
                <div style={{ width: 36, height: 36, borderRadius: 10, background: set ? 'var(--au-primary-soft)' : 'var(--au-fill)', border: `1px solid ${set ? 'var(--au-primary-line)' : 'var(--au-line-2)'}`, display: 'grid', placeItems: 'center', flexShrink: 0 }}><Icon name={PLACE_ICON[key] ?? 'pin'} size={18} color={set ? 'var(--au-primary)' : 'var(--au-text-2)'} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--au-type-body-size)', fontWeight: 600, color: FG1, marginBottom: 4 }}>{label}</div>
                  {set ? (
                    <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG2 }}>{formatPlace(place)}</div>
                  ) : (
                    <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3 }}>未设置 · 说『{hint}』即可设置</div>
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
// 具体模型的短标签（去品牌前缀，Segmented 里紧凑显示）：MiMo 2.5 Pro→Pro / 通义千问 3.7 Max→Max
function modelShort(label: string): string {
  return label.includes(' ') ? label.split(' ').pop() || label : label
}

// 被动健康点（运行时硬化 D5）：绿=近窗全成 / 黄=偶发失败 / 红=高失败或限流 / 灰=近期未使用。
// 「可用」只代表配了 key，这里回答「最近真的答得上来吗」。
function llmHealthDot(h?: import('../types').LlmProviderHealth): { color: string; text: string } {
  if (!h || !h.window) return { color: 'var(--au-text-3)', text: '未使用' }
  const bad = h.err / h.window
  if (bad === 0) {
    return { color: 'var(--au-primary)', text: h.ewma_latency_ms ? `${Math.round(h.ewma_latency_ms)}ms` : '正常' }
  }
  if (bad < 0.3) return { color: 'var(--au-warn, #FCD34D)', text: `${h.err}/${h.window} 失败` }
  return { color: 'var(--au-danger, #F87171)', text: h.rate_limited ? '限流' : `${h.err}/${h.window} 失败` }
}

function AssistantSection({ audioApi }: { audioApi: string }) {
  const { settings, update } = useSettings()
  const [llm, setLlm] = useState<LlmStatus | null>(null)
  const [readState, setReadState] = useState<'loading' | 'ready' | 'fallback'>('loading')

  // 探测网关厂商清单 + 当前 active；失败留离线兜底
  const load = useCallback(async () => {
    setReadState('loading')
    const result = await fetchLlmProviders(audioApi)
    if (result) { setLlm(result); setReadState('ready') } else setReadState('fallback')
  }, [audioApi])
  useEffect(() => { void load() }, [load])

  const providers: LlmProviderInfo[] = llm?.providers ?? LLM_PROVIDER_FALLBACK
  // 选中厂商：本地显式选定优先，否则跟随网关当前 active（空则第一个）
  const activeProvider = settings.llmProvider || llm?.active.provider || providers[0]?.id || 'mimo'
  const curProv = providers.find((p) => p.id === activeProvider) ?? providers[0]
  const activeModel = settings.llmModel || (settings.llmProvider ? '' : llm?.active.model) || curProv?.primary || ''

  const selectProvider = async (pid: string) => {
    const p = providers.find((x) => x.id === pid)
    if (!p || !p.available) return
    update({ llmProvider: pid, llmModel: '' })          // 换厂商清空具体模型（用该厂商 primary）
    const st = await setLlmProvider(audioApi, pid, '')  // 全局切换
    if (st) setLlm(st)
  }
  const selectModel = async (mid: string) => {
    update({ llmModel: mid })
    const st = await setLlmProvider(audioApi, activeProvider, mid)
    if (st) setLlm(st)
  }

  const models = [
    { value: 'fast' as const, name: '快速', desc: '低延迟，适合导航/天气/音乐等实时任务', latency: '<0.5s' },
    { value: 'deep' as const, name: '深度推理', desc: '复杂推理，适合行程规划、调研报告', latency: '1-3s' },
    { value: 'auto' as const, name: '自动', desc: '根据任务复杂度智能切换，推荐日常使用', latency: '智能' },
  ]
  return (
    <div>
      <SectionHdr icon="assistant" title="助手" sub={`设置${settings.assistantName}的称呼、回答风格和问答引擎`} />
      <ReadStatus state={readState} onRetry={()=>void load()} />
      <SettingGroup title="AI 大脑">
        <SettingRow label="模型厂商" sub="切换后所有会话共用；不可用的服务已停用。未选择时沿用当前服务。">
          <Select value={activeProvider} onChange={selectProvider}
            options={providers.map((p) => ({ value: p.id, label: p.label.split('·')[0], disabled: !p.available }))} />
        </SettingRow>
        <SettingRow label="具体模型" sub="选择问答模型；未选择时使用当前服务的默认模型" noBorder>
          {curProv && curProv.models.length > 1 ? (
            <Select value={activeModel} onChange={selectModel}
              options={curProv.models.map((m) => ({ value: m.id, label: modelShort(m.label) }))} />
          ) : (
            <span style={{ fontSize: 'var(--au-type-body-size)', color: FG3, fontFamily: MONO }}>{curProv?.models.find(m=>m.id===activeModel)?.label || '默认模型'}</span>
          )}
        </SettingRow>
      </SettingGroup>
      <HR />
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
              <div key={m.value} style={{ display: 'flex', gap: 12, padding: '10px 14px', borderRadius: 12, background: on ? 'var(--au-primary-soft)' : 'var(--au-fill)', border: `1px solid ${on ? 'var(--au-primary-line)' : 'var(--au-fill)'}` }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--au-type-body-size)', fontWeight: 600, color: on ? TEAL : FG2, marginBottom: 2 }}>{m.name}</div>
                  <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3 }}>{m.desc}</div>
                </div>
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
      <SectionHdr icon="capability" title="能力开关" sub="控制各项能力是否启用；核心能力不能关闭。" />
      <SettingGroup title="能力列表">
        <div style={{ paddingBottom: 8 }}>
          {AGENT_CATALOG.map(a => <SettingRow key={a.id} label={a.label} sub={a.desc + (a.core ? ' · 核心能力，始终开启' : '')}>
            <Toggle on={settings.agents[a.id] ?? true} onChange={()=>!a.core && toggleAgent(a.id)} disabled={a.core} />
          </SettingRow>)}
        </div>
      </SettingGroup>
    </div>
  )
}

// ─── 8 · 记忆 ───
const _PLACE_LABEL: Record<string, string> = { home: '家', company: '公司', school: '学校' }
const _PROV_LABEL: Record<string, string> = { user_stated: '你说的', agent_inferred: '推断' }
const _EMPTY_PROFILE: MemoryProfile = { preferences: [], places: [], episodes: [] }

function MemorySection({ audioApi, sessionId, occupantId }: { audioApi: string; sessionId: string; occupantId: string }) {
  const { settings, update } = useSettings()
  const [mem, setMem] = useState<MemoryView>({ turns: [] })
  const [profile, setProfile] = useState<MemoryProfile>(_EMPTY_PROFILE)
  const [loading, setLoading] = useState(false)

  // 默认只看当前乘员（M-B）。此前面板把全部乘员的记忆混在一起列，而「删除」按钮
  // 又是按 scope 删的——看到的是别人的，删掉的是所有人的。
  const [delErr, setDelErr] = useState('')
  const [deletion, setDeletion] = useState<{ title: string; description: string; run: () => void } | null>(null)
  useEffect(() => { setDeletion(null) }, [occupantId])

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      fetchMemory(audioApi, sessionId, { userId: 'u1', occupantId }),
      fetchMemoryProfile(audioApi, 'u1', { occupantId }),
    ])
      .then(([m, p]) => { setMem(m); setProfile(p) })
      .catch(() => {/* 离线 */}).finally(() => setLoading(false))
  }, [audioApi, sessionId, occupantId])
  useEffect(() => { load() }, [load])

  const forget = useCallback(async (scope: string) => {
    const ok = await forgetMemory(audioApi, 'u1', scope)
    setDelErr(ok ? '' : '清除失败，请稍后重试')
    load()
  }, [audioApi, load])
  /** 删这一行：按 item id 精确删（L1）。受管条目引导去声纹设置，不在这里删。 */
  const delItem = useCallback(async (item: { id?: string; managed?: boolean }) => {
    setDelErr('')
    if (item.managed) { setDelErr('这是「乘员与声纹」里的称呼，改名或删除请到那里操作'); return }
    if (!item.id) { setDelErr('这条记忆缺少标识，请刷新后重试'); return }
    const r = await deleteMemoryItem(audioApi, 'u1', occupantId, item.id)
    if (!r.ok) {
      setDelErr(r.error === 'managed_memory'
        ? '这是「乘员与声纹」里的称呼，改名或删除请到那里操作'
        : r.error === 'not_found' ? '这条记忆已经不在了，正在刷新' : '删除失败，请稍后再试')
    }
    load()
  }, [audioApi, occupantId, load])
  const clearLocal = () => {
    try { Object.keys(localStorage).filter((k) => k.startsWith('cockpit.') && k !== 'cockpit.settings.v1').forEach((k) => localStorage.removeItem(k)) } catch {/* ignore */}
  }
  const hasProfile = profile.preferences.length + profile.places.length + profile.episodes.length > 0

  return (
    <div>
      <SectionHdr icon="memory" title="记忆" sub={`${settings.assistantName}记住的会话对话，与从交流中学到的偏好/常去地点/经历（云端硬删，不可恢复）`} />
      {deletion && <ConfirmDialog title={deletion.title} description={deletion.description} confirmLabel="确认删除"
        onCancel={() => setDeletion(null)} onConfirm={() => { const action = deletion; setDeletion(null); action.run() }} />}
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
            <div style={{ padding: '14px 0', fontSize: 'var(--au-type-body-size)', color: FG3 }}>暂无对话记忆。和助手聊几句后回来看看。</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 4 }}>
              {mem.turns.map((t, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 12px', borderRadius: 10, background: 'var(--au-fill)', border: '1px solid var(--au-fill)' }}>
                  <span style={{ fontSize: 'var(--au-type-caption-size)', fontWeight: 600, color: t.role === 'user' ? FG2 : TEAL, flexShrink: 0, width: 32 }}>{t.role === 'user' ? '你' : settings.assistantName}</span>
                  <span style={{ flex: 1, fontSize: 'var(--au-type-caption-size)', color: FG2, lineHeight: 1.55 }}>{t.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </SettingGroup>
      <HR />
      <SettingGroup title="学到的画像">
        <div style={{ paddingBottom: 16 }}>
          {!hasProfile ? (
            <div style={{ padding: '14px 0', fontSize: 'var(--au-type-body-size)', color: FG3 }}>还没记住什么。多聊聊偏好（如「我不吃辣」），助手会慢慢学到。</div>
          ) : (
            <>
              {profile.preferences.length > 0 && <MemCat title="偏好" items={profile.preferences.map(p => ({ text: p.text, meta: p.managed ? '声纹称呼（只读）' : (_PROV_LABEL[p.provenance] || p.provenance), onDel: p.managed ? undefined : () => setDeletion({title:'删除这条偏好？',description:`「${p.text}」将从云端删除，不能恢复。`,run:()=>void delItem(p)}) }))} />}
              {profile.places.length > 0 && <MemCat title="常去地点" items={profile.places.map(pl => ({ text: `${_PLACE_LABEL[pl.key] || pl.key}：${pl.name}`, meta: '常去地点 · 高敏', onDel: () => setDeletion({title:'删除这个常去地点？',description:`「${pl.name}」将从云端删除，不能恢复。`,run:()=>void delItem(pl)}) }))} />}
              {profile.episodes.length > 0 && <MemCat title="经历" items={profile.episodes.map(ep => ({ text: ep.text, meta: '经历' }))} />}
              {delErr && <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3, padding: '6px 0' }}>{delErr}</div>}
            </>
          )}
        </div>
      </SettingGroup>
      <SettingGroup title="危险操作">
        {profile.episodes.length > 0 && <SettingRow label="清除全部经历" sub="清除当前账号整个经历类别，包含其他乘员的经历；不是只删除上面的一行">
          <DangerBtn onClick={()=>setDeletion({title:'清除全部经历？',description:'当前账号整个经历类别（包含其他乘员）将从云端删除，不能恢复。偏好与常去地点保留。',run:()=>void forget('episodic.general')})}>清除全部经历</DangerBtn>
        </SettingRow>}
        <SettingRow label="清除全部记忆" sub="清除当前账号所有乘员的记忆及关联声纹身份数据；云端删除不能恢复">
          <DangerBtn onClick={()=>setDeletion({title:'清除全部记忆？',description:'当前账号所有乘员的记忆、会话记录及关联声纹身份数据将从云端删除，不能恢复。',run:()=>void forget('')})}>清除全部</DangerBtn>
        </SettingRow>
      </SettingGroup>
      <HR />
      <div style={{ padding: '14px 28px', display: 'flex', gap: 12, alignItems: 'center' }}>
        <GhostBtn onClick={clearLocal}>清除本机缓存</GhostBtn>
        <span style={{ fontSize: 'var(--au-type-caption-size)', color: FG3 }}>仅清空本地缓存，不含设置项与服务端记忆</span>
      </div>
    </div>
  )
}

function MemCat({ title, items }: { title: string; items: { text: string; meta: string; onDel?: () => void }[] }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 'var(--au-type-caption-size)', color: FG3, letterSpacing: '.06em', marginBottom: 7 }}>{title}</div>
      {items.map((m, i) => (
        <div key={i} className="au-memory-item">
          <span style={{ flex: 1, fontSize: 'var(--au-type-body-size)', color: FG2, lineHeight: 1.5 }}>{m.text}</span>
          <span style={{ fontSize: 'var(--au-type-caption-size)', color: FG3, flexShrink: 0 }}>{m.meta}</span>
          {m.onDel && <button className="au-icon-btn" onClick={m.onDel} aria-label={`删除：${m.text}`} title="删除"><Icon name="trash" size={28} /></button>}
        </div>
      ))}
    </div>
  )
}

function DeveloperSection({ audioApi, messages }: { audioApi: string; messages: Msg[] }) {
  const { settings, update, developerMode, setDeveloperMode, developerOptions, updateDeveloperOptions } = useSettings()
  const [tts, setTts] = useState<TtsProviderInfo[] | null>(null)
  const [llm, setLlm] = useState<LlmStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => {
    setLoading(true)
    const result = await Promise.allSettled([fetchTtsProviders(audioApi), fetchLlmProviders(audioApi)])
    if (result[0].status === 'fulfilled') setTts(result[0].value)
    if (result[1].status === 'fulfilled') setLlm(result[1].value)
    setLoading(false)
  }, [audioApi])
  useEffect(() => { if (developerMode) void refresh() }, [developerMode, refresh])
  const engine = tts?.find(p => p.id === settings.ttsProvider)
  return <div><SectionHdr icon="developer" title="开发者" sub="排查模型、语音链路与请求错误；诊断信息默认隐藏" />
    <SettingGroup title="调试">
      <SettingRow label="开发者模式" sub="打开后可查看技术详情，也可通过页面参数 ?dev 开启">
        <Toggle on={developerMode} onChange={setDeveloperMode} />
      </SettingRow>
      {developerMode && <>
        <SettingRow label="显示 trace 角标" sub="在回答旁显示请求标识，便于排查">
          <Toggle on={developerOptions.trace} onChange={trace => updateDeveloperOptions({trace})} />
        </SettingRow>
        <SettingRow label="显示原始错误" sub="错误块和最近错误区显示服务端原文">
          <Toggle on={developerOptions.rawErrors} onChange={rawErrors => updateDeveloperOptions({rawErrors})} />
        </SettingRow>
      </>}
    </SettingGroup>
    {developerMode && <>
      <SettingGroup title="链路">
        <SettingRow label="TTS 模型"><span className="au-setting-value">{engine?.model || '读不到'}{engine?.sample_rate ? ` · ${(engine.sample_rate / 1000).toFixed(1)} kHz` : ''}</span></SettingRow>
        <SettingRow label="ASR 模型"><span className="au-setting-value">{settings.asrProvider} · {settings.asrModel}</span></SettingRow>
        <SettingRow label="LLM 当前模型"><span className="au-setting-value">{llm?.active.provider || '读不到'} · {llm?.active.model || '读不到'}</span></SettingRow>
        {(llm?.providers || []).map(p => { const health = llmHealthDot(llm?.health?.[p.id]); return <SettingRow key={p.id} label={p.label} sub={developerOptions.rawErrors ? llm?.health?.[p.id]?.last_error : undefined}>
          <span className="au-setting-value"><span style={{color:health.color}}>●</span> {health.text}</span></SettingRow> })}
        <SettingRow label="VAD 静音尾" sub="停顿多久判定说完并发送；长句容易停顿时可调大">
          <Segmented value={settings.silenceTailMs} onChange={silenceTailMs => update({silenceTailMs})}
            options={[{value:500,label:'500 ms'},{value:800,label:'800 ms'},{value:1200,label:'1200 ms'}]} />
        </SettingRow>
        <SettingRow label="语音模型资源" sub="见 hmi/README.md · fetch-voice-models；声纹服务需要管理员准备资源后重启网关">
          <span className="au-setting-value">未探测</span>
        </SettingRow>
        <GhostBtn onClick={()=>void refresh()} disabled={loading}>{loading?'读取中…':'刷新链路信息'}</GhostBtn>
      </SettingGroup>
      <SettingGroup title="最近错误">{developerOptions.rawErrors
        ? <pre className="au-developer-log">{messages.filter(m=>m.error).slice(-8).map(m=>`${m.traceId || '无 trace'}  ${m.text}`).join('\n\n') || '当前会话没有错误记录'}</pre>
        : <p className="au-setting-note">打开「显示原始错误」后可查看当前会话的错误原文。</p>}</SettingGroup>
    </>}
  </div>
}
