// Edge Gateway：HMI WebSocket 接入 → Edge Orchestrator（gRPC EdgeOrchestrator.Handle）。
// 端云持久 bidi 长连由 Edge Orchestrator 持有（orchestrator/edge/cloud_client.py，R2.3）；
// 本网关另订阅 NATS agent.proactive 把主动消息广播给已连 HMI。
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	natsgo "github.com/nats-io/nats.go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"

	"github.com/cockpit/car-agent/gateway/deployprofile"
	"github.com/cockpit/car-agent/gateway/tlscfg"
	commonpb "github.com/cockpit/car-agent/gen/go/cockpit/common/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// ─── WebSocket 处理 ───

var upgrader = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}

// ─── WS Hub：主动建议异步广播给已连 HMI ───
// gorilla/websocket 不允许并发写同一连接，故每连一把写锁，请求-响应与广播都经它序列化。

type wsClient struct {
	conn *websocket.Conn
	mu   sync.Mutex
}

func (c *wsClient) send(v any) {
	b, _ := json.Marshal(v)
	c.mu.Lock()
	_ = c.conn.WriteMessage(websocket.TextMessage, b)
	c.mu.Unlock()
}

type wsHub struct {
	mu      sync.Mutex
	clients map[*wsClient]bool
}

func newHub() *wsHub { return &wsHub{clients: map[*wsClient]bool{}} }

func (h *wsHub) register(c *wsClient)   { h.mu.Lock(); h.clients[c] = true; h.mu.Unlock() }
func (h *wsHub) unregister(c *wsClient) { h.mu.Lock(); delete(h.clients, c); h.mu.Unlock() }

func (h *wsHub) broadcast(v any) int {
	h.mu.Lock()
	cs := make([]*wsClient, 0, len(h.clients))
	for c := range h.clients {
		cs = append(cs, c)
	}
	h.mu.Unlock()
	for _, c := range cs {
		c.send(v)
	}
	return len(cs)
}

var hub = newHub()

// ─── 车况镜像：NATS vehicle.state.changed（增量 diff + edge 周期全量快照，同一主题）
// 合并缓存 → HMI 连上即推全量、变更即播。右舞台待机场景电量/续航/挡位据此动态取数
// （此前 ContextualStage 写死 62%/430km/P 占位）。

var vehState = struct {
	mu   sync.Mutex
	m    map[string]any
	last string // 上次广播的序列化快照：周期全量快照重放时去重，不给 HMI 发无变化帧
}{m: map[string]any{}}

// mergeVehState 合并 changes 进镜像，返回（全量快照, 是否有实际变化）。
func mergeVehState(changes []map[string]any) (map[string]any, bool) {
	vehState.mu.Lock()
	defer vehState.mu.Unlock()
	for _, kv := range changes {
		if k, _ := kv["key"].(string); k != "" {
			vehState.m[k] = kv["new"]
		}
	}
	snap := make(map[string]any, len(vehState.m))
	for k, v := range vehState.m {
		snap[k] = v
	}
	b, _ := json.Marshal(snap) // encoding/json 按 key 排序，序列化即规范形
	changed := string(b) != vehState.last
	vehState.last = string(b)
	return snap, changed
}

// 主动投递回路（M-C）。此前网关只做单向广播：`hub.broadcast` 的返回值（在线 HMI 数）
// 只进了一行日志，n==0 时消息直接蒸发。回执与上线补投是「发出去了」与「用户收到了」
// 之间缺的那一跳，两者都要能从 WS 处理器发回治理器，故句柄提到包级。
var proactiveBus struct {
	mu sync.Mutex
	nc *natsgo.Conn
}

func setProactiveBus(nc *natsgo.Conn) {
	proactiveBus.mu.Lock()
	proactiveBus.nc = nc
	proactiveBus.mu.Unlock()
}

