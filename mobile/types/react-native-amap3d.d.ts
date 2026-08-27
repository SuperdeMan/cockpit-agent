// react-native-amap3d 的类型声明（M3-3）。
//
// 为什么自己声明而不是用包自带的：它的 `main` 指向**未编译的 TS 源码**（`lib/src`），
// tsc 会跟进去逐文件检查，而那份源码是 2023 年写的、过不了 RN 0.86 的类型
// （`cluster-view.tsx` 把 `ViewStyle` 传给 `<Text>`，`userSelect` 类型不兼容）。
// `skipLibCheck` 管不着它——那只跳过 `.d.ts`，这是 `.tsx` 源码。
// ⇒ tsconfig 的 `paths` 把**类型解析**重定向到本文件；运行时解析仍走 metro 的正常路径。
//
// 只声明用得到的三个出口。写全等于替一个我们不维护的库背类型书，
// 而**只声明用到的那部分，本身就是一份「我们依赖它的哪些面」的清单**
// ——哪天换库，要对齐的就是这个文件里的东西。
declare module 'react-native-amap3d' {
  import type { ComponentType, ReactNode, Ref } from 'react'
  import type { LayoutChangeEvent, NativeSyntheticEvent, ViewStyle } from 'react-native'

  export interface LatLng {
    latitude: number
    longitude: number
  }

  export interface CameraPosition {
    target?: LatLng
    zoom?: number
    bearing?: number
    tilt?: number
  }

  /** 西南/东北两角（`lib/src/types.ts::LatLngBounds`） */
  export interface LatLngBounds {
    southwest: LatLng
    northeast: LatLng
  }

  /** 相机事件负载（`lib/src/map-view.tsx::CameraEvent`） */
  export interface CameraEvent {
    cameraPosition: CameraPosition
    latLngBounds: LatLngBounds
  }

  export interface MapViewProps {
    style?: ViewStyle
    initialCameraPosition?: CameraPosition
    myLocationEnabled?: boolean
    children?: ReactNode
    /** 点空白处（`map-view.tsx:123`）——本项目用它收起 marker 详情 */
    onPress?: (event: NativeSyntheticEvent<LatLng>) => void
    /** 相机停下（`map-view.tsx:143`）——本项目用它跟踪当前 zoom */
    onCameraIdle?: (event: NativeSyntheticEvent<CameraEvent>) => void
    /** MapViewProps extends ViewProps（`map-view.tsx:16`），布局回调随之而来；
     *  本项目用它拿真实视口尺寸算「装进画面」的 zoom */
    onLayout?: (event: LayoutChangeEvent) => void
  }

  /** 命令式把手（本项目只用 moveCamera 做「回中」） */
  export interface MapViewHandle {
    moveCamera(position: CameraPosition, duration?: number): void
  }

  export const MapView: ComponentType<MapViewProps & { ref?: Ref<MapViewHandle> }>

  export interface MarkerProps {
    position: LatLng
    title?: string
    subtitle?: string
    children?: ReactNode
    /** ⚠ 无参数（`marker.tsx:72`）——是哪个 marker 被点，只能靠调用方闭包捕获 */
    onPress?: () => void
  }

  export const Marker: ComponentType<MarkerProps>

  export interface PolylineProps {
    points: LatLng[]
    color?: string
    width?: number
  }

  export const Polyline: ComponentType<PolylineProps>

  export namespace AMapSdk {
    /** Android 上 key 走 AndroidManifest 注入，此处传空即可；
     *  它同时做高德 9.x 必需的隐私合规调用（SdkModule.kt:20-23）。 */
    function init(apiKey?: string): void
    function getVersion(): Promise<string>
  }
}
