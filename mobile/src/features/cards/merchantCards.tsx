// 商户 / 支付族卡片（M3-1）：payment_qr / payment_receipt / parking_fee /
// mcp_order / mcp_result / merchant_checkout（含 merchant_choices、merchant_order_preview 两别名）。
//
// 全部业务判定复用 `@shared/merchantUi.mjs`（品牌归一、行项归一、金额分转元、按钮合成、
// 图片与支付链接的 https 白名单、规格 chip 句式）——**App 侧只做渲染**。
// 之所以一条都不在这里重写：两个客户端对「这张卡该显示什么」给出不同答案，
// 是比任何一侧写错都更难发现的缺陷（同 §10 共享面纪律）。
//
// ⚠ 安全面两条，改这个文件前先读：
//  ① `merchantImageUrl` / `paymentPresentation` 里的 `new URL()` 在 RN 上走的是
//     react-native/Libraries/Blob/URL.js（残缺实现，非 spec）。已逐条核过：`protocol`
//     getter 真实存在，垃圾串在构造期抛→被 catch，两种路径都 **fail closed**。
//     结论是「可以复用」，不是「没有差异」——test/merchantCards.test.ts 钉住这三条。
//  ② `checkout_token` 可能在卡数据里，**绝不渲染**（types.ts:232 段注释）。本文件不读它。
import * as Clipboard from 'expo-clipboard'
import { useEffect, useState } from 'react'
import { Image, Linking, Pressable, Text, TurboModuleRegistry, View } from 'react-native'
import { SvgXml } from 'react-native-svg'

import {
  merchantActionButtons,
  merchantImageUrl,
  normalizeMerchantOrder,
  paymentPresentation,
  specChipAction,
} from '@shared/merchantUi.mjs'

import { base64ToBytes } from '../../core/voice/base64'
import type { Palette } from '../../ui/theme'
import { CardButtons, CardShell, Chip, KV, ProvBadge, type SendFn } from './parts'

/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * react-native-svg 的**原生**侧是否在场（M3-1，2026-08-27 真机实测逼出来的）。
 *
 * 为什么必须显式探测，而不是交给 `CardBoundary` 兜：
 * 原生 ViewManager 缺席时，Fabric 在**挂载期、原生线程**抛
 * `IllegalViewOperationException: ViewManagerRegistry.get(RNSVGSvgViewAndroid)`——
 * 那不是 React 的渲染异常，`getDerivedStateFromError` **根本不会触发**，
 * 结果是整个 dev-client 红屏，而不是这一张卡掉兜底卡。
 * ⇒ **「单卡异常不许抛崩整个列表」这条铁则只覆盖 JS 侧异常**，原生组件缺席是它的盲区。
 *
 * 触发它的真实场景不止「装了新 JS 配旧 APK」这一种开发态：
 * M5 规划里的 expo-updates OTA 同样只推 JS 不推原生，那时新增任何原生支撑的卡片
 * 都会以完全一样的形态炸掉整屏。所以这不是一次性的开发期补丁。
 *
 * 探测走 TurboModule 而不是渲染试错：拿不到就是没链接进来，零副作用。
 */
const SVG_NATIVE_READY: boolean = (() => {
  try {
    return TurboModuleRegistry.get('RNSVGSvgViewModule') != null
  } catch {
    return false
  }
})()

/** 演示商户角标（「三重冗余」的第二重——后端标了，前端得有出口） */
function DemoBadge({ p, card }: { p: Palette; card: any }) {
  if (!card?.demo) return null
  return <Chip p={p} tone="amber" text={card.demo_label || '演示商户'} />
}

/** 品牌胶囊 + 标题 + 角标的公共头 */
function MerchantHead({
  p,
  brand,
  title,
  card,
}: {
  p: Palette
  brand: string
  title: string
  card: any
}) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <Chip p={p} tone="accent" text={brand} />
      <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '700' }}>{title}</Text>
      <DemoBadge p={p} card={card} />
      <View style={{ flex: 1 }} />
      <ProvBadge p={p} prov={card?._prov} />
    </View>
  )
}