func publishProactiveControl(subject string, payload map[string]any) {
	proactiveBus.mu.Lock()
	nc := proactiveBus.nc
	proactiveBus.mu.Unlock()
	if nc == nil {
		return // 无 NATS：主动链路本就禁用，静默（同既有降级口径）
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return
	}
	if err := nc.Publish(subject, b); err != nil {
		log.Printf("[edge-gateway] proactive control publish failed (%s): %v", subject, err)
	}
}

func vehStateSnapshot() map[string]any {
	vehState.mu.Lock()
	defer vehState.mu.Unlock()
	if len(vehState.m) == 0 {
		return nil
	}
	snap := make(map[string]any, len(vehState.m))
	for k, v := range vehState.m {
		snap[k] = v
	}
	return snap
}

type wsRequest struct {
	Type                string            `json:"type"` // R4.3b P2：="cancel" 时取消在飞请求（旧 HMI 不发此字段，向后兼容）
	Text                string            `json:"text"`
	SessionID           string            `json:"session_id"`
	// QA 卡 Q3：本轮的请求 id，由 HMI 生成。网关把它盖在该轮**每一帧**上，
	// 归属不再靠「WS 串行所以 fifo[0] 就是当前轮」那个在抢发时不成立的假设。
	RequestID           string            `json:"request_id"`
	IsConfirmation      bool              `json:"is_confirmation"` // HMI 确认/取消按钮回应多轮确认时置 true
	// QA 卡 Q1-B：这一下确认/取消**指向哪一条挂起**。由 final 下发、HMI 原样回传。
	// 网关只搬运不解释——它既不是授权凭据也不参与任何判定，寻址在编排侧做。
	OperationID         string            `json:"operation_id"`
	Meta                map[string]string `json:"meta"`            // HMI 设置透传（answer_length/model_pref 等）
	E2EMemoryCapability string            `json:"e2e_memory_capability"`
	// M-C 投递回执：HMI 呈现主动消息后回传凭据（合并组回整组）。
	DeliveryID          string            `json:"delivery_id"`
	DeliveryIDs         []string          `json:"delivery_ids"`
}

func buildHandleRequest(
	req wsRequest,
	id identity,
	e2eClaims *e2eIdentityClaims,
) (*orchpb.HandleRequest, error) {
	if e2eClaims != nil {
		if err := validateE2ESessionID(req.SessionID, e2eClaims.UserID); err != nil {
			return nil, err
		}
	} else if req.E2EMemoryCapability != "" {
		return nil, fmt.Errorf("memory capability requires signed E2E identity")
	}
	if req.SessionID == "" {
		req.SessionID = "default"
	}
	return &orchpb.HandleRequest{
		Text:                req.Text,
		SessionId:           req.SessionID,
		RequestId:           req.RequestID,
		IsConfirmation:      req.IsConfirmation,
		OperationId:         req.OperationID,
		Meta:                stampScopes(req.Meta, id.scopes),
		E2EMemoryCapability: req.E2EMemoryCapability,
		Context: &commonpb.ContextRef{
			SessionId: req.SessionID,
			VehicleId: id.vehicleID,
			UserId:    id.userID,
		},
	}, nil
}

