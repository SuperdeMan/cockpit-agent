// reanimated 的手写 mock（AR03 起，组件交互测试共用）。
//
// 为什么不用 `react-native-reanimated/mock`：reanimated 4 的那份 mock 自己 `import` 了真包，
// 于是在 jest 里照样走到 `react-native-worklets` 的原生初始化（`loadUnpackers` 读不到 TurboModule）
// ——「有一份官方 mock」不等于「这份 mock 在这套环境里能用」。
//
// 覆盖面就是 `src/ui/aurora/*` 与 `VoiceSheet` 真正 import 的那些符号；动画**不在任何断言里**，
// 交互测试问的是「哪枚键、按到哪个回调、在不在场」。加了新的 reanimated API 而这里没有，
// 表现是 undefined 立刻炸，不会静默过。
import { View, Text, ScrollView } from 'react-native'

 

const identity = (v: any) => v
const value = (v: any) => (v && typeof v === 'object' && 'value' in v ? v.value : v)

export const useSharedValue = (initial: any) => ({ value: initial })
export const useAnimatedStyle = (fn: () => any) => fn()
export const withTiming = (to: any) => value(to)
export const withSpring = (to: any) => value(to)
export const withDelay = (_ms: number, animation: any) => animation
export const withRepeat = (animation: any) => animation
export const withSequence = (...animations: any[]) => animations[animations.length - 1]
export const cancelAnimation = () => {}
export const Easing = {
  linear: identity,
  ease: identity,
  bezier: () => identity,
  inOut: identity,
  out: identity,
  in: identity,
  quad: identity,
  cubic: identity,
  sin: identity,
}

// RNGH 的 GestureDetector 在「reanimated 在场」时会走 useAnimatedGesture 那条路，
// 它要 useEvent / useSharedValue。给一个不做事的事件句柄即可：手势本身在 jest 里不会触发，
// 用例按的是 Pressable 的 onPress。
export const useEvent = () => () => {}
export const useHandler = () => ({ context: {}, doDependenciesDiffer: false, useWeb: false })
export const runOnJS = identity
export const runOnUI = identity
export const isSharedValue = (v: any) => !!v && typeof v === 'object' && 'value' in v
export const makeMutable = (v: any) => ({ value: v })

const Animated = { View, Text, ScrollView, createAnimatedComponent: identity }
export default Animated
