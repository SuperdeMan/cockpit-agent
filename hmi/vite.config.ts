import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 5173 },
  // onnxruntime-web（R4.3 hands-free VAD）：dev 不预打包，让 ORT 自行按其模块 URL 定位 wasm，
  // 否则 Vite dep-optimizer 在 dev 下取不到 wasm（回退 index.html→WebAssembly magic word 报错）。
  // 构建期正常打包（wasm 作 hash 资产），不受此影响。
  optimizeDeps: { exclude: ['onnxruntime-web'] },
})
