/**
 * AR02 原生补丁的隐私回归锁；验证 npm ci 后实际安装的 Kotlin 源码。
 * 这是源码边界守卫，不替代 APK 编译、CameraX 真机开关或文件证据。
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(__dirname, '..')
const nativeRoot = path.join(root, 'node_modules/expo-camera/android/src/main/java/expo/modules/camera')
const read = (file: string) => fs.readFileSync(path.join(nativeRoot, file), 'utf8')
const sources = {
  resolver: read('tasks/ResolveTakenPicture.kt'),
  view: read('ExpoCameraView.kt'),
  module: read('CameraViewModule.kt'),
  options: read('Options.kt'),
  capture: read('tasks/MemoryOnlyCapture.kt'),
}

function between(source: string, start: string, end: string): string {
  const from = source.indexOf(start)
  const to = source.indexOf(end, from + start.length)
  if (from < 0 || to <= from) throw new Error(`Missing audited native boundary: ${start}`)
  return source.slice(from, to)
}

function audit(input: typeof sources): void {
  const resolve = between(input.resolver, 'suspend fun resolve()', 'private suspend fun checkMemoryCaptureActive')
  const memoryDispatch = between(resolve, 'if (options.memoryOnly)', 'val bundle = processImage()')
  expect(memoryDispatch).toContain('processMemoryOnlyImage(capture)')
  expect(memoryDispatch).toContain('checkMemoryCaptureActive(capture)')
  expect(memoryDispatch).toContain('capture.complete(response)')
  expect(memoryDispatch).toContain('return@withContext')
  expect(memoryDispatch).not.toMatch(/processImage\(|skipProcessing\(|onComplete\(/)
  const processor = between(input.resolver, 'private suspend fun processMemoryOnlyImage', 'private fun processImage()')
  expect(processor).not.toMatch(/\b(?:File|FileOutputStream|writeStreamToFile|generateOutputPath|generateOutputFile|PictureRef|getExifData|addExifData|setExifData)\s*\(/)
  expect(processor).not.toMatch(/put(?:String|Bundle)\((?:URI_KEY|EXIF_KEY)|saveAttributes\(|options\.(?:skipProcessing|pictureRef|fastMode|exif|additionalExif|imageType)/)
  expect(processor).toContain('Bitmap.CompressFormat.JPEG')
  expect(processor).toContain('putString(BASE64_KEY, encoded)')
  expect(processor).toContain('checkMemoryCaptureActive(capture)')
  expect(input.resolver).toContain('MEMORY_ONLY_MAX_DIMENSION = 1280')
  expect(processor).toContain('if (edge > MEMORY_ONLY_MAX_DIMENSION)')
  expect(processor).toContain('postRotate(memoryRotationDegrees.toFloat())')
  expect(processor).not.toContain('ExifInterface')
  expect(input.options).toContain('@Field val memoryOnly: Boolean = false')
  expect(input.view).toContain('if (options.fastMode && !options.memoryOnly)')
  const success = between(input.view, 'override fun onCaptureSuccess', 'override fun onError')
  expect(success.indexOf('memoryCapture.ensureActive()')).toBeLessThan(success.indexOf('image.planes.toByteArray()'))
  expect(success).toContain('finally {\n            image.close()')
  expect(input.view).toContain('memoryCaptures.forEach { it.cancel() }')
  expect(input.capture).toContain('state.compareAndSet(PENDING, next)')
  const cancellation = between(input.capture, 'fun cancel()', 'fun fail(')
  expect(cancellation).toContain('if (finish(CANCELLED, cancelled))')
  expect(cancellation).toContain('promise.reject("ERR_CAMERA_CAPTURE_CANCELLED"')
  expect(input.module).toContain('Property("supportsMemoryOnly") { true }')
  expect(input.module).toContain('Property("cameraActive") { cameraActive }')
  expect(input.module).toContain('cameraInfo.cameraState.observeForever(observer)')
  expect(input.module).toContain('CameraState.Type.OPEN -> activeCameras.add(cameraInfo)')
  expect(input.module).toContain('CameraState.Type.CLOSED -> activeCameras.remove(cameraInfo)')
  expect(input.module).not.toMatch(/CameraState.Type.CLOSING[^\n]*activeCameras.remove/)
  const stats = between(input.module, 'AsyncFunction("getMemoryCaptureStatsAsync")', '// Aligned with iOS')
  expect(stats).toContain('"cacheFileCount" to count')
  expect(stats).toContain('"cacheBytes" to bytes')
  expect(stats).not.toMatch(/\.delete\(|write|readBytes|absolutePath|canonicalPath/)
}

test('Expo Camera 57.0.4 installed native memory-only and physical-state boundaries stay intact', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'node_modules/expo-camera/package.json'), 'utf8'))
  expect(pkg.version).toBe('57.0.4')
  audit(sources)
})

test('patch-package artifact includes only the five intended Kotlin source files', () => {
  const patch = fs.readFileSync(path.join(root, 'patches/expo-camera+57.0.4.patch'), 'utf8')
  const files = [...patch.matchAll(/^diff --git a\/node_modules\/expo-camera\/(\S+) /gm)].map(match => match[1])
  expect(files.sort()).toEqual([
    'CameraViewModule.kt', 'ExpoCameraView.kt', 'Options.kt',
    'tasks/MemoryOnlyCapture.kt', 'tasks/ResolveTakenPicture.kt',
  ].map(file => `android/src/main/java/expo/modules/camera/${file}`).sort())
})

test.each([
  ['file write in processor', 'resolver', 'val bounds = BitmapFactory.Options()', 'writeStreamToFile(directory, stream)\n    val bounds = BitmapFactory.Options()'],
  ['URI in memory response', 'resolver', 'putString(BASE64_KEY, encoded)', 'putString(URI_KEY, "file://cache.jpg")'],
  ['EXIF in memory response', 'resolver', 'putString(BASE64_KEY, encoded)', 'putBundle(EXIF_KEY, metadata)'],
  ['legacy fallback after memory response', 'resolver', 'return@withContext', 'processImage()'],
  ['fastMode early resolve', 'view', 'options.fastMode && !options.memoryOnly', 'options.fastMode'],
  ['missing disposal invalidation', 'view', 'memoryCaptures.forEach { it.cancel() }', 'memoryCaptures.forEach { }'],
  ['unsettled cancelled promise', 'capture', 'promise.reject("ERR_CAMERA_CAPTURE_CANCELLED", "Memory-only capture was cancelled", null)', 'Unit'],
  ['premature physical-close fact', 'module', 'CameraState.Type.CLOSED -> activeCameras.remove(cameraInfo)', 'CameraState.Type.CLOSING -> activeCameras.remove(cameraInfo)'],
] as const)('negative mutation: guard rejects %s', (_name, key, from, to) => {
  expect(sources[key]).toContain(from)
  const changed = { ...sources, [key]: sources[key].replace(from, to) }
  expect(() => audit(changed)).toThrow()
})
