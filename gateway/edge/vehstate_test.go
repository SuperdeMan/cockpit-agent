package main

import "testing"

// 车况镜像合并/去重（右舞台动态取数的网关侧契约）：
// 增量 diff 合并进镜像；周期全量快照重放（内容相同）不算变化（不给 HMI 发无变化帧）。
func TestMergeVehStateMergesAndDedupes(t *testing.T) {
	vehState.mu.Lock()
	vehState.m = map[string]any{}
	vehState.last = ""
	vehState.mu.Unlock()

	snap, changed := mergeVehState([]map[string]any{
		{"key": "battery", "old": nil, "new": 72.0},
		{"key": "gear", "old": "P", "new": "D"},
	})
	if !changed || snap["battery"] != 72.0 || snap["gear"] != "D" {
		t.Fatalf("first merge: snap=%v changed=%v", snap, changed)
	}

	// 周期全量快照重放同样内容 → 非变化
	if _, changed = mergeVehState([]map[string]any{
		{"key": "battery", "old": nil, "new": 72.0},
		{"key": "gear", "old": nil, "new": "D"},
	}); changed {
		t.Fatal("replayed identical snapshot must not count as change")
	}

	// 真变更：合并且保留未变键
	snap, changed = mergeVehState([]map[string]any{{"key": "battery", "new": 60.0}})
	if !changed || snap["battery"] != 60.0 || snap["gear"] != "D" {
		t.Fatalf("delta merge: snap=%v changed=%v", snap, changed)
	}
	if got := vehStateSnapshot(); got["battery"] != 60.0 || got["gear"] != "D" {
		t.Fatalf("snapshot getter mismatch: %v", got)
	}

	// 无 key 的脏条目忽略
	if _, changed = mergeVehState([]map[string]any{{"new": 1.0}}); changed {
		t.Fatal("entry without key must be ignored")
	}
}

func TestVehStateSnapshotEmptyIsNil(t *testing.T) {
	vehState.mu.Lock()
	vehState.m = map[string]any{}
	vehState.last = ""
	vehState.mu.Unlock()
	if vehStateSnapshot() != nil {
		t.Fatal("empty mirror should yield nil (connect-time push skipped)")
	}
}