/** 行项（名称 · 规格 ×数量），mcp_order 与 merchant_checkout 共用 */
function LineItems({ p, items }: { p: Palette; items: Array<{ name: string; quantity: number; specs: string }> }) {
  return (
    <>
      {items.map((it, i) => (
        <View key={`${it.name}:${i}`} style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start' }}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: p.fg1, fontSize: p.font(13) }}>{it.name}</Text>
            {it.specs ? (
              <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 2 }}>{it.specs}</Text>
            ) : null}
          </View>
          <Text style={{ color: p.fg2, fontSize: p.font(12) }}>×{it.quantity}</Text>
        </View>
      ))}
    </>
  )
}

/** 虚线分隔的信息区（对齐 HMI 的 dashed 上下边） */
function DashedBlock({ p, children }: { p: Palette; children: React.ReactNode }) {
  return (
    <View
      style={{
        borderTopWidth: 1,
        borderBottomWidth: 1,
        borderStyle: 'dashed',
        borderColor: p.line,
        paddingVertical: 10,
        gap: 6,
      }}
    >
      {children}
    </View>
  )
}

// ───────────────────────────── payment_qr ─────────────────────────────

/** data:image/svg+xml;base64,… → SVG 源码。非该形态或解不出一律返回空串（调用方降级） */
export function decodeSvgDataUri(dataUri: string): string {
  const m = /^data:image\/svg\+xml;base64,(.+)$/.exec((dataUri || '').trim())
  if (!m) return ''
  try {
    const bytes = base64ToBytes(m[1])
    // 自己解 UTF-8：Hermes 没有 TextDecoder（RN 0.86 core 里也没有 polyfill，实测已确认）。
    // 付款码 SVG 由 qrcode.image.svg.SvgPathImage 生成、纯 ASCII，但不拿产生方的
    // 当前实现当契约——多写十行，换掉一个「哪天它带个中文注释就乱码」的假设。
    let out = ''
    for (let i = 0; i < bytes.length; ) {
      const b = bytes[i]
      if (b < 0x80) {
        out += String.fromCharCode(b)
        i += 1
      } else if (b >= 0xc0 && b < 0xe0 && i + 1 < bytes.length) {
        out += String.fromCharCode(((b & 0x1f) << 6) | (bytes[i + 1] & 0x3f))
        i += 2
      } else if (b >= 0xe0 && b < 0xf0 && i + 2 < bytes.length) {
        out += String.fromCharCode(((b & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f))
        i += 3
      } else if (b >= 0xf0 && i + 3 < bytes.length) {
        const cp =
          ((b & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f)
        const v = cp - 0x10000
        out += String.fromCharCode(0xd800 + (v >> 10), 0xdc00 + (v & 0x3ff))
        i += 4
      } else {
        return '' // 截断/非法字节：整体判失败，宁可降级到安全链接也不渲半张码
      }
    }
    return out.includes('<svg') ? out : ''
  } catch {
    return ''
  }
}

export function PaymentQr({ p, card, onSend }: { p: Palette; card: any; onSend: SendFn }) {
  const [now, setNow] = useState(() => Date.now())
  const [copied, setCopied] = useState(false)
  const expiresAt = Number(card.expires_at_ms || 0)
  const expired = expiresAt > 0 && now >= expiresAt
  const presentation = paymentPresentation(card)
  const actions = merchantActionButtons(card)
  // 原生 svg 不在场就当作「没有码」，走下面那条安全链接分支——那本来就是契约里的
  // 合法降级路径（paymentPresentation 的 hasQr=false 分支），不是为此新造的兜底。
  const svg = presentation.hasQr && SVG_NATIVE_READY ? decodeSvgDataUri(card.qr_svg) : ''
  // ⚠ 「有码但渲不出」必须**说出来**：presentation 仍然是扫码档（title=扫码支付、
  // hint=请用手机扫码），而屏上没有码 —— 不说明就是在让用户去扫一个不存在的东西。
  // 数据真实性纪律在这里同样成立：宁可承认显示不了，不许摆一句做不到的指示。
  const qrBlocked = presentation.hasQr && !svg

  useEffect(() => {
    if (!expiresAt || expired) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [expiresAt, expired])

  const remain = expiresAt > 0 ? Math.max(0, Math.floor((expiresAt - now) / 1000)) : 0
  const mm = String(Math.floor(remain / 60)).padStart(2, '0')
  const ss = String(remain % 60).padStart(2, '0')

  const copyLink = async () => {
    if (!presentation.safeUrl || expired) return
    try {
      await Clipboard.setStringAsync(presentation.safeUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return (
    <CardShell p={p} title={presentation.title} right={<ProvBadge p={p} prov={card._prov} />}>
      <Text style={{ color: p.fg1, fontSize: p.font(20), fontWeight: '800' }}>{card.amount}</Text>

      {svg ? (
        <View style={{ alignItems: 'center' }}>
          <View
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 12,
              padding: 10,
              opacity: expired ? 0.35 : 1,
            }}
          >
            <SvgXml xml={svg} width={180} height={180} />
          </View>
          {expired ? (
            <View
              style={{
                position: 'absolute',
                top: '40%',
                backgroundColor: p.amber,
                paddingHorizontal: 12,
                paddingVertical: 4,
                borderRadius: 8,
              }}
            >
              <Text style={{ color: '#3A2604', fontSize: p.font(13), fontWeight: '700' }}>已过期</Text>
            </View>
          ) : null}
        </View>
      ) : presentation.safeUrl && !expired ? (
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Pressable
            onPress={() => void Linking.openURL(presentation.safeUrl)}
            style={{
              flex: 1,
              minHeight: 44,
              borderRadius: 10,
              backgroundColor: p.accentSoft,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: p.accent, fontSize: p.font(13), fontWeight: '700' }}>打开安全支付链接</Text>
          </Pressable>
          <Pressable
            onPress={() => void copyLink()}
            style={{
              minHeight: 44,
              paddingHorizontal: 13,
              borderRadius: 10,
              borderWidth: 1,
              borderColor: p.line,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: copied ? p.green : p.fg2, fontSize: p.font(12) }}>
              {copied ? '已复制' : '复制链接'}
            </Text>
          </Pressable>
        </View>
      ) : null}

      {/* ⚠ 过期时不说这句：卡片已经在下面说「支付入口已过期」，再解释码为什么显示不了是噪声。
          ⚠ 「请用上面的链接」**只在链接真的渲染出来时才说**——链接那一段的条件是
          `safeUrl && !expired`，少判一个 expired 就会指向一个不存在的按钮。
          2026-08-27 真机实测：这句话第一版就是这么错的（样本过期后仍在让用户去按不存在的按钮），
          而它恰好是本段代码要消灭的那类假话。**修一句假话的代码自己也会说假话。** */}
      {qrBlocked && !expired ? (
        <Text style={{ color: p.amber, fontSize: p.font(12), textAlign: 'center' }}>
          {SVG_NATIVE_READY ? '付款码解析失败' : '本机暂时无法显示二维码'}
          {presentation.safeUrl ? '，请用上面的安全支付链接' : '，请到商家应用内完成支付'}
        </Text>
      ) : null}
      <Text style={{ color: p.fg3, fontSize: p.font(12), textAlign: 'center' }}>
        {expired
          ? '支付入口已过期，请重新发起'
          : qrBlocked
            ? expiresAt > 0
              ? `${mm}:${ss} 后过期`
              : ''
            : expiresAt > 0
              ? `${presentation.hint} · ${mm}:${ss} 后过期`
              : presentation.hint}
      </Text>
      {card.merchant_note ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11), textAlign: 'center' }}>{card.merchant_note}</Text>
      ) : null}
      <CardButtons p={p} onSend={onSend} buttons={actions} />
    </CardShell>
  )
}

