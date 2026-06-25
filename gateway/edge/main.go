// Edge Gateway Phase 1：WebSocket 接入 + ChannelClient bidi 长连到云。
// 职责：HMI WebSocket → ChannelClient(多路复用) → Cloud Gateway → Planner。
// 心跳、断线重连（指数退避）、降级。
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	natsgo "github.com/nats-io/nats.go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	commonpb "github.com/cockpit/car-agent/gen/go/cockpit/common/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// ─── ChannelClient：bidi 长连 + 多路复用 + 重连 ───

const (
	maxReconnectDelay = 30 * time.Second
	pingInterval      = 15 * time.Second
	missedPongLimit   = 3
)

type channelState int32

const (
	stateDisconnected channelState = iota
	stateConnecting
	stateConnected
)

type pendingRequest struct {
	ch  chan *orchpb.HandleEvent
	ctx context.Context
}

type ChannelClient struct {
	cloudAddr string
	vehicleID string

	conn     *grpc.ClientConn
	stream   channelpb.EdgeCloudChannel_ConnectClient
	state    atomic.Int32
	mu       sync.RWMutex
	sendLock sync.Mutex // F13：保护 stream.Send（Request/pingLoop/connect 三处）

	pending    sync.Map // correlation_id -> *pendingRequest
	corrSeq    atomic.Int64
	missedPong atomic.Int32
}

func NewChannelClient(cloudAddr, vehicleID string) *ChannelClient {
	c := &ChannelClient{
		cloudAddr: cloudAddr,
		vehicleID: vehicleID,
	}
	c.state.Store(int32(stateDisconnected))
	return c
}

func (c *ChannelClient) Start(ctx context.Context) {
	go c.connectLoop(ctx)
}

func (c *ChannelClient) State() channelState {
	return channelState(c.state.Load())
}

func (c *ChannelClient) Request(ctx context.Context, text, sessionID string, isConfirmation bool) (<-chan *orchpb.HandleEvent, error) {
	if c.State() != stateConnected {
		return nil, fmt.Errorf("not connected to cloud")
	}

	corrID := fmt.Sprintf("%s-%d", c.vehicleID, c.corrSeq.Add(1))
	ch := make(chan *orchpb.HandleEvent, 32)
	c.pending.Store(corrID, &pendingRequest{ch: ch, ctx: ctx})

	req := &channelpb.UpFrame{
		CorrelationId: corrID,
		Body: &channelpb.UpFrame_Request{
			Request: &orchpb.HandleRequest{
				Text:           text,
				SessionId:      sessionID,
				IsConfirmation: isConfirmation,
				Context: &commonpb.ContextRef{
					SessionId: sessionID,
					VehicleId: c.vehicleID,
					UserId:    "u1",
				},
			},
		},
	}

	c.mu.RLock()
	stream := c.stream
	c.mu.RUnlock()

	if stream == nil {
		c.pending.Delete(corrID)
		return nil, fmt.Errorf("stream not ready")
	}

	c.sendLock.Lock()
	err := stream.Send(req)
	c.sendLock.Unlock()
	if err != nil {
		c.pending.Delete(corrID)
		return nil, fmt.Errorf("send failed: %w", err)
	}

	return ch, nil
}

func (c *ChannelClient) connectLoop(ctx context.Context) {
	delay := 500 * time.Millisecond
	for {
		if ctx.Err() != nil {
			return
		}

		c.state.Store(int32(stateConnecting))
		err := c.connect(ctx)
		if err == nil {
			delay = 500 * time.Millisecond // 成功后重置退避
			c.state.Store(int32(stateConnected))
			c.recvLoop(ctx)
		} else {
			log.Printf("[edge-gateway] connect failed: %v, retry in %v", err, delay)
		}

		c.state.Store(int32(stateDisconnected))
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}

		// 指数退避 + 抖动
		delay = delay * 2
		if delay > maxReconnectDelay {
			delay = maxReconnectDelay
		}
		delay += time.Duration(rand.Int63n(int64(delay / 4)))
	}
}

func (c *ChannelClient) connect(ctx context.Context) error {
	var err error
	c.conn, err = grpc.NewClient(c.cloudAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return fmt.Errorf("dial cloud: %w", err)
	}

	client := channelpb.NewEdgeCloudChannelClient(c.conn)
	c.stream, err = client.Connect(ctx)
	if err != nil {
		c.conn.Close()
		return fmt.Errorf("connect stream: %w", err)
	}

	// 握手（F13：经 sendLock 保护）
	helloCorrID := fmt.Sprintf("%s-hello", c.vehicleID)
	c.sendLock.Lock()
	helloErr := c.stream.Send(&channelpb.UpFrame{
		CorrelationId: helloCorrID,
		Body: &channelpb.UpFrame_Hello{
			Hello: &channelpb.Hello{VehicleId: c.vehicleID},
		},
	})
	c.sendLock.Unlock()
	if helloErr != nil {
		c.conn.Close()
		return fmt.Errorf("hello send: %w", helloErr)
	}

	// 等 HelloAck
	ack, err := c.stream.Recv()
	if err != nil {
		c.conn.Close()
		return fmt.Errorf("hello ack recv: %w", err)
	}
	if ha := ack.GetHelloAck(); ha != nil && !ha.Ok {
		c.conn.Close()
		return fmt.Errorf("hello rejected: %s", ha.Reason)
	}

	log.Printf("[edge-gateway] connected to cloud as %s", c.vehicleID)
	c.missedPong.Store(0)
	go c.pingLoop(ctx)
	return nil
}

