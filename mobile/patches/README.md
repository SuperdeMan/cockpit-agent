# Native dependency patches

`npm ci` / `npm install` 通过 `postinstall: patch-package` 应用补丁。只修改依赖的源文件，
生成时必须用 `--include` 限定范围，不能把构建产物或下载的二进制收进 patch。

## Expo Camera 57.0.4 · AR02

补丁：`expo-camera+57.0.4.patch`。基于 npm 已安装的 **57.0.4**，上游 gitHead
`499024a441d67293aa60afcbe02051999a54f1bb`。相机仍使用 Expo CameraX
`OnImageCapturedCallback`，没有改成文件式 `OutputFileOptions`。

[Expo SDK 57 官方 Camera 文档](https://docs.expo.dev/versions/v57.0.0/sdk/camera/)
说明普通拍照写入应用缓存；`base64: true` 只增加返回值，不能取消上游写文件。
本补丁增加 Android 专用的 `memoryOnly: true`，并从原生 `ExpoCamera` 模块暴露
`supportsMemoryOnly === true`。JS 必须先检查能力，再挂载相机；旧 APK 缺能力时不采集。
Android 必须把 `expo-camera` 列入 autolinking 的 `buildFromSource`，不能使用未打补丁的 AAR。

`memoryOnly` 先于上游普通处理分支返回：原始 JPEG → 有界采样解码 →
等比缩放最长边不超过 1280 → 像素方向/镜像校正 → 重新压缩 JPEG → base64。
返回仅含 `base64`、`width`、`height`、`format`；不含 URI、EXIF、PictureRef。
调用者的 `base64/exif/additionalExif/imageType/pictureRef/skipProcessing/fastMode`
不改变这条输出路径。`quality` 和 `shutterSound` 仍生效。
方向直接使用 CameraX `ImageProxy.imageInfo.rotationDegrees`，不读取或复制 JPEG 元数据；
依据 [Android OnImageCapturedCallback 契约](https://developer.android.com/reference/androidx/camera/core/ImageCapture.OnImageCapturedCallback#onCaptureSuccess(androidx.camera.core.ImageProxy))，内存回调像素尚未应用方向。

取消由原生 CameraView 所属的 `MemoryOnlyCapture` 和协程共同执行：
卸载先标所有在途请求为 cancelled，再取消 scope 并解绑 CameraX；回调在复制像素前检查，
处理在解码、缩放、方向校正、压缩、base64 和 resolve 的边界检查。
请求对象用 CAS 统一执行 resolve/reject，取消即使早于协程启动也会拒绝 Promise；
清理、CameraX 回调和协程收尾重复到达不会重复终结。
已进入 Android Bitmap 的同步解码/压缩不能被硬中断；取消不证明硬件没有采到在途帧。
迟到结果还必须由 JS 请求代际作废，不得上传或发送。内存数组在可控边界清零，bitmap 回收；
base64 字符串及平台内部缓冲由运行时回收，不能据此宣称已对 RAM 做完整擦除。

原生只读诊断接口：

- `cameraActive` 属性及 `cameraActiveChanged` 事件（`{ active: boolean }`）：
  CameraX `OPEN` 为 true，`CLOSING` 保持原值，直到原生 `CLOSED` 才变 false。
  模块观察器独立于 React 挂载和 Activity 的前台生命周期；模块销毁时移除。
- `getMemoryCaptureStatsAsync()`：进程内 `started/completed/cancelled/failed` 计数，
  `cameraActive`，以及 `Camera` 缓存子树的 `cacheFileCount/cacheBytes`。
  无路径、图片、用户标识；不删除文件。`started` 是原生请求数，不是硬件实际曝光数；
  进程重启会清零。缓存目录不存在返回 0，遍历失败抛错，不以 0 冒充成功。

源码守卫：`npm test -- --runInBand test/cameraNativePatch.test.ts`，包含反向缺陷注入。
守卫只验证补丁边界；APK 编译、成功/取消/失败/超时/关闭视觉/切后台后的计数和原生
`CLOSED`、应用缓存未新增文件，需要按 AR02 实施记录在对应 SHA 的包上取证。

在 `mobile/` 重新生成：

```powershell
npx --no-install patch-package expo-camera --include '(CameraViewModule|ExpoCameraView|Options|MemoryOnlyCapture|ResolveTakenPicture)\.kt$'
```

## React Native Audio API 0.13.3

`react-native-audio-api+0.13.3.patch` 为 Oboe 输入设置 `VoiceCommunication`，启用系统
AEC 所需输入预设。历史依据和声学读数边界见 `mobile/README.md` 的 AEC 节。