// ─────────────────────── payment_receipt / parking_fee ───────────────────────

export function PaymentReceipt({ p, card }: { p: Palette; card: any; onSend: SendFn }) {
  return (
    <CardShell p={p} title="支付成功" right={<ProvBadge p={p} prov={card._prov} />}>
      {card.amount ? (
        <Text style={{ color: p.green, fontSize: p.font(22), fontWeight: '800' }}>✓ {card.amount}</Text>
      ) : (
        <Text style={{ color: p.green, fontSize: p.font(18), fontWeight: '700' }}>✓ 已收款</Text>
      )}
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
        凭证号 {card.receipt_id}
        {card.order_id ? ` · 订单 ${card.order_id}` : ''}
      </Text>
    </CardShell>
  )
}

/** 停车费查询卡（只读，一分钱不动） */
export function ParkingFee({ p, card }: { p: Palette; card: any; onSend: SendFn }) {
  return (
    <CardShell p={p} title="当前停车费">
      <Text style={{ color: p.fg1, fontSize: p.font(22), fontWeight: '800' }}>{card.amount}</Text>
      <KV p={p} k="车牌" v={card.plate} />
      <KV p={p} k="订单" v={card.order_id} />
    </CardShell>
  )
}

// ───────────────────────── mcp_order / mcp_result ─────────────────────────

