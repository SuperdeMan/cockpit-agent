package main

import (
	"context"
	"io"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	agentpb "github.com/cockpit/car-agent/gen/go/cockpit/agent/v1"
	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	commonpb "github.com/cockpit/car-agent/gen/go/cockpit/common/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

type fakeDownSender struct {
	frames chan *channelpb.DownFrame
	err    error
}

func (f *fakeDownSender) Send(frame *channelpb.DownFrame) error {
	if f.err != nil {
		return f.err
	}
	f.frames <- frame
	return nil
}

func TestDispatchEdgeCallRejectsMissingVehicleStream(t *testing.T) {
	server := &channelServer{}

	_, err := server.dispatchEdgeCall(
		context.Background(), "missing", &channelpb.EdgeCall{StepId: "s1"})

	if status.Code(err) != codes.NotFound {
		t.Fatalf("expected NotFound, got %v", err)
	}
}

func TestDispatchEdgeCallPairsResultByCorrelationID(t *testing.T) {
	server := &channelServer{}
	sender := &fakeDownSender{frames: make(chan *channelpb.DownFrame, 1)}
	server.sessions.Store("v1", &sessionState{
		vehicleID: "v1",
		sender:    &sendMu{stream: sender},
	})

	go func() {
		frame := <-sender.frames
		if frame.GetEdgeCall().GetStepId() != "s1" {
			t.Errorf("unexpected edge call: %v", frame)
		}
		server.deliverEdgeResult(frame.GetCorrelationId(), &channelpb.EdgeResult{
			StepId: "s1",
			Result: &agentpb.ExecuteResponse{
				Status: agentpb.ExecuteResponse_OK,
				Speech: "done",
			},
		})
	}()

	result, err := server.dispatchEdgeCall(
		context.Background(), "v1", &channelpb.EdgeCall{StepId: "s1"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if result.GetResult().GetSpeech() != "done" {
		t.Fatalf("unexpected result: %v", result)
	}
}

func TestDispatchEdgeCallHonorsContextDeadline(t *testing.T) {
	server := &channelServer{}
	sender := &fakeDownSender{frames: make(chan *channelpb.DownFrame, 1)}
	server.sessions.Store("v1", &sessionState{
		vehicleID: "v1",
		sender:    &sendMu{stream: sender},
	})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()

	_, err := server.dispatchEdgeCall(
		ctx, "v1", &channelpb.EdgeCall{StepId: "s1"})

	if status.Code(err) != codes.DeadlineExceeded {
		t.Fatalf("expected DeadlineExceeded, got %v", err)
	}
}

func TestBindRequestVehicleRejectsCrossVehicleRequest(t *testing.T) {
	req := &orchpb.HandleRequest{
		Context: &commonpb.ContextRef{VehicleId: "v2"},
	}

	err := bindRequestVehicle(req, "v1")

	if status.Code(err) != codes.PermissionDenied {
		t.Fatalf("expected PermissionDenied, got %v", err)
	}
}

func TestBindRequestVehicleFillsAuthenticatedStreamVehicle(t *testing.T) {
	req := &orchpb.HandleRequest{}

	if err := bindRequestVehicle(req, "v1"); err != nil {
		t.Fatalf("bind failed: %v", err)
	}
	if req.GetContext().GetVehicleId() != "v1" {
		t.Fatalf("unexpected vehicle id: %s", req.GetContext().GetVehicleId())
	}
}

// ─── R3.1 层 2：通道鉴权（Hello session_token）───

func TestChannelTokenAllowed(t *testing.T) {
	// AUTH_REQUIRED=false → 恒放行（保持现状）。
	open := &channelServer{authRequired: false}
	if !open.channelTokenAllowed("") || !open.channelTokenAllowed("whatever") {
		t.Fatalf("auth off should allow any token")
	}
	// AUTH_REQUIRED=true → token 须非空且在允许集内。
	secured := &channelServer{authRequired: true, channelTokens: parseChannelTokens("a, b ,c")}
	if !secured.channelTokenAllowed("a") || !secured.channelTokenAllowed("b") {
		t.Fatalf("valid token should be allowed")
	}
	if secured.channelTokenAllowed("") || secured.channelTokenAllowed("x") {
		t.Fatalf("empty/unknown token should be rejected when required")
	}
}

// fakeConnectStream 是 EdgeCloudChannel_ConnectServer 的最小实现：按序回放 Recv 帧、收集 Send。
// 嵌入 grpc.ServerStream 只为满足接口——Connect 的 Hello 路径不调其余方法（调则 panic 暴露）。
type fakeConnectStream struct {
	grpc.ServerStream
	recv []*channelpb.UpFrame
	idx  int
	sent []*channelpb.DownFrame
}

func (f *fakeConnectStream) Recv() (*channelpb.UpFrame, error) {
	if f.idx >= len(f.recv) {
		return nil, io.EOF
	}
	fr := f.recv[f.idx]
	f.idx++
	return fr, nil
}

func (f *fakeConnectStream) Send(fr *channelpb.DownFrame) error {
	f.sent = append(f.sent, fr)
	return nil
}

func helloFrame(vehicle, token string) *channelpb.UpFrame {
	return &channelpb.UpFrame{
		CorrelationId: "c1",
		Body: &channelpb.UpFrame_Hello{
			Hello: &channelpb.Hello{VehicleId: vehicle, SessionToken: token},
		},
	}
}

func TestConnectRejectsInvalidChannelToken(t *testing.T) {
	s := &channelServer{authRequired: true, channelTokens: parseChannelTokens("good")}
	stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame("v1", "bad")}}

	_ = s.Connect(stream) // Hello 校验失败 → 发 hello_ack 后 return

	if len(stream.sent) != 1 {
		t.Fatalf("want 1 hello_ack, got %d", len(stream.sent))
	}
	ack := stream.sent[0].GetHelloAck()
	if ack == nil || ack.Ok {
		t.Fatalf("want hello_ack ok=false, got %+v", ack)
	}
}

func TestConnectAcceptsValidChannelToken(t *testing.T) {
	s := &channelServer{authRequired: true, channelTokens: parseChannelTokens("good")}
	// 第一帧有效 Hello；第二帧缺省 → Recv 返回 io.EOF → Connect 正常收尾。
	stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame("v1", "good")}}

	if err := s.Connect(stream); err != nil {
		t.Fatalf("connect returned error: %v", err)
	}
	if len(stream.sent) != 1 {
		t.Fatalf("want 1 hello_ack, got %d", len(stream.sent))
	}
	ack := stream.sent[0].GetHelloAck()
	if ack == nil || !ack.Ok {
		t.Fatalf("want hello_ack ok=true, got %+v", ack)
	}
}
