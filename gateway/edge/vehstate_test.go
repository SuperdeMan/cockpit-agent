package main

import (
	"encoding/json"
	"github.com/cockpit/car-agent/gateway/vehiclestate"
	"testing"
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
