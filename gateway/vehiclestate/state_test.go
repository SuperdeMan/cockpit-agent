package vehiclestate

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestCrossLanguageWireVectors(t *testing.T) {
	data, err := os.ReadFile("../../test/fixtures/vehicle_state_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Now       int64           `json:"now_ms"`
		Policy    json.RawMessage `json:"policy"`
		Scenarios []struct {
			Name   string `json:"name"`
			Start  int64  `json:"start_ms"`
			Legacy bool   `json:"legacy"`
			Steps  []struct {
				Raw         *string                      `json:"raw"`
				AdvanceMS   int64                        `json:"advance_ms"`
				WallDeltaMS int64                        `json:"wall_delta_ms"`
				Accepted    bool                         `json:"accepted"`
				Reason      string                       `json:"reason"`
				States      map[string]map[string]any    `json:"states"`
				Qualities   map[string]map[string]string `json:"qualities"`
				Sequences   map[string]map[string]int64  `json:"sequences"`
			} `json:"steps"`
		} `json:"scenarios"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, scenario := range fixture.Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			policy, err := ParsePolicy(string(fixture.Policy), "v1")
			if err != nil {
				t.Fatal(err)
			}
			if scenario.Legacy {
				policy = LegacyPolicy("v1")
			}
			wall, mono := fixture.Now, int64(0)
			if scenario.Start != 0 {
				wall = scenario.Start
			}
			store := NewStore(policy, func() (int64, int64) { return wall, mono })
			for i, step := range scenario.Steps {
				wall += step.AdvanceMS + step.WallDeltaMS
				mono += step.AdvanceMS
				if step.Raw != nil {
					got := store.Ingest([]byte(*step.Raw))
					if got.Accepted != step.Accepted || got.Reason != step.Reason {
						t.Fatalf("step %d: %+v want accepted=%t reason=%s", i, got, step.Accepted, step.Reason)
					}
				}
				for vehicle, want := range step.States {
					if got := store.Snapshot(vehicle); !reflect.DeepEqual(got, want) {
						t.Fatalf("step %d car %s: %v want %v", i, vehicle, got, want)
					}
				}
				for vehicle, checks := range step.Qualities {
					meta := store.View(vehicle)["signals"].(map[string]any)
					for key, want := range checks {
						if got := meta[key].(map[string]any)["quality"]; got != want {
							t.Fatalf("step %d %s.%s: %v want %v", i, vehicle, key, got, want)
						}
					}
				}
				for vehicle, checks := range step.Sequences {
					meta := store.View(vehicle)["signals"].(map[string]any)
					for key, want := range checks {
						if got := meta[key].(map[string]any)["source_seq"]; got != want {
							t.Fatalf("step %d %s.%s seq %v want %v", i, vehicle, key, got, want)
						}
					}
				}
			}
		})
	}
}

func TestCallerCannotMutateAdmittedPolicyOrNestedValues(t *testing.T) {
	policy := LegacyPolicy("v1")
	wall, mono := int64(1800000000000), int64(0)
	store := NewStore(policy, func() (int64, int64) { return wall, mono })
	policy.Legacy.VehicleID = "v2"
	policy.Legacy.TTL["*"] = 99999999
	if got := store.Ingest([]byte(`{"changes":[{"key":"location","new":{"city":"深圳"}}]}`)); !got.Accepted {
		t.Fatal(got)
	}
	store.Snapshot("v1")["location"].(map[string]any)["city"] = "changed"
	if got := store.Snapshot("v1")["location"].(map[string]any)["city"]; got != "深圳" {
		t.Fatal(got)
	}
	mono += 180001
	if len(store.Snapshot("v1")) != 0 || len(store.Snapshot("v2")) != 0 {
		t.Fatal("caller changed admitted trust or TTL")
	}
}

func TestTokenBindsOneVehicleAndBadConfigurationDoesNotEnableLegacy(t *testing.T) {
	p := &Policy{Sources: map[string]Binding{"a": {VehicleID: "v1", ChannelHash: ChannelTokenDigest("fixture-token")}}}
	if !p.ChannelVehicleAllowed("fixture-token", "v1") || p.ChannelVehicleAllowed("fixture-token", "v2") || p.ChannelVehicleAllowed("", "v1") {
		t.Fatal("channel identity not bound")
	}
	for _, raw := range []string{"{}", "not-json", `{"version":1,"sources":[]}`} {
		if _, err := ParsePolicy(raw, "v1"); err == nil {
			t.Fatalf("bad configuration accepted: %s", raw)
		}
	}
	if LegacyPolicy("").ChannelVehicleAllowed("", "v1") {
		t.Fatal("disabled legacy reopened")
	}
}

func TestCanonicalProductionProfileCannotReopenUnsignedCompatibility(t *testing.T) {
	t.Setenv("VEHICLE_STATE_TRUST", "")
	for _, profile := range []string{"prod", "PROD", " prod "} {
		t.Setenv("DEPLOY_PROFILE", profile)
		policy, err := LoadPolicy()
		if err != nil || policy.Legacy != nil {
			t.Fatalf("profile %q reopened compatibility: %v", profile, err)
		}
	}
	t.Setenv("DEPLOY_PROFILE", "production")
	if _, err := LoadPolicy(); err == nil {
		t.Fatal("unknown profile enabled legacy")
	}
}
