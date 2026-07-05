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

type wsRequest struct {
	Type           string            `json:"type"`            // R4.3b P2：="cancel" 时取消在飞请求（旧 HMI 不发此字段，向后兼容）
	Text           string            `json:"text"`
	SessionID      string            `json:"session_id"`
	IsConfirmation bool              `json:"is_confirmation"` // HMI 确认/取消按钮回应多轮确认时置 true
	Meta           map[string]string `json:"meta"`            // HMI 设置透传（answer_length/model_pref 等）
}

func handleWS(w http.ResponseWriter, r *http.Request, orch orchpb.EdgeOrchestratorClient, auth authConfig) {
	// 层 1 鉴权：解析 ?token=（命中即用其身份+scope；未命中看 AUTH_REQUIRED）。
	// 校验须在 WS Upgrade 之前——拒绝时回 401，客户端握手即失败、连接不建立。
	id, ok := auth.resolve(r.URL.Query().Get("token"))
	if !ok {
		if auth.required {
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
	hub.register(client)
	defer hub.unregister(client)

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
	var mu sync.Mutex // 保护 currentCancel/reqGen
	var currentCancel context.CancelFunc
	var reqGen uint64

	cancelCurrent := func() {
		mu.Lock()
		if currentCancel != nil {
			currentCancel()
			currentCancel = nil
		}
		mu.Unlock()
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
		if req.Type == "cancel" {
			// THINKING 期唤醒词打断：取消在飞请求，回确认（幂等；无在飞时也回，HMI 侧忽略）
			cancelCurrent()
			client.send(map[string]any{"type": "cancelled"})
			continue
		}
		if req.Text == "" {
			continue // 向后兼容：空 Text 的非 cancel 消息忽略（同旧行为）
		}
		if req.SessionID == "" {
			req.SessionID = "default"
		}

		// 起新请求：先 cancel 旧的在飞请求（防御，每连接同时至多一个在飞），登记自己的 cancel。
		// 90s：复杂任务动态开思考端到端更慢，过程区覆盖等待；快意图仍毫秒级返回。
		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		mu.Lock()
		if currentCancel != nil {
			currentCancel()
		}
		currentCancel = cancel
		reqGen++
		myGen := reqGen
		mu.Unlock()

		go func(req wsRequest, ctx context.Context, cancel context.CancelFunc, myGen uint64) {
			defer func() {
				cancel()
				mu.Lock()
				if reqGen == myGen { // 仅当仍是当前请求时清空（避免误清后来者）
					currentCancel = nil
				}
				mu.Unlock()
			}()
			stream, err := orch.Handle(ctx, &orchpb.HandleRequest{
				Text:           req.Text,
				SessionId:      req.SessionID,
				IsConfirmation: req.IsConfirmation,
				Meta:           stampScopes(req.Meta, id.scopes), // 网关权威注入 granted_scopes
				Context: &commonpb.ContextRef{
					SessionId: req.SessionID,
					VehicleId: id.vehicleID,
					UserId:    id.userID, // 由 token 解析（匿名回退 AUTH_DEFAULT_USER_ID），去硬编码 "u1"
				},
			})
			if err != nil {
				// 取消导致的错误（context.Canceled）吞掉，不回发 error（HMI 已收 cancelled）
				if ctx.Err() == nil {
					client.send(map[string]any{"type": "error", "message": err.Error()})
				}
				return
			}
			for {
				ev, err := stream.Recv()
				if err != nil {
					// 晚到的 grpc CANCELLED（ctx 已取消）不回发 error；正常 EOF/错误照旧收尾
					return
				}
				client.send(eventToMap(ev))
			}
		}(req, ctx, cancel, myGen)
	}
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
	orchAddr := getenv("EDGE_ORCHESTRATOR_ADDR", "edge-orchestrator:50070")
	port := getenv("EDGE_GATEWAY_PORT", "8090")
	auth := loadAuthConfig() // 层 1 会话鉴权（R3.1）；默认关，逐字保持现状

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
			if _, err := nc.Subscribe("agent.proactive", func(m *natsgo.Msg) {
				var p map[string]any
				if json.Unmarshal(m.Data, &p) != nil {
					return
				}
				// card 透传：异步深调研完成时带可读分节报告卡（p["card"]）；
				// 普通主动播报（路况/早报）无该键 → nil → HMI 端忽略，不影响既有行为。
				n := hub.broadcast(map[string]any{
					"type": "proactive", "speech": p["speech"],
					"advisory": p["type"], "source": p["agent_id"],
					"card": p["card"],
				})
				log.Printf("[edge-gateway] proactive(nats) -> %d HMI: %v", n, p["speech"])
			}); err != nil {
				log.Printf("[edge-gateway] NATS subscribe failed: %v", err)
			} else {
				log.Printf("[edge-gateway] NATS proactive bridge active (%s)", natsURL)
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
		log.Printf("[edge-gateway] HTTP/WS serving on :%s -> %s (auth_required=%v, tokens=%d, default_vehicle=%s)",
			port, orchAddr, auth.required, len(auth.tokens), auth.defaultVehicle)
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
