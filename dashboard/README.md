# Observability Dashboard

独立于座舱 HMI 的开发/演示可观测台，实时展示：

- 车辆状态与变更 diff；
- 端云请求链路；
- Agent 健康、调用数、时延和错误率；
- 车速/电量等模拟环境量；
- 经现有 Edge Gateway 发出的对照实验指令。

## 运行

```bash
npm ci
npm run dev
```

默认地址为 `http://localhost:5174`。环境变量：

- `VITE_COLLECTOR_URL`：默认 `http://localhost:8092`
- `VITE_EDGE_GATEWAY_URL`：默认 `http://localhost:8090`

## 验证

```bash
npm test
npm run build
```

Dashboard 不直接写空调、车窗等车控状态。命令复用 Edge Gateway，车控仍只经 VAL；
动态滑块只调用 collector 的环境量 debug 接口。

`DEBUG_VEHICLE_CONTROL` 仅用于本地演示；非开发环境必须设为 `false`，并把 collector
置于正式鉴权边界之后。
