# 座舱 HMI（React 简版）

PoC 演示前端：通过 WebSocket 连 Edge Gateway，发送指令（模拟语音）、展示助手回复与车控动作卡片。

## 本地运行
```bash
cd hmi
npm install
npm run dev      # http://localhost:5173
```
通过 `VITE_EDGE_GATEWAY_URL` 配置网关地址（默认 `http://localhost:8090`）。

## 待办
- TODO(Phase1): 接入真实语音（Web Speech / 端侧 ASR）、流式话术逐字渲染、POI 卡片可视化、TTS 播报。
