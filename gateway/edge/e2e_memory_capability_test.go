package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestSignedE2ERequestForwardsPlainSessionAndDedicatedCapability(t *testing.T) {
	userID := "e2e-run-abc-e2e_journeys"
	req := wsRequest{
		Text:                "remember this",
		SessionID:           userID + "-session-1",
		E2EMemoryCapability: "e2emem.v1.payload.signature",
		Meta:                map[string]string{"answer_length": "short"},
	}
	claims := &e2eIdentityClaims{RunID: "e2e-run-abc", UserID: userID}
	out, err := buildHandleRequest(req, identity{
		userID: userID, vehicleID: "vehicle-1", scopes: "memory.write",
	}, claims)
	if err != nil {
		t.Fatal(err)
	}
	if out.SessionId != userID+"-session-1" {
		t.Fatalf("business session was replaced: %q", out.SessionId)
	}
	if out.E2EMemoryCapability != req.E2EMemoryCapability {
		t.Fatal("dedicated memory capability was not forwarded")
	}
	if _, leaked := out.Meta["e2e_memory_capability"]; leaked {
		t.Fatal("memory capability leaked into ordinary meta")
	}
}

func TestUnsignedRequestCannotForwardMemoryCapability(t *testing.T) {
	out, err := buildHandleRequest(
		wsRequest{
			Text:                "remember this",
			SessionID:           "ordinary-session",
			E2EMemoryCapability: "e2emem.v1.payload.signature",
		},
		identity{userID: "u1", vehicleID: "v1"},
		nil,
	)
	if err == nil || out != nil {
		t.Fatal("unsigned memory capability was forwarded")
	}
}

func TestInvalidSignedBusinessSessionStillClosesWith1008(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	a := authConfig{
		e2eEnabled:     true,
		e2eSecret:      secret,
		defaultUserID:  "u1",
		defaultVehicle: "v-default",
		now:            func() time.Time { return time.Unix(fixture.Now, 0) },
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		handleWS(w, r, nil, a)
	}))
	defer server.Close()
	wsURL := "ws" + strings.TrimPrefix(server.URL, "http") +
		"/?token=" + fixture.Vectors[0].Token
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, _, err := conn.ReadMessage(); err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(wsRequest{
		Text:                "remember this",
		SessionID:           "e2emem.v1.payload.signature",
		E2EMemoryCapability: "e2emem.v1.payload.signature",
	})
	if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
		t.Fatal(err)
	}
	_, _, err = conn.ReadMessage()
	closeErr, ok := err.(*websocket.CloseError)
	if !ok || closeErr.Code != websocket.ClosePolicyViolation {
		t.Fatalf("want close 1008, got %v", err)
	}
}
