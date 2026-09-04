package main

import (
	"testing"

	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// B5 缺陷 C：final 帧也透传 driving（与 process 分支同款、恒带键不 omitempty——
// 客户端要能分辨「false」与「旧网关没有这个键」）。
func TestEventToMapFinalCarriesDriving(t *testing.T) {
	for _, want := range []bool{true, false} {
		ev := &orchpb.HandleEvent{Event: &orchpb.HandleEvent_Final{
			Final: &orchpb.FinalResult{Speech: "好的", Driving: want}}}
		m := eventToMap(ev)
		got, ok := m["driving"]
		if !ok {
			t.Fatalf("final frame lacks driving key (want %v): %v", want, m)
		}
		if got != want {
			t.Fatalf("driving=%v want %v", got, want)
		}
	}
}

func TestEventToMapProcessStillCarriesDriving(t *testing.T) {
	ev := &orchpb.HandleEvent{Event: &orchpb.HandleEvent_Progress{
		Progress: &orchpb.ProcessUpdate{Phase: "analyze", Driving: true}}}
	if got := eventToMap(ev)["driving"]; got != true {
		t.Fatalf("process driving=%v", got)
	}
}
