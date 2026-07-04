import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // R4.3 唤醒词 KWS：sherpa KWS WASM 是 pthread 构建，需 SharedArrayBuffer → 需跨源隔离。
    // COEP=credentialless（非 require-corp）：开启隔离但不挡无凭据的跨源 no-cors 资源（图片/地图），
    // 后端 API 走 cors（已带 ACAO）不受影响、WS 豁免。prod 需宿主服务器同样下发这两个头。
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'credentialless',
    },
  },
  // onnxruntime-web（R4.3 hands-free VAD）：dev 不预打包，让 ORT 自行按其模块 URL 定位 wasm，
  // 否则 Vite dep-optimizer 在 dev 下取不到 wasm（回退 index.html→WebAssembly magic word 报错）。
  // 构建期正常打包（wasm 作 hash 资产），不受此影响。
  optimizeDeps: { exclude: ['onnxruntime-web'] },
})
