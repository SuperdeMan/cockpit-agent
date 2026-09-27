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

func TestResultBundleSurvivesWithoutBecomingAnAction(t *testing.T) {
	ev := &orchpb.HandleEvent{Event: &orchpb.HandleEvent_Final{Final: &orchpb.FinalResult{
		Speech: "短简报", NeedConfirm: true, OperationId: "op-1",
		ResultBundles: []*orchpb.ResultBundle{{Version: 1, TaskId: "task-1", Revision: 2,
			CoverageStatus: "unknown", DisplayText: "完整回答，末尾也在",
			Results: []*orchpb.ResultEntry{{StepId: "s1", Status: "ok", Answer: "完整回答，末尾也在",
				AnswerState: "inline", ResultRef: "task-1/s1", CardRef: "final:", Verification: "unknown"}},
		}},
	}}}
	m := eventToMap(ev)
	if m["speech"] != "短简报" || m["need_confirm"] != true || m["operation_id"] != "op-1" {
		t.Fatalf("legacy control fields changed: %v", m)
	}
	b := m["result_bundles"].([]any)[0].(map[string]any)
	if b["display_text"] != "完整回答，末尾也在" || b["task_id"] != "task-1" {
		t.Fatalf("result snapshot lost: %v", b)
	}
	r := b["results"].([]any)[0].(map[string]any)
	if _, exists := r["actions"]; exists || r["card_ref"] != "final:" {
		t.Fatalf("snapshot is not data-only: %v", r)
	}
}
