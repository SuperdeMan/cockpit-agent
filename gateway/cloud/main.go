// Cloud Gateway Phase 1：实现 EdgeCloudChannel bidi 双向流。
// 职责：握手鉴权 → 解复用请求 → 转发到 CloudPlanner → 回填 correlation_id → 心跳。
package main

import (
	"context"
	"io"
	"log"
	"net"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"

	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// ─── EdgeCloudChannel 实现 ───

type channelServer struct {
	channelpb.UnimplementedEdgeCloudChannelServer
	planner orchpb.CloudPlannerClient
	sessions sync.Map // vehicle_id -> *sessionState
	idem     IdempotencyStore
}

type sessionState struct {
	vehicleID string
	lastSeen  time.Time
}

// sendMu 保护 stream.Send（F13）：gRPC bidi stream 不支持并发 SendMsg，
// 主循环的 HelloAck/Pong 与 handleRequest goroutine 的 Event Send 可能交错。
type sendMu struct {
	mu     sync.Mutex
	stream channelpb.EdgeCloudChannel_ConnectServer
}

func (s *sendMu) Send(f *channelpb.DownFrame) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stream.Send(f)
}

func (s *channelServer) Connect(stream channelpb.EdgeCloudChannel_ConnectServer) error {
	var vehicleID string
	sm := &sendMu{stream: stream}

	for {
		up, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			log.Printf("[cloud-gateway] recv error: %v", err)
			return err
		}

		corrID := up.CorrelationId

		switch body := up.Body.(type) {
		case *channelpb.UpFrame_Hello:
			// 握手：校验 token（PoC 阶段简单通过）
			vehicleID = body.Hello.VehicleId
			if vehicleID == "" {
				return sm.Send(&channelpb.DownFrame{
					CorrelationId: corrID,
					Body: &channelpb.DownFrame_HelloAck{
						HelloAck: &channelpb.HelloAck{Ok: false, Reason: "missing vehicle_id"},
					},
				})
			}
			s.sessions.Store(vehicleID, &sessionState{vehicleID: vehicleID, lastSeen: time.Now()})
			log.Printf("[cloud-gateway] hello from %s", vehicleID)
			if err := sm.Send(&channelpb.DownFrame{
				CorrelationId: corrID,
				Body: &channelpb.DownFrame_HelloAck{
					HelloAck: &channelpb.HelloAck{Ok: true, HeartbeatSec: 15},
				},
			}); err != nil {
				return err
			}

		case *channelpb.UpFrame_Ping:
			// 心跳
			if state, ok := s.sessions.Load(vehicleID); ok {
				state.(*sessionState).lastSeen = time.Now()
			}
			if err := sm.Send(&channelpb.DownFrame{
				CorrelationId: corrID,
				Body:          &channelpb.DownFrame_Pong{Pong: &channelpb.Pong{Ts: time.Now().UnixMilli()}},
			}); err != nil {
				return err
			}

		case *channelpb.UpFrame_Request:
			// 请求：解复用 → 转发 Planner → 回填 correlation_id
			go s.handleRequest(sm, corrID, body.Request, vehicleID)

		case *channelpb.UpFrame_Ack:
			// 客户端确认（幂等/可靠投递），当前 PoC 仅记录
			log.Printf("[cloud-gateway] ack from %s: seq=%d", vehicleID, body.Ack.Seq)
		}
	}
}

func (s *channelServer) handleRequest(sm *sendMu,
	corrID string, req *orchpb.HandleRequest, vehicleID string) {

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// F18：MarkIfNew 原子化幂等检查（消除 Seen+Mark TOCTOU）
	if !s.idem.MarkIfNew(ctx, corrID, 10*time.Minute) {
		log.Printf("[cloud-gateway] duplicate corrID %s from %s, skipping", corrID, vehicleID)
		return
	}

	plannerStream, err := s.planner.Handle(ctx, req)
	if err != nil {
		log.Printf("[cloud-gateway] planner error for %s: %v", vehicleID, err)
		sm.Send(&channelpb.DownFrame{
			CorrelationId: corrID,
			Body: &channelpb.DownFrame_Event{
				Event: &orchpb.HandleEvent{
					Event: &orchpb.HandleEvent_Final{
						Final: &orchpb.FinalResult{Speech: "云端处理异常，请稍后重试。"},
					},
				},
			},
		})
		return
	}

	for {
		ev, err := plannerStream.Recv()
		if err == io.EOF {
			return
		}
		if err != nil {
			log.Printf("[cloud-gateway] planner stream error: %v", err)
			return
		}
		// F13：经 sendMu 加锁发送（主循环的 Pong 与此处的 Event 不再交错）
		if err := sm.Send(&channelpb.DownFrame{
			CorrelationId: corrID,
			Body:          &channelpb.DownFrame_Event{Event: ev},
		}); err != nil {
			log.Printf("[cloud-gateway] send error: %v", err)
			return
		}
	}
}

// ─── 辅助 ───

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// ─── 入口 ───

func main() {
	plannerAddr := getenv("CLOUD_PLANNER_ADDR", "cloud-planner:50054")
	port := getenv("CLOUD_GATEWAY_PORT", "8080")

	conn, err := grpc.NewClient(plannerAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("dial planner: %v", err)
	}
	defer conn.Close()

	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}

	s := grpc.NewServer(
		keepalivePolicy(),
	)
	channelpb.RegisterEdgeCloudChannelServer(s, &channelServer{
		planner: orchpb.NewCloudPlannerClient(conn),
		idem:    buildIdempotencyStore(),
	})

	log.Printf("[cloud-gateway] EdgeCloudChannel serving on :%s -> %s", port, plannerAddr)
	if err := s.Serve(lis); err != nil {
		log.Fatal(err)
	}
}

func keepalivePolicy() grpc.ServerOption {
	return grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
		MinTime:             5 * time.Second,
		PermitWithoutStream: true,
	})
}
