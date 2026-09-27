package main

import (
	"github.com/cockpit/car-agent/gateway/vehiclestate"
	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"testing"
)

func TestValidChannelTokenCannotSelectAnotherVehicle(t *testing.T) {
	policy := &vehiclestate.Policy{Sources: map[string]vehiclestate.Binding{"fixture": {VehicleID: "v1", ChannelHash: vehiclestate.ChannelTokenDigest("good")}}}
	for _, vehicle := range []string{"v1", "v2"} {
		s := &channelServer{authRequired: true, channelTokens: parseChannelTokens("good"), vehiclePolicy: policy}
		stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame(vehicle, "good")}}
		if err := s.Connect(stream); err != nil {
			t.Fatal(err)
		}
		if len(stream.sent) != 1 || stream.sent[0].GetHelloAck().Ok != (vehicle == "v1") {
			t.Fatalf("unexpected binding verdict for %s", vehicle)
		}
	}
}

func TestSecondHelloCannotRebindOrLeaveAnOrphanSession(t *testing.T) {
	s := &channelServer{}
	stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame("v1", ""), helloFrame("v2", "")}}
	if err := s.Connect(stream); status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("want failed precondition, got %v", err)
	}
	for _, vehicle := range []string{"v1", "v2"} {
		if _, ok := s.sessions.Load(vehicle); ok {
			t.Fatalf("orphan session %s", vehicle)
		}
	}
}
