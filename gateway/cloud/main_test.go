package main

import (
	"context"
	"testing"
	"time"

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
