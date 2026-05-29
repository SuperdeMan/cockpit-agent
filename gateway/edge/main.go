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

	conn    *grpc.ClientConn
	stream  channelpb.EdgeCloudChannel_ConnectClient
	state   atomic.Int32
	mu      sync.RWMutex

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

func (c *ChannelClient) Request(ctx context.Context, text, sessionID string) (<-chan *orchpb.HandleEvent, error) {
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
				Text:      text,
				SessionId: sessionID,
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

	if err := stream.Send(req); err != nil {
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

	// 握手
	helloCorrID := fmt.Sprintf("%s-hello", c.vehicleID)
	if err := c.stream.Send(&channelpb.UpFrame{
		CorrelationId: helloCorrID,
		Body: &channelpb.UpFrame_Hello{
			Hello: &channelpb.Hello{VehicleId: c.vehicleID},
		},
	}); err != nil {
		c.conn.Close()
		return fmt.Errorf("hello send: %w", err)
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
			c.stream.Send(&channelpb.UpFrame{
				CorrelationId: corrID,
				Body:          &channelpb.UpFrame_Ping{Ping: &channelpb.Ping{Ts: time.Now().UnixMilli()}},
			})
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
			// 主动推送（如低电量提醒），当前仅日志
			log.Printf("[edge-gateway] proactive: %s - %s", body.Proactive.Type, body.Proactive.Speech)
		}
	}
}

// ─── WebSocket 处理 ───

var upgrader = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}

type wsRequest struct {
	Text      string `json:"text"`
	SessionID string `json:"session_id"`
}

func handleWS(w http.ResponseWriter, r *http.Request, cc *ChannelClient) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

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

		if cc.State() != stateConnected {
			writeJSON(conn, map[string]any{
				"type": "error", "message": "云端不可用，车内控制仍可正常使用。",
			})
			continue
		}

		ch, err := cc.Request(r.Context(), req.Text, req.SessionID)
		if err != nil {
			writeJSON(conn, map[string]any{"type": "error", "message": err.Error()})
			continue
		}

		for ev := range ch {
			writeJSON(conn, eventToMap(ev))
		}
	}
}

func eventToMap(ev *orchpb.HandleEvent) map[string]any {
	switch e := ev.Event.(type) {
	case *orchpb.HandleEvent_SpeechDelta:
		return map[string]any{"type": "speech_delta", "delta": e.SpeechDelta}
	case *orchpb.HandleEvent_Action:
		return map[string]any{"type": "action", "action": actionToMap(e.Action)}
	case *orchpb.HandleEvent_Final:
		f := e.Final
		actions := make([]any, 0, len(f.Actions))
		for _, a := range f.Actions {
			actions = append(actions, actionToMap(a))
		}
		return map[string]any{
			"type": "final", "speech": f.Speech, "follow_up": f.FollowUp,
			"need_confirm": f.NeedConfirm, "actions": actions,
		}
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
	cloudAddr := getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
	port := getenv("EDGE_GATEWAY_PORT", "8090")
	vehicleID := getenv("VEHICLE_ID", "v1")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	cc := NewChannelClient(cloudAddr, vehicleID)
	cc.Start(ctx)

	// 等待连接就绪（最多 5s）
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cc.State() == stateConnected {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}

	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		state := "disconnected"
		if cc.State() == stateConnected {
			state = "connected"
		}
		fmt.Fprintf(w, `{"status":"ok","cloud":"%s"}`, state)
	})
	http.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		handleWS(w, r, cc)
	})

	log.Printf("[edge-gateway] HTTP/WS serving on :%s -> %s (vehicle=%s)", port, cloudAddr, vehicleID)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
