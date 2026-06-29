// P0 设计系统预览沙盒——验证 tokens / 光球三态 / 玻璃 / 极光描边 / 置信度 / AQI / 动效。
// 仅在 URL 带 ?aurora 时挂载（见 main.tsx），不进入正式应用主链。
import { useState } from 'react'
import { AuroraOrb, Glass, AuroraBorder, ConfBadge, CatChip, AQISection } from './index'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 40 }}>
      <h2 style={{ fontSize: 13, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--au-text-3)', margin: '0 0 16px' }}>
        {title}
      </h2>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-start' }}>{children}</div>
    </section>
  )
}

function Swatch({ name, color }: { name: string; color: string }) {
  return (
    <div style={{ width: 96 }}>
      <div style={{ height: 56, borderRadius: 12, background: color, border: '1px solid var(--au-line-2)' }} />
      <div style={{ fontSize: 11, color: 'var(--au-text-2)', marginTop: 6 }}>{name}</div>
    </div>
  )
}

export function AuroraPreview() {
  const [driving, setDriving] = useState(false)

  return (
    <div
      style={{
        position: 'relative',
        minHeight: '100vh',
        background: 'var(--au-bg)',
        color: 'var(--au-text)',
        fontFamily: 'var(--au-font-ui)',
        overflow: 'hidden',
      }}
    >
      {/* 活的场景做底：极光氛围，供玻璃折射 */}
      <div aria-hidden style={{ position: 'fixed', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
        <div style={{ position: 'absolute', top: '-10%', left: '12%', width: 520, height: 520, borderRadius: '50%', background: 'radial-gradient(circle, rgba(91,140,255,0.35), transparent 70%)', filter: 'blur(60px)' }} />
        <div style={{ position: 'absolute', bottom: '-12%', right: '8%', width: 560, height: 560, borderRadius: '50%', background: 'radial-gradient(circle, rgba(154,107,255,0.30), transparent 70%)', filter: 'blur(70px)' }} />
        <div style={{ position: 'absolute', top: '30%', right: '32%', width: 360, height: 360, borderRadius: '50%', background: 'radial-gradient(circle, rgba(91,233,255,0.18), transparent 70%)', filter: 'blur(60px)' }} />
      </div>

      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1280, margin: '0 auto', padding: 40 }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 40 }}>
          <AuroraOrb size={56} state="idle" driving={driving} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 22, fontWeight: 600 }}>
              Aurora Glass · <span className="au-aurora-text">极光液态座舱</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--au-text-2)' }}>P0 设计系统地基预览 · 设计契约 v1.0</div>
          </div>
          <button
            onClick={() => setDriving((d) => !d)}
            className="au-glass"
            style={{ padding: '10px 16px', color: 'var(--au-text)', fontFamily: 'inherit', cursor: 'pointer', borderRadius: 999 }}
          >
            行车态模拟：{driving ? '开' : '关'}
          </button>
        </div>

        <Section title="深空底色 / 文字层级">
          <Swatch name="bg #06080F" color="var(--au-bg)" />
          <Swatch name="space-800" color="var(--au-space-800)" />
          <Swatch name="space-600" color="var(--au-space-600)" />
          <Swatch name="text 92%" color="var(--au-text)" />
          <Swatch name="text 56%" color="var(--au-text-2)" />
          <Swatch name="primary #46D6E0" color="var(--au-primary)" />
        </Section>

        <Section title="语义色（A股红涨绿跌 · 置信 · 在线/警告/危险）">
          <Swatch name="涨 up" color="var(--au-up)" />
          <Swatch name="跌 down" color="var(--au-down)" />
          <Swatch name="conf-high" color="var(--au-conf-high)" />
          <Swatch name="conf-mid" color="var(--au-conf-mid)" />
          <Swatch name="online" color="var(--au-online)" />
          <Swatch name="danger" color="var(--au-danger)" />
        </Section>

        <Section title="AI 签名渐变（极光）— 仅 5 处 AI 时刻">
          <div style={{ width: '100%', height: 18, borderRadius: 999, background: 'var(--au-aurora)' }} />
          <div style={{ fontSize: 32, fontWeight: 700 }}>
            <span className="au-aurora-text">小舟正在为你思考</span>
          </div>
        </Section>

        <Section title="小舟 AuroraOrb 三态（idle / thinking / speaking）">
          {(['idle', 'thinking', 'speaking'] as const).map((s) => (
            <div key={s} style={{ textAlign: 'center' }}>
              <AuroraOrb size={88} state={s} driving={driving} />
              <div style={{ fontSize: 12, color: 'var(--au-text-2)', marginTop: 12 }}>{s}</div>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <AuroraOrb size={24} state="idle" />
            <AuroraOrb size={40} state="thinking" />
            <AuroraOrb size={64} state="speaking" />
          </div>
        </Section>

        <Section title="液态玻璃面板 + 仪表级等宽数字">
          <Glass style={{ width: 260 }}>
            <div style={{ fontSize: 13, color: 'var(--au-text-2)' }}>杭州 · 多云转阵雨</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, margin: '6px 0' }}>
              <span className="au-num" style={{ fontSize: 48, fontWeight: 700 }}>28</span>
              <span style={{ fontSize: 18, color: 'var(--au-text-2)' }}>°C</span>
            </div>
            <div style={{ display: 'flex', gap: 12, fontSize: 13, color: 'var(--au-text-2)' }}>
              <span>湿度 <b className="au-num" style={{ color: 'var(--au-text)' }}>65%</b></span>
              <span>能见度 <b className="au-num" style={{ color: 'var(--au-text)' }}>24km</b></span>
            </div>
          </Glass>
          <Glass style={{ width: 220 }}>
            <div style={{ fontSize: 13, color: 'var(--au-text-2)' }}>贵州茅台 · 600519</div>
            <div className="au-num" style={{ fontSize: 34, fontWeight: 700, color: 'var(--au-up)', margin: '6px 0' }}>1689.00</div>
            <div className="au-num" style={{ fontSize: 14, color: 'var(--au-up)' }}>+12.50 +0.75%</div>
          </Glass>
        </Section>

        <Section title="AI 内容虹彩描边（深度调研报告等 AI 出品）">
          <AuroraBorder r={24} style={{ width: 320 }}>
            <Glass style={{ width: '100%' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <AuroraOrb size={18} state="idle" />
                <span className="au-aurora-text" style={{ fontSize: 12, fontWeight: 600 }}>AI · 深度调研</span>
              </div>
              <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 6 }}>固态电池 2027 年量产可行性</div>
              <div style={{ fontSize: 13, color: 'var(--au-text-2)', lineHeight: 1.6 }}>
                技术路线已基本确立，但量产良率与成本控制仍是决定性障碍。
              </div>
            </Glass>
          </AuroraBorder>
        </Section>

        <Section title="置信度 / 类别芯片">
          <ConfBadge level="high" />
          <ConfBadge level="medium" />
          <ConfBadge level="low" />
          <CatChip cat="科技" color="var(--au-primary)" />
          <CatChip cat="财经" color="var(--au-conf-mid)" />
          <CatChip cat="学术" color="var(--au-violet)" />
        </Section>

        <Section title="AQI 7 档色阶">
          <Glass style={{ width: 360 }}>
            <AQISection aqi={68} category="良" />
          </Glass>
          <Glass style={{ width: 360 }}>
            <AQISection aqi={168} category="中度" />
          </Glass>
        </Section>

        <Section title="标志性动效（思考律动 / 流式虹彩光标 / 刚变闪动）">
          <Glass style={{ width: 200, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="au-think-dots"><i /><i /><i /></span>
            <span style={{ fontSize: 13, color: 'var(--au-text-2)' }}>正在思考…</span>
          </Glass>
          <Glass style={{ width: 280 }}>
            <span style={{ fontSize: 14 }}>固态电池的主要技术路线有三种<span className="au-cursor" /></span>
          </Glass>
          <Glass className="au-flash" style={{ width: 160, textAlign: 'center' }}>
            <span className="au-num" style={{ fontSize: 20, fontWeight: 700 }}>26°C</span>
            <div style={{ fontSize: 12, color: 'var(--au-text-2)' }}>刚变高亮</div>
          </Glass>
        </Section>
      </div>
    </div>
  )
}
