// Cloud Gateway Phase 1：实现 EdgeCloudChannel bidi 双向流。
// 职责：握手鉴权 → 解复用请求 → 转发到 CloudPlanner → 回填 correlation_id → 心跳。
package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/grpc/status"

	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	commonpb "github.com/cockpit/car-agent/gen/go/cockpit/common/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// ─── EdgeCloudChannel 实现 ───

type channelServer struct {
	channelpb.UnimplementedEdgeCloudChannelServer
	planner orchpb.CloudPlannerClient
	sessions sync.Map // vehicle_id -> *sessionState
	pending  sync.Map // correlation_id -> chan *EdgeResult
	edgeSeq  atomic.Uint64
	idem     IdempotencyStore
}

type sessionState struct {
	vehicleID string
	lastSeen  time.Time
	sender    *sendMu
}

// sendMu 保护 stream.Send（F13）：gRPC bidi stream 不支持并发 SendMsg，
// 主循环的 HelloAck/Pong 与 handleRequest goroutine 的 Event Send 可能交错。
type downFrameSender interface {
	Send(*channelpb.DownFrame) error
}

type sendMu struct {
	mu     sync.Mutex
	stream downFrameSender
}

func (s *sendMu) Send(f *channelpb.DownFrame) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stream.Send(f)
}

func (s *channelServer) Connect(stream channelpb.EdgeCloudChannel_ConnectServer) error {
	var vehicleID string
	var activeSession *sessionState
	sm := &sendMu{stream: stream}
	defer func() {
		if vehicleID != "" && activeSession != nil {
			s.sessions.CompareAndDelete(vehicleID, activeSession)
		}
	}()

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
			activeSession = &sessionState{
				vehicleID: vehicleID,
				lastSeen:  time.Now(),
				sender:    sm,
			}
			s.sessions.Store(vehicleID, activeSession)
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
			if err := bindRequestVehicle(body.Request, vehicleID); err != nil {
				return err
			}
			go s.handleRequest(sm, corrID, body.Request, vehicleID)

		case *channelpb.UpFrame_Ack:
			// 客户端确认（幂等/可靠投递），当前 PoC 仅记录
			log.Printf("[cloud-gateway] ack from %s: seq=%d", vehicleID, body.Ack.Seq)

		case *channelpb.UpFrame_EdgeResult:
			s.deliverEdgeResult(corrID, body.EdgeResult)
		}
	}
}

func bindRequestVehicle(req *orchpb.HandleRequest, vehicleID string) error {
	if vehicleID == "" {
		return status.Error(codes.Unauthenticated, "hello required before request")
	}
	if req == nil {
		return status.Error(codes.InvalidArgument, "missing request")
	}
	if req.Context == nil {
		req.Context = &commonpb.ContextRef{}
	}
	if claimed := req.Context.GetVehicleId(); claimed != "" && claimed != vehicleID {
		return status.Errorf(
			codes.PermissionDenied,
			"request vehicle %s does not match stream vehicle %s",
			claimed,
			vehicleID,
		)
	}
	req.Context.VehicleId = vehicleID
	return nil
}

// DispatchToEdge implements the internal unary API used by Cloud Planner.
func (s *channelServer) DispatchToEdge(
	ctx context.Context, envelope *channelpb.EdgeCallEnvelope,
) (*channelpb.EdgeResult, error) {
	if envelope == nil || envelope.GetCall() == nil {
		return nil, status.Error(codes.InvalidArgument, "missing edge call")
	}
	return s.dispatchEdgeCall(ctx, envelope.GetVehicleId(), envelope.GetCall())
}

