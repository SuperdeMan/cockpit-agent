package main

import (
	"github.com/cockpit/car-agent/gateway/vehiclestate"
	"github.com/gorilla/websocket"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestAuthenticatedWebSocketsReceiveOnlyTheirVehicleAndUser(t *testing.T) {
	configureVehicleState(vehiclestate.LegacyPolicy("v1"))
	a := authConfig{required: true, tokens: map[string]identity{
		"a": {userID: "u1", vehicleID: "v1"}, "b": {userID: "u1", vehicleID: "v2"}, "c": {userID: "u2", vehicleID: "v1"}}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { handleWS(w, r, nil, a) }))
	defer srv.Close()
	var clients []*websocket.Conn
	for _, token := range []string{"a", "b", "c"} {
		c, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http")+"/?token="+token, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
		var identityFrame, initial map[string]any
		if err := c.ReadJSON(&identityFrame); err != nil {
			t.Fatal(err)
		}
		if identityFrame["type"] != "session_identity" || identityFrame["vehicle_id"] != a.tokens[token].vehicleID {
			t.Fatal(identityFrame)
		}
		if err := c.ReadJSON(&initial); err != nil {
			t.Fatal(err)
		}
		if initial["vehicle_id"] != a.tokens[token].vehicleID {
			t.Fatal(initial)
		}
		clients = append(clients, c)
	}
	if n := hub.broadcastFor("v1", "u1", map[string]any{"type": "private-proactive"}); n != 1 {
		t.Fatalf("recipient count %d", n)
	}
	if n := hub.broadcastFor("v2", "", map[string]any{"type": "car-b-state"}); n != 1 {
		t.Fatalf("recipient count %d", n)
	}
	// A barrier proves absence of a leaked frame without a timeout-based test.
	hub.broadcast(map[string]any{"type": "barrier"})
	for i, c := range clients {
		var frame map[string]any
		if err := c.ReadJSON(&frame); err != nil {
			t.Fatal(err)
		}
		want := []string{"private-proactive", "car-b-state", "barrier"}[i]
		if frame["type"] != want {
			t.Fatalf("client %d received %v", i, frame)
		}
		if i < 2 {
			if err := c.ReadJSON(&frame); err != nil || frame["type"] != "barrier" {
				t.Fatalf("leaked frame %v err %v", frame, err)
			}
		}
	}
}