func handleWS(w http.ResponseWriter, r *http.Request, orch orchpb.EdgeOrchestratorClient, auth authConfig) {
	// 层 1 鉴权：解析 ?token=（命中即用其身份+scope；未命中看 AUTH_REQUIRED）。
	// 校验须在 WS Upgrade 之前——拒绝时回 401，客户端握手即失败、连接不建立。
	id, e2eClaims, ok, hardReject := auth.resolveSession(r.URL.Query().Get("token"))
	if !ok {
		if hardReject || auth.required {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			log.Printf("[edge-gateway] WS rejected: missing/invalid token")
			return
		}
		id = auth.anonymous() // 默认模式匿名放行（逐字保持现状）
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

	client := &wsClient{conn: conn}

	// Signed E2E children must receive owner proof before any test setup or
	// destructive request can be sent over this connection. Send it before
	// hub registration so a concurrent proactive broadcast cannot win the
	// first-frame race.
	if e2eClaims != nil {
		client.send(map[string]any{
			"type":       "e2e_identity_ack",
			"run_id":     e2eClaims.RunID,
			"user_id":    e2eClaims.UserID,
			"vehicle_id": e2eClaims.VehicleID,
		})
	}

	hub.register(client)
	defer hub.unregister(client)

	// 车况镜像连上即推（尚无镜像时静默；下一个周期快照/变更事件会补上）
	if snap := vehStateSnapshot(); snap != nil {
		client.send(map[string]any{"type": "vehicle_state", "state": snap})
	}

	// 连上即请求补投未送达的主动消息（M-C）。车况镜像早就在用「连上即推」这个
	// 机制，只是从没用在主动消息上——断线期间到点的提醒此前直接蒸发。
	publishProactiveControl("agent.proactive.replay", map[string]any{
		"user_id": id.userID,
	})

	// WS 保活：复杂任务开思考时执行期可能 30s+ 无应用层流量，期间不读 WS 控制帧
	// （主循环阻塞在 stream.Recv）。服务端周期 Ping 维持连接，避免浏览器/代理 idle 掐断
	// 导致过程区与最终答案丢失。WriteControl 可与 WriteMessage 并发（gorilla 明确允许）。
	stopPing := make(chan struct{})
	defer close(stopPing)
	go func() {
		t := time.NewTicker(15 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-stopPing:
				return
			case <-t.C:
				_ = conn.WriteControl(websocket.PingMessage, nil,
					time.Now().Add(5*time.Second))
			}
		}
	}()

	// R4.3b P2（U2 真打断）：读循环不再串行 drain 每条请求的事件流，改为「主循环只读消息、
	// 请求在独立 goroutine 处理」——这样处理中（THINKING 90s）仍能读到 {type:"cancel"} 并即时取消。
	// ctx cancel 沿 gRPC 天然传播到 edge-orchestrator→cloud→LLM（通讯加固卡已验证预算级联），零 proto 改动。
	var mu sync.Mutex // 保护 currentCancel/currentReqID/reqGen
	var currentCancel context.CancelFunc
	var currentReqID string // QA 卡 Q3：在飞那轮的 request_id，取消时要点名回给客户端
	var reqGen uint64

	// 取消在飞请求，返回它的 request_id（无在飞时返回空串）。
	cancelCurrent := func() string {
		mu.Lock()
		rid := currentReqID
		if currentCancel != nil {
			currentCancel()
			currentCancel = nil
			currentReqID = ""
		}
		mu.Unlock()
		return rid
	}
	defer cancelCurrent() // 连接退出时取消在飞请求

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return
		}
		var req wsRequest
		if json.Unmarshal(msg, &req) != nil {
			continue
		}
		if req.Type == "proactive_ack" {
			// HMI 已呈现——**唯一的通知合同完成条件**。网关只转发不判定：
			// WebSocket write 成功不能被提升为「用户看见了」。
			ids := req.DeliveryIDs
			if len(ids) == 0 && req.DeliveryID != "" {
				ids = []string{req.DeliveryID}
			}
			if len(ids) > 0 {
				publishProactiveControl("agent.proactive.ack", map[string]any{
					"delivery_ids": ids,
				})
			}
			continue
		}
		if req.Type == "cancel" {
			// THINKING 期唤醒词打断：取消在飞请求，回确认（幂等；无在飞时也回，HMI 侧忽略）。
			// Q3：点名被取消的那一轮——不点名，客户端只能猜是哪个气泡该标「已打断」。
			cancelled := map[string]any{"type": "cancelled"}
			if rid := cancelCurrent(); rid != "" {
				cancelled["request_id"] = rid
			}
			client.send(cancelled)
			continue
		}
		if req.Text == "" {
			continue // 向后兼容：空 Text 的非 cancel 消息忽略（同旧行为）
		}
		handleReq, err := buildHandleRequest(req, id, e2eClaims)
		if err != nil {
			_ = conn.WriteControl(
				websocket.CloseMessage,
				websocket.FormatCloseMessage(
					websocket.ClosePolicyViolation,
					"invalid signed E2E request",
				),
				time.Now().Add(5*time.Second),
			)
			return
		}

		// 起新请求：先 cancel 旧的在飞请求（防御，每连接同时至多一个在飞），登记自己的 cancel。
		// 90s：复杂任务动态开思考端到端更慢，过程区覆盖等待；快意图仍毫秒级返回。
		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		mu.Lock()
		preemptedReqID := ""
		if currentCancel != nil {
			currentCancel()
			preemptedReqID = currentReqID
		}
		currentCancel = cancel
		currentReqID = handleReq.RequestId
		reqGen++
		myGen := reqGen
		mu.Unlock()
		// Q3：抢占掉的那一轮**必须点名告诉客户端**。此前它无声消失——客户端的
		// 单槽看门狗又刚被新请求清掉，那个气泡就永远转圈（报告里「需要刷新标签页」
		// 的成因）。判据同 B3「静默回落就是要消灭的形态」。
		if preemptedReqID != "" {
			client.send(map[string]any{
				"type": "cancelled", "request_id": preemptedReqID})
		}

		go func(handleReq *orchpb.HandleRequest, ctx context.Context, cancel context.CancelFunc, myGen uint64) {
			reqID := handleReq.RequestId
			defer func() {
				cancel()
				mu.Lock()
				if reqGen == myGen { // 仅当仍是当前请求时清空（避免误清后来者）
					currentCancel = nil
					currentReqID = ""
				}
				mu.Unlock()
			}()
			stream, err := orch.Handle(ctx, handleReq)
			if err != nil {
				// 取消导致的错误（context.Canceled）吞掉，不回发 error（HMI 已收 cancelled）
				if ctx.Err() == nil {
					client.send(stampRequestID(
						map[string]any{"type": "error", "message": err.Error()}, reqID))
				}
				return
			}
			for {
				ev, err := stream.Recv()
				if err != nil {
					// 晚到的 grpc CANCELLED（ctx 已取消）不回发 error；正常 EOF/错误照旧收尾
					return
				}
				client.send(stampRequestID(eventToMap(ev), reqID))
			}
		}(handleReq, ctx, cancel, myGen)
	}
}

