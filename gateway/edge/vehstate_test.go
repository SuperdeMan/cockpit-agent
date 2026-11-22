package main

import (
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"

	"github.com/cockpit/car-agent/gateway/vehiclestate"
)

func TestVehicleStateProjectionIsScopedAndStableWithoutANewObservation(t *testing.T) {
	configureVehicleState(vehiclestate.LegacyPolicy("v1"))
	input := []byte(`{"changes":[{"key":"battery","new":72},{"key":"gear","new":"D"}]}`)
	if !receiveVehicleState(input) {
		t.Fatal("legacy simulator event rejected")
	}
	snap := vehStateSnapshot("v1")
	if snap["battery"] != 72.0 || snap["gear"] != "D" {
		t.Fatalf("bad projection: %v", snap)
	}
	if len(vehStateSnapshot("v2")) != 0 {
		t.Fatal("borrowed another vehicle's values")
	}
	first, _ := vehicleStateFrame("v1")
	again, changed := vehicleStateFrame("v1")
	if changed || first["revision"] != again["revision"] {
		t.Fatal("unchanged observation should not rebroadcast")
	}
	if receiveVehicleState([]byte(`{"changes":[{"new":1}]}`)) {
		t.Fatal("invalid entry accepted")
	}
	if !receiveVehicleState([]byte(`{"changes":[{"key":"battery","new":60}]}`)) {
		t.Fatal("valid delta rejected")
	}
	if got := vehStateSnapshot(); got["battery"] != 60.0 || got["gear"] != "D" {
		t.Fatalf("lost unchanged signal: %v", got)
	}
}

func TestEmptyVehicleProjectionClearsOldClientValues(t *testing.T) {
	configureVehicleState(vehiclestate.LegacyPolicy("v1"))
	frame, _ := vehicleStateFrame("v1")
	raw, err := json.Marshal(frame)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil {
		t.Fatal("frame is not JSON")
	}
	if len(parsed["state"].(map[string]any)) != 0 || parsed["vehicle_id"] != "v1" || parsed["version"] != 2.0 {
		t.Fatalf("invalid empty projection %v", parsed)
	}
}

func TestSnapshotRequestThrottlesAndRespectsForce(t *testing.T) {
	snapshotSync.Lock()
	snapshotSync.last = time.Time{}
	snapshotSync.Unlock()

	var calls []string
	publish := func(subject string, data []byte) error {
		calls = append(calls, subject+":"+string(data))
		return nil
	}
	base := time.Unix(1_700_000_000, 0)
	if !requestVehicleStateSnapshotWith(publish, base, false) {
		t.Fatal("initial snapshot request should publish")
	}
	if requestVehicleStateSnapshotWith(publish, base.Add(200*time.Millisecond), false) {
		t.Fatal("snapshot request within 1s throttle should be skipped")
	}
	if !requestVehicleStateSnapshotWith(publish, base.Add(300*time.Millisecond), true) {
		t.Fatal("forced snapshot request should bypass throttle")
	}
	if !requestVehicleStateSnapshotWith(publish, base.Add(1500*time.Millisecond), false) {
		t.Fatal("snapshot request after throttle window should publish")
	}
	if len(calls) != 3 {
		t.Fatalf("expected 3 snapshot requests, got %v", calls)
	}
	want := vehiclestate.SnapshotRequestSubject + ":{}"
	for _, got := range calls {
		if got != want {
			t.Fatalf("unexpected publish %q, want %q", got, want)
		}
	}
}

func TestReceiveVehicleStateResultReportsSnapshotRequired(t *testing.T) {
	configureVehicleState(vehiclestate.LegacyPolicy("v1"))
	nowMS := time.Now().UnixMilli()
	payload, err := json.Marshal(map[string]any{
		"vehicle_id":          "v1",
		"source_id":           "val-simulator",
		"source_epoch":        "boot-1",
		"epoch_started_at_ms": nowMS,
		"source_seq":          2,
		"emitted_at_ms":       nowMS,
		"snapshot":            false,
		"signals": []map[string]any{{
			"key":            "hvac_on",
			"value":          true,
			"observed_at_ms": nowMS,
			"quality":        "good",
			"unit":           "",
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	envelope, err := json.Marshal(map[string]any{
		"version":   2,
		"key_id":    "unsigned-simulator",
		"payload":   base64.StdEncoding.EncodeToString(payload),
		"signature": "",
	})
	if err != nil {
		t.Fatal(err)
	}
	res := receiveVehicleStateResult(envelope)
	if res.Accepted || res.Reason != "snapshot_required" {
		t.Fatalf("expected snapshot_required rejection, got %+v", res)
	}
}