export function McpOrder({ p, card, onSend }: { p: Palette; card: any; onSend: SendFn }) {
  const order = normalizeMerchantOrder(card)
  const actions = merchantActionButtons(card)
  const sku = typeof card.sku === 'string' ? card.sku : ''
  const size = typeof card.size === 'string' ? card.size : ''
  return (
    <CardShell p={p}>
      <MerchantHead
        p={p}
        brand={order.brand}
        title={card.type === 'mcp_order' ? '商户订单' : '商户服务'}
        card={card}
      />
      <DashedBlock p={p}>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>订单号</Text>
          <View style={{ flex: 1 }} />
          {order.status ? <Text style={{ color: p.amber, fontSize: p.font(11) }}>{order.status}</Text> : null}
        </View>
        <Text style={{ color: p.fg1, fontSize: p.font(12), fontWeight: '700' }}>
          {order.orderId || '待商户回传'}
        </Text>
        {order.storeName ? (
          <Text style={{ color: p.fg2, fontSize: p.font(12) }}>门店 · {order.storeName}</Text>
        ) : null}
        {order.fulfillment ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>取餐 · {order.fulfillment}</Text>
        ) : null}
        <LineItems p={p} items={order.items} />
        {(sku || size) && !order.items.length ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            {sku}
            {size ? ` · ${size}` : ''}
          </Text>
        ) : null}
      </DashedBlock>
      {order.amount || card.duplicate === true ? (
        <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
          {order.amount ? (
            <>
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>应付</Text>
              <Text style={{ color: p.fg1, fontSize: p.font(18), fontWeight: '800' }}>{order.amount}</Text>
            </>
          ) : null}
          <View style={{ flex: 1 }} />
          {card.duplicate === true ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>已有订单 · 幂等命中</Text>
          ) : null}
        </View>
      ) : null}
      <CardButtons p={p} onSend={onSend} buttons={actions} />
    </CardShell>
  )
}

/** 只读商户工具的结果卡（QA I-022）。**刻意极简**——内容由 speech 承载
 *  （这些工具都是 speech_mode: summarize），卡片只留后端保证、话术说不清的三件事：
 *  品牌 / 演示角标 / 数据真实性。订单号、状态、应付、按钮一个都不出——
 *  一次营养成分查询里没有它们的对应物，渲染出来就是无中生有。 */