func (c *ChannelClient) pingLoop(ctx context.Context) {
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if c.State() != stateConnected {
				return
			}
			corrID := fmt.Sprintf("%s-ping-%d", c.vehicleID, time.Now().UnixMilli())
			c.sendLock.Lock()
			pingErr := c.stream.Send(&channelpb.UpFrame{
				CorrelationId: corrID,
				Body:          &channelpb.UpFrame_Ping{Ping: &channelpb.Ping{Ts: time.Now().UnixMilli()}},
			})
			c.sendLock.Unlock()
			if pingErr != nil {
				log.Printf("[edge-gateway] ping send error: %v", pingErr)
			}
			if c.missedPong.Add(1) > missedPongLimit {
				log.Printf("[edge-gateway] missed too many pongs, reconnecting")
				c.stream.CloseSend()
				return
			}
		}
	}
}

func (c *ChannelClient) recvLoop(ctx context.Context) {
	for {
		if ctx.Err() != nil {
			return
		}
		down, err := c.stream.Recv()
		if err != nil {
			log.Printf("[edge-gateway] recv error: %v", err)
			return
		}

		switch body := down.Body.(type) {
		case *channelpb.DownFrame_Pong:
			c.missedPong.Store(0)

		case *channelpb.DownFrame_HelloAck:
			// 已在 connect() 处理

		case *channelpb.DownFrame_Event:
			if val, ok := c.pending.Load(down.CorrelationId); ok {
				pr := val.(*pendingRequest)
				select {
				case pr.ch <- body.Event:
					// 检查是否为 final
					if _, ok := body.Event.Event.(*orchpb.HandleEvent_Final); ok {
						close(pr.ch)
						c.pending.Delete(down.CorrelationId)
					}
				default:
					log.Printf("[edge-gateway] pending channel full for %s", down.CorrelationId)
				}
			}

		case *channelpb.DownFrame_Proactive:
			// 主动推送（如低电量提醒）：经云端 channel 下来 → 广播给已连 HMI
			n := hub.broadcast(map[string]any{
				"type": "proactive", "speech": body.Proactive.Speech,
				"advisory": body.Proactive.Type, "source": "cloud",
			})
			log.Printf("[edge-gateway] proactive(channel) -> %d HMI: %s",
				n, body.Proactive.Speech)
		}
	}
}

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
	Text           string            `json:"text"`
	SessionID      string            `json:"session_id"`
	IsConfirmation bool              `json:"is_confirmation"` // HMI 确认/取消按钮回应多轮确认时置 true
	Meta           map[string]string `json:"meta"`            // HMI 设置透传（answer_length/model_pref 等）
}

func handleWS(w http.ResponseWriter, r *http.Request, orch orchpb.EdgeOrchestratorClient, vehicleID string) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

	client := &wsClient{conn: conn}
	hub.register(client)
	defer hub.unregister(client)

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return
		}
		var req wsRequest
		if json.Unmarshal(msg, &req) != nil || req.Text == "" {
			continue
		}
		if req.SessionID == "" {
			req.SessionID = "default"
		}

		// 调端侧编排器（架构 §2.2：快意图本地秒回 / 慢意图上云）
		// 90s：复杂任务动态开思考端到端更慢，过程区覆盖等待；快意图仍毫秒级返回。
		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		stream, err := orch.Handle(ctx, &orchpb.HandleRequest{
			Text:           req.Text,
			SessionId:      req.SessionID,
			IsConfirmation: req.IsConfirmation,
			Meta:           req.Meta,
			Context: &commonpb.ContextRef{
				SessionId: req.SessionID,
				VehicleId: vehicleID,
				UserId:    "u1",
			},
		})
		if err != nil {
			cancel()
			client.send(map[string]any{"type": "error", "message": err.Error()})
			continue
		}

		for {
			ev, err := stream.Recv()
			if err != nil {
				cancel()
				break
			}
			client.send(eventToMap(ev))
		}
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

// ─── 入口 ───

func main() {
	orchAddr := getenv("EDGE_ORCHESTRATOR_ADDR", "edge-orchestrator:50070")
	port := getenv("EDGE_GATEWAY_PORT", "8090")
	vehicleID := getenv("VEHICLE_ID", "v1")

	// 连接端侧编排器（架构 §2.2：HMI → Edge Gateway → Edge Orchestrator）
	orchConn, err := grpc.NewClient(orchAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
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
				n := hub.broadcast(map[string]any{
					"type": "proactive", "speech": p["speech"],
					"advisory": p["type"], "source": p["agent_id"],
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
		handleWS(w, r, orchStub, vehicleID)
	})

	log.Printf("[edge-gateway] HTTP/WS serving on :%s -> %s (vehicle=%s)", port, orchAddr, vehicleID)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
