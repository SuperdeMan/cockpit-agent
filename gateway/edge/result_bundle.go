package main

import orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"

// Snapshot data only. Actions and confirmation remain on their original WS fields.
func resultBundleToMap(bundle *orchpb.ResultBundle) map[string]any {
	goals := make([]any, 0, len(bundle.Goals))
	for _, goal := range bundle.Goals {
		goals = append(goals, map[string]any{
			"goal_id": goal.GoalId, "origin_exchange_id": goal.OriginExchangeId,
			"source_sha256": goal.SourceSha256, "start": goal.Start, "end": goal.End, "coverage": goal.Coverage,
		})
	}
	results := make([]any, 0, len(bundle.Results))
	for _, result := range bundle.Results {
		results = append(results, map[string]any{
			"step_id": result.StepId, "goal_ids": stringsOrEmpty(result.GoalIds),
			"intent": result.Intent, "status": result.Status, "answer": result.Answer,
			"card_ref": result.CardRef, "operation_id": result.OperationId,
			"answer_state": result.AnswerState, "result_ref": result.ResultRef, "verification": result.Verification,
			"pending_edge": result.PendingEdge,
		})
	}
	cards := map[string]any{}
	for key, card := range bundle.Cards {
		if card != nil {
			cards[key] = card.AsMap()
		}
	}
	return map[string]any{
		"version": bundle.Version, "task_id": bundle.TaskId, "revision": bundle.Revision,
		"goals": goals, "results": results, "coverage_status": bundle.CoverageStatus,
		"display_text": bundle.DisplayText, "cards": cards,
	}
}