// QA 卡 Q3：把本轮 request_id 盖在每一帧上。空 id（旧 HMI 不发）不盖——
// 客户端见不到 id 时回落 FIFO，滚动升级窗口里不黑屏。
func stampRequestID(frame map[string]any, reqID string) map[string]any {
	if reqID != "" && frame != nil {
		frame["request_id"] = reqID
	}
	return frame
}

func eventToMap(ev *orchpb.HandleEvent) map[string]any {
	switch e := ev.Event.(type) {
	case *orchpb.HandleEvent_SpeechDelta:
		return map[string]any{"type": "speech_delta", "delta": e.SpeechDelta}
	case *orchpb.HandleEvent_Action:
		return map[string]any{"type": "action", "action": actionToMap(e.Action)}
	case *orchpb.HandleEvent_Progress:
		// 复杂任务过程区增量（脱敏）：步骤标签 + 思考摘要 + 行车态门控标记。
		p := e.Progress
		return map[string]any{
			"type": "process", "phase": p.Phase, "label": p.Label,
			"summary": p.Summary, "status": p.Status, "step_id": p.StepId,
			"driving": p.Driving,
		}
	case *orchpb.HandleEvent_Final:
		f := e.Final
		actions := make([]any, 0, len(f.Actions))
		for _, a := range f.Actions {
			actions = append(actions, actionToMap(a))
		}
		result := map[string]any{
			"type": "final", "speech": f.Speech, "follow_up": f.FollowUp,
			"need_confirm": f.NeedConfirm, "actions": actions,
			// B5 缺陷 C：终态也透传行车态（与 process 分支同款、恒带键不 omitempty——
			// 客户端要能分辨「false」与「旧网关没有这个键」；HMI 不读它）
			"driving": f.Driving,
		}
		// M2 P2：会话级情绪信号（空=中性不发键，HMI 按缺省处理）
		if f.Emotion != "" {
			result["emotion"] = f.Emotion
		}
		// Q1-B：挂起寻址键（只有挂起 final 非空——不发恒空键）
		if f.OperationId != "" {
			result["operation_id"] = f.OperationId
		}
		// Q1-C：本轮关掉的挂起（HMI 据此撤确认条）
		if len(f.ClosedOperationIds) > 0 {
			result["closed_operation_ids"] = f.ClosedOperationIds
		}
		if f.UiCard != nil {
			result["ui_card"] = f.UiCard.AsMap()
		}
		return result
	}
	return map[string]any{"type": "unknown"}
}

