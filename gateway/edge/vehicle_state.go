package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"sync"
	"time"

	"github.com/cockpit/car-agent/gateway/vehiclestate"
)

var vehicleProjection = struct {
	sync.Mutex
	store     *vehiclestate.Store
	epoch     string
	last      map[string]string
	revisions map[string]uint64
}{store: vehiclestate.NewStore(vehiclestate.LegacyPolicy("v1"), nil), epoch: newProjectionEpoch(), last: map[string]string{}, revisions: map[string]uint64{}}

func newProjectionEpoch() string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("vehicle projection identity unavailable")
	}
	return hex.EncodeToString(value[:])
}

func configureVehicleState(policy *vehiclestate.Policy) {
	vehicleProjection.Lock()
	defer vehicleProjection.Unlock()
	vehicleProjection.store = vehiclestate.NewStore(policy, nil)
	vehicleProjection.epoch = newProjectionEpoch()
	vehicleProjection.last = map[string]string{}
	vehicleProjection.revisions = map[string]uint64{}
}

func vehicleStateFrame(vehicleID string) (map[string]any, bool) {
	vehicleProjection.Lock()
	defer vehicleProjection.Unlock()
	view := vehicleProjection.store.View(vehicleID)
	encoded, _ := json.Marshal(view)
	changed := vehicleProjection.last[vehicleID] != string(encoded)
	if changed {
		vehicleProjection.last[vehicleID] = string(encoded)
		vehicleProjection.revisions[vehicleID]++
	}
	return map[string]any{"type": "vehicle_state", "version": 2, "vehicle_id": vehicleID,
		"projection_epoch": vehicleProjection.epoch, "revision": vehicleProjection.revisions[vehicleID],
		"state": view["state"], "observation": view}, changed
}

func receiveVehicleState(raw []byte) bool {
	vehicleProjection.Lock()
	result := vehicleProjection.store.Ingest(raw)
	vehicleProjection.Unlock()
	if !result.Accepted {
		return false
	}
	frame, changed := vehicleStateFrame(result.VehicleID)
	if changed {
		hub.broadcastFor(result.VehicleID, "", frame)
	}
	return true
}

func expireVehicleState(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			vehicleProjection.Lock()
			vehicles := vehicleProjection.store.Vehicles()
			vehicleProjection.Unlock()
			for _, vehicle := range vehicles {
				frame, changed := vehicleStateFrame(vehicle)
				if changed {
					hub.broadcastFor(vehicle, "", frame)
				}
			}
		}
	}
}

func vehStateSnapshot(vehicleIDs ...string) map[string]any {
	vehicle := vehiclestate.LegacyVehicle
	if len(vehicleIDs) > 0 {
		vehicle = vehicleIDs[0]
	}
	vehicleProjection.Lock()
	defer vehicleProjection.Unlock()
	return vehicleProjection.store.Snapshot(vehicle)
}