func (s *channelServer) dispatchEdgeCall(
	ctx context.Context, vehicleID string, call *channelpb.EdgeCall,
) (*channelpb.EdgeResult, error) {
	value, ok := s.sessions.Load(vehicleID)
	if !ok {
		return nil, status.Errorf(codes.NotFound, "no active stream for vehicle %s", vehicleID)
	}
	session := value.(*sessionState)
	if session.sender == nil {
		return nil, status.Errorf(codes.NotFound, "no active sender for vehicle %s", vehicleID)
	}

	corrID := fmt.Sprintf("edge-%s-%d-%s", vehicleID, s.edgeSeq.Add(1), call.GetStepId())
	resultCh := make(chan *channelpb.EdgeResult, 1)
	s.pending.Store(corrID, resultCh)
	defer s.pending.Delete(corrID)

	if err := session.sender.Send(&channelpb.DownFrame{
		CorrelationId: corrID,
		Body:          &channelpb.DownFrame_EdgeCall{EdgeCall: call},
	}); err != nil {
		return nil, status.Errorf(codes.Unavailable, "send edge call: %v", err)
	}

	select {
	case result := <-resultCh:
		if result.GetStepId() != call.GetStepId() {
			return nil, status.Errorf(
				codes.Internal, "edge result step mismatch: want %s got %s",
				call.GetStepId(), result.GetStepId())
		}
		return result, nil
	case <-ctx.Done():
		return nil, status.FromContextError(ctx.Err()).Err()
	}
}

func (s *channelServer) deliverEdgeResult(
	corrID string, result *channelpb.EdgeResult,
) {
	value, ok := s.pending.Load(corrID)
	if !ok {
		log.Printf("[cloud-gateway] late/unknown edge result corrID=%s", corrID)
		return
	}
	select {
	case value.(chan *channelpb.EdgeResult) <- result:
	default:
		log.Printf("[cloud-gateway] duplicate edge result corrID=%s", corrID)
	}
}

func (s *channelServer) handleRequest(sm *sendMu,
	corrID string, req *orchpb.HandleRequest, vehicleID string) {

	// 90s：复杂任务动态开思考（行程/深度调研）端到端更慢，过程区覆盖等待；普通请求仍秒回。
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
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

	conn, err := grpc.NewClient(plannerAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		clientKeepalive())
	if err != nil {
		log.Fatalf("dial planner: %v", err)
	}
	defer conn.Close()

	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}

	s := grpc.NewServer(
		keepaliveServerParams(),
		keepalivePolicy(),
	)
	channelpb.RegisterEdgeCloudChannelServer(s, &channelServer{
		planner: orchpb.NewCloudPlannerClient(conn),
		idem:    buildIdempotencyStore(),
	})

	go func() {
		log.Printf("[cloud-gateway] EdgeCloudChannel serving on :%s -> %s", port, plannerAddr)
		if err := s.Serve(lis); err != nil {
			log.Fatal(err)
		}
	}()

	// 优雅停机：收到 SIGTERM/SIGINT（docker compose 重建/停止）时排空在途 RPC 再退出，
	// 不再硬杀正在处理的请求（减少重建容器期间的报错/无响应）。
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	log.Printf("[cloud-gateway] shutting down gracefully")
	s.GracefulStop()
}

func keepalivePolicy() grpc.ServerOption {
	return grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
		MinTime:             5 * time.Second,
		PermitWithoutStream: true,
	})
}

// keepaliveServerParams 让服务端也主动发 keepalive ping，及时发现死连接
// （断连/无响应根因），与客户端 keepalive 对称。
func keepaliveServerParams() grpc.ServerOption {
	return grpc.KeepaliveParams(keepalive.ServerParameters{
		Time:    20 * time.Second,
		Timeout: 10 * time.Second,
	})
}

// clientKeepalive 给出站 gRPC 连接加 keepalive：容器/NAT 掐断空闲连接后能在一个
// 周期内探测到并重连重解析 DNS（修复"依赖重启换 IP 后需重启本服务"）。
func clientKeepalive() grpc.DialOption {
	return grpc.WithKeepaliveParams(keepalive.ClientParameters{
		Time:                20 * time.Second,
		Timeout:             10 * time.Second,
		PermitWithoutStream: true,
	})
}