func actionToMap(a *commonpb.AgentAction) map[string]any {
	var payload map[string]any
	if a.Payload != nil {
		payload = a.Payload.AsMap()
	}
	return map[string]any{"type": a.Type, "payload": payload, "require_confirm": a.RequireConfirm}
}

func writeJSON(c *websocket.Conn, v any) {
	b, _ := json.Marshal(v)
	_ = c.WriteMessage(websocket.TextMessage, b)
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// clientKeepalive 给出站 gRPC 连接加 keepalive：容器/NAT 掐断空闲连接后能在一个
// 周期内探测到并重连重解析 DNS（修复"依赖重启换 IP 后需重启本服务"，亦防长任务静默断流）。
func clientKeepalive() grpc.DialOption {
	return grpc.WithKeepaliveParams(keepalive.ClientParameters{
		Time:                20 * time.Second,
		Timeout:             10 * time.Second,
		PermitWithoutStream: true,
	})
}

// transportCreds 选择传输凭证：GRPC_TLS 开启走 mTLS 客户端凭证，否则 insecure（保持现状）。
func transportCreds() grpc.DialOption {
	if tlscfg.Enabled() {
		c, err := tlscfg.ClientCreds()
		if err != nil {
			log.Fatalf("[edge-gateway] tls client creds: %v", err)
		}
		return grpc.WithTransportCredentials(c)
	}
	return grpc.WithTransportCredentials(insecure.NewCredentials())
}

// dnsTarget 强制 dns resolver：裸 host:port 默认走 passthrough（只解析一次、永不重解析），
// 依赖容器重建换 IP 后会一直连旧 IP 报错，直到本服务重启。dns scheme 在连接 TRANSIENT_FAILURE
// 时重解析 DNS → 自动重连（配合 keepalive 探活），无需重启本服务。已带 scheme 的原样返回。
func dnsTarget(addr string) string {
	if strings.Contains(addr, "://") {
		return addr
	}
	return "dns:///" + addr
}

// ─── 入口 ───

func main() {
	// B3 部署形态闸：dev（默认）零校验；prod 下任一 fail-open 配置即拒绝启动。
	// 放在 main 第一行——校验必须先于任何监听/拨号，别让一个 fail-open 的进程先把端口占上。
	deployprofile.Enforce(deployprofile.RoleEdgeGateway)
	orchAddr := getenv("EDGE_ORCHESTRATOR_ADDR", "edge-orchestrator:50070")
	port := getenv("EDGE_GATEWAY_PORT", "8090")
	auth := loadAuthConfig() // 层 1 会话鉴权（R3.1）；默认关，逐字保持现状
	// AUTH_TOKENS 畸形 ⇒ **拒绝启动**。放在这里（与 deployprofile.Enforce 同段）而不是
	// 让它静默跳过：2026-08-19 云端实测过静默的代价——漏写一段导致 user_id 位被解析成
	// vehicle_id，**权限全通、功能全正常，只有长期记忆一条都召不回**。
	// 起不来是刺眼的，身份悄悄错了不是。
	if auth.tokensConfigErr != nil {
		log.Fatalf("[edge-gateway] %v", auth.tokensConfigErr)
	}

	// 连接端侧编排器（架构 §2.2：HMI → Edge Gateway → Edge Orchestrator）
	orchConn, err := grpc.NewClient(dnsTarget(orchAddr),
		transportCreds(),
		clientKeepalive())
	if err != nil {
		log.Fatalf("[edge-gateway] dial orchestrator %s: %v", orchAddr, err)
	}
	defer orchConn.Close()
	orchStub := orchpb.NewEdgeOrchestratorClient(orchConn)

	// 主动建议投递：订阅 NATS agent.proactive（agents/memory 发布）→ 广播给已连 HMI。
	// 这是「NATS→HMI 投递一跳」；无 NATS_URL 时静默禁用，不影响请求-响应。
	if natsURL := os.Getenv("NATS_URL"); natsURL != "" {
		if nc, err := natsgo.Connect(natsURL, natsgo.MaxReconnects(-1)); err != nil {
			log.Printf("[edge-gateway] NATS connect failed, proactive disabled: %v", err)
		} else {
			setProactiveBus(nc)
			if _, err := nc.Subscribe("agent.proactive", func(m *natsgo.Msg) {
				var p map[string]any
				if json.Unmarshal(m.Data, &p) != nil {
					return
				}
				// card 透传：异步深调研完成时带可读分节报告卡（p["card"]）；
				// 普通主动播报（路况/早报）无该键 → nil → HMI 端忽略，不影响既有行为。
				// delivery_id/delivery_ids：投递凭据，HMI 按它幂等呈现并回执（M-C）。
				// priority：**HMI 要靠它仲裁语音**——此前这个键被网关吞掉，于是
				// S2S 说话时 HMI 只能一刀切「全都只出气泡」，分不出哪条该抢话、
				// 哪条该等空闲补播。
				out := map[string]any{
					"type": "proactive", "speech": p["speech"],
					"advisory": p["type"], "source": p["agent_id"],
					"card": p["card"],
				}
				for _, k := range []string{"delivery_id", "delivery_ids", "priority"} {
					if v, ok := p[k]; ok && v != nil {
						out[k] = v
					}
				}
				n := hub.broadcast(out)
				log.Printf("[edge-gateway] proactive(nats) -> %d HMI: %v", n, p["speech"])
			}); err != nil {
				log.Printf("[edge-gateway] NATS subscribe failed: %v", err)
			} else {
				log.Printf("[edge-gateway] NATS proactive bridge active (%s)", natsURL)
			}
			// 车况桥接：合并增量 diff / 周期全量快照（同主题）→ 有实际变化才广播全量给 HMI。
			if _, err := nc.Subscribe("vehicle.state.changed", func(m *natsgo.Msg) {
				var p struct {
					Changes []map[string]any `json:"changes"`
				}
				if json.Unmarshal(m.Data, &p) != nil || len(p.Changes) == 0 {
					return
				}
				if snap, changed := mergeVehState(p.Changes); changed {
					hub.broadcast(map[string]any{"type": "vehicle_state", "state": snap})
				}
			}); err != nil {
				log.Printf("[edge-gateway] NATS vehicle.state subscribe failed: %v", err)
			} else {
				log.Printf("[edge-gateway] NATS vehicle-state bridge active")
			}
		}
	}

	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"status":"ok","orchestrator":"%s"}`, orchAddr)
	})
	http.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		handleWS(w, r, orchStub, auth)
	})

	srv := &http.Server{Addr: ":" + port}
	go func() {
		log.Printf("[edge-gateway] HTTP/WS serving on :%s -> %s (auth_required=%v, tokens=%d, e2e_identity=%v, default_vehicle=%s)",
			port, orchAddr, auth.required, len(auth.tokens), auth.e2eEnabled, auth.defaultVehicle)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()

	// 优雅停机：SIGTERM/SIGINT 时停止接收新连接并给在连 HMI 留出收尾窗口，
	// 不再硬断 WebSocket（减少重建容器期间过程区/最终答案丢失）。
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	log.Printf("[edge-gateway] shutting down gracefully")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
}
