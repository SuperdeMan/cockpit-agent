package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
	"github.com/gorilla/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

type deniedVehicleOrchestrator struct {
	orchpb.UnimplementedEdgeOrchestratorServer
}

func (deniedVehicleOrchestrator) Handle(_ *orchpb.HandleRequest, _ orchpb.EdgeOrchestrator_HandleServer) error {
	return status.Error(codes.PermissionDenied, "vehicle identity mismatch")
}

func TestDeniedVehicleStreamEndsWithAnErrorAndNoDrivingObservation(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	orchpb.RegisterEdgeOrchestratorServer(server, deniedVehicleOrchestrator{})
	go server.Serve(listener)
	defer server.Stop()
	channel, err := grpc.NewClient(listener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer channel.Close()
	a := authConfig{defaultUserID: "u1", defaultVehicle: "v2"}
	httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		handleWS(w, r, orchpb.NewEdgeOrchestratorClient(channel), a)
	}))
	defer httpServer.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(httpServer.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	for i := 0; i < 2; i++ {
		var initial map[string]any
		if err := conn.ReadJSON(&initial); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.WriteJSON(wsRequest{Text: "你好", SessionID: "isolated-session", RequestID: "isolation-request"}); err != nil {
		t.Fatal(err)
	}
	var frame map[string]any
	if err := conn.ReadJSON(&frame); err != nil {
		t.Fatal(err)
	}
	if frame["type"] != "error" || frame["code"] != "permission_denied" || frame["request_id"] != "isolation-request" {
		t.Fatal(frame)
	}
	if _, exists := frame["driving"]; exists {
		t.Fatal("invented a driving observation", frame)
	}
}