export function McpInfo({ p, card }: { p: Palette; card: any; onSend: SendFn }) {
  const order = normalizeMerchantOrder(card)
  return (
    <CardShell p={p}>
      <MerchantHead p={p} brand={order.brand} title="商户信息" card={card} />
      {typeof card.tool === 'string' && card.tool ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>来源 · {card.tool}</Text>
      ) : null}
    </CardShell>
  )
}

/** mcp_result：readonly=true 走信息卡，否则同订单卡（HMI Cards.tsx:182-184 同判据） */
export function McpResult({ p, card, onSend }: { p: Palette; card: any; onSend: SendFn }) {
  return card.readonly ? (
    <McpInfo p={p} card={card} onSend={onSend} />
  ) : (
    <McpOrder p={p} card={card} onSend={onSend} />
  )
}

// ───────────────────────── merchant_checkout 族 ─────────────────────────

function centsLabel(value: unknown): string {
  const cents = Number(value)
  if (!Number.isInteger(cents) || cents < 0) return ''
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}元`
}

/** 选项行（商品/门店）：可点 + 可选商品图。图加载失败就把自己摘掉——
 *  车机与手机同理，一张裂图比没有图更糟。 */
function OptionRow({
  p,
  label,
  subtitle,
  imageUrl,
  onPress,
}: {
  p: Palette
  label: string
  subtitle?: string
  imageUrl: string
  onPress: () => void
}) {
  const [broken, setBroken] = useState(false)
  return (
    <Pressable
      onPress={onPress}
      style={{
        minHeight: 52,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
        paddingHorizontal: 12,
        paddingVertical: 9,
        borderRadius: 12,
        borderWidth: 1,
        borderColor: p.line,
        backgroundColor: p.panel,
      }}
    >
      {imageUrl && !broken ? (
        <Image
          source={{ uri: imageUrl }}
          onError={() => setBroken(true)}
          style={{ width: 40, height: 40, borderRadius: 8, backgroundColor: p.line }}
        />
      ) : null}
      <View style={{ flex: 1 }}>
        <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '700' }} numberOfLines={2}>
          {label}
        </Text>
        {subtitle ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 3 }} numberOfLines={2}>
            {subtitle}
          </Text>
        ) : null}
      </View>
    </Pressable>
  )
}

export function MerchantCheckout({ p, card, onSend }: { p: Palette; card: any; onSend: SendFn }) {
  const order = normalizeMerchantOrder(card)
  const isChoices = card.type === 'merchant_choices' || card.stage === 'choices'
  const title =
    card.title ||
    (isChoices
      ? `选择${order.brand}${card.choice_kind === 'store' ? '门店' : '商品'}`
      : card.stage === 'cancel'
        ? `${order.brand}取消订单`
        : `${order.brand}订单预览`)

  // 选项与按钮的配对沿用 HMI 的按序号取（Cards.tsx:1856-1858 同形态）——
  // 两个客户端对同一张卡给出不同选项顺序，比任何一侧写错都难发现。
  const options: any[] =
    Array.isArray(card.options) && card.options.length
      ? card.options
      : isChoices && Array.isArray(card.items)
        ? card.items
        : []
  const optionButtons: Array<{ label: string; send_text: string }> =
    Array.isArray(card.buttons) && card.buttons.length
      ? merchantActionButtons({ buttons: card.buttons })
      : merchantActionButtons({ options: card.options || [] })
  const regularButtons = merchantActionButtons({ ...card, options: [] })
  const discount =
    typeof card.discount === 'string' && card.discount.trim()
      ? card.discount.trim()
      : centsLabel(card.discount_cents)

  return (
    <CardShell p={p} title={title} right={<ProvBadge p={p} prov={card._prov} />}>
      {isChoices ? (
        <View style={{ gap: 8 }}>
          {typeof card.total === 'number' && card.total > optionButtons.length ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
              在售共 {card.total} 款，这里是一页——按分类看或直接报名字
            </Text>
          ) : null}
          {Array.isArray(card.categories) && card.categories.length ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
              {card.categories.map((cat: any) =>
                cat?.label && cat?.send_text ? (
                  <Pressable
                    key={cat.label}
                    onPress={() => onSend(cat.send_text)}
                    style={{
                      paddingHorizontal: 10,
                      paddingVertical: 3,
                      borderRadius: 999,
                      borderWidth: 1,
                      borderColor: p.accent,
                    }}
                  >
                    <Text style={{ color: p.accent, fontSize: p.font(11), fontWeight: '600' }}>{cat.label}</Text>
                  </Pressable>
                ) : null,
              )}
            </View>
          ) : null}
          {optionButtons.map((button, index) => (
            <OptionRow
              key={`${button.label}:${button.send_text}`}
              p={p}
              label={button.label}
              subtitle={options[index]?.subtitle}
              imageUrl={merchantImageUrl(options[index]?.image_url)}
              onPress={() => onSend(button.send_text)}
            />
          ))}
        </View>
      ) : (
        <>
          <DashedBlock p={p}>
            {order.storeName ? (
              <Text style={{ color: p.fg2, fontSize: p.font(12) }}>门店 · {order.storeName}</Text>
            ) : null}
            <LineItems p={p} items={order.items} />
            {order.fulfillment ? (
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>取餐方式 · {order.fulfillment}</Text>
            ) : null}
          </DashedBlock>

          {Array.isArray(card.spec_options) && card.spec_options.length ? (
            <View style={{ gap: 7 }}>
              {card.spec_options.map((group: any) => (
                <View
                  key={group.name}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}
                >
                  <Text style={{ color: p.fg3, fontSize: p.font(11), minWidth: 28 }}>{group.name}</Text>
                  {(group.options || []).map((opt: any) => {
                    if (!opt?.label) return null
                    const active = opt.label === group.selected
                    const action = specChipAction(card, group, opt.label)
                    const clickable = !active && !!action
                    const delta =
                      typeof opt.price_delta_cents === 'number' && opt.price_delta_cents > 0
                        ? ` +${(opt.price_delta_cents / 100).toFixed(opt.price_delta_cents % 100 ? 1 : 0)}元`
                        : ''
                    return (
                      <Pressable
                        key={opt.label}
                        disabled={!clickable}
                        onPress={clickable ? () => onSend(action!.send_text) : undefined}
                        style={{
                          paddingHorizontal: 10,
                          paddingVertical: 3,
                          borderRadius: 999,
                          borderWidth: 1,
                          borderColor: active ? p.accent : p.line,
                          backgroundColor: active ? p.accent : 'transparent',
                        }}
                      >
                        <Text
                          style={{
                            color: active ? '#FFFFFF' : p.fg2,
                            fontSize: p.font(11),
                            fontWeight: '600',
                          }}
                        >
                          {opt.label}
                          {delta}
                        </Text>
                      </Pressable>
                    )
                  })}
                </View>
              ))}
            </View>
          ) : null}

          {discount || order.amount ? (
            <View style={{ gap: 4 }}>
              {discount ? (
                <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                  <Text style={{ color: p.fg3, fontSize: p.font(11) }}>优惠</Text>
                  <Text style={{ color: p.fg3, fontSize: p.font(11) }}>-{discount}</Text>
                </View>
              ) : null}
              {order.amount ? (
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Text style={{ color: p.fg2, fontSize: p.font(12) }}>实付</Text>
                  <Text style={{ color: p.fg1, fontSize: p.font(20), fontWeight: '800' }}>{order.amount}</Text>
                </View>
              ) : null}
            </View>
          ) : null}

          <CardButtons p={p} onSend={onSend} buttons={regularButtons} />
        </>
      )}
    </CardShell>
  )
}
