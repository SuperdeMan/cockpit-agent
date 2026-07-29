package main

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestParseAuthTokens(t *testing.T) {
	table := parseAuthTokens(
		"demo-u1:u1:v1:vehicle.control,media.control;demo-u2:u2:v2:location.read")
	if len(table) != 2 {
		t.Fatalf("want 2 tokens, got %d", len(table))
	}
	id := table["demo-u1"]
	if id.userID != "u1" || id.vehicleID != "v1" || id.scopes != "vehicle.control,media.control" {
		t.Fatalf("unexpected identity: %+v", id)
	}
	if table["demo-u2"].scopes != "location.read" {
		t.Fatalf("unexpected u2 scopes: %q", table["demo-u2"].scopes)
	}
}

func TestParseAuthTokensSkipsMalformed(t *testing.T) {
	// 空串 / 缺段(<4) / 空 token 都跳过；scope-csv 内部逗号保留。
	table := parseAuthTokens("  ;bad:only:three;:emptytoken:v:scope;ok:u:v:a.b,c.d")
	if len(table) != 1 {
		t.Fatalf("want 1 valid token, got %d: %+v", len(table), table)
	}
	if table["ok"].scopes != "a.b,c.d" {
		t.Fatalf("scope csv mangled: %q", table["ok"].scopes)
	}
}

func TestParseAuthTokensEmpty(t *testing.T) {
	if len(parseAuthTokens("")) != 0 {
		t.Fatalf("empty env should yield empty table")
	}
}

func TestResolveHitAndMiss(t *testing.T) {
	a := authConfig{
		tokens:         parseAuthTokens("demo:u9:v9:media.control"),
		defaultUserID:  "u1",
		defaultVehicle: "v1",
	}
	if id, ok := a.resolve("demo"); !ok || id.userID != "u9" || id.vehicleID != "v9" {
		t.Fatalf("resolve hit failed: %+v ok=%v", id, ok)
	}
	if _, ok := a.resolve("nope"); ok {
		t.Fatalf("unknown token should miss")
	}
	if _, ok := a.resolve(""); ok {
		t.Fatalf("empty token should miss")
	}
}

func TestResolveFillsDefaults(t *testing.T) {
	// token 表里 user/vehicle 段为空 → 回退进程默认。
	a := authConfig{
		tokens:         parseAuthTokens("demo:::navigation.control"),
		defaultUserID:  "u1",
		defaultVehicle: "v1",
	}
	id, ok := a.resolve("demo")
	if !ok || id.userID != "u1" || id.vehicleID != "v1" || id.scopes != "navigation.control" {
		t.Fatalf("defaults not filled: %+v ok=%v", id, ok)
	}
}

func TestStampScopesAuthoritative(t *testing.T) {
	// 客户端伪造的 granted_scopes 被剔除；token scope 注入；无关 meta 保留。
	meta := map[string]string{"granted_scopes": "vehicle.control", "answer_length": "short"}
	out := stampScopes(meta, "location.read")
	if out["granted_scopes"] != "location.read" {
		t.Fatalf("want token scopes, got %q", out["granted_scopes"])
	}
	if out["answer_length"] != "short" {
		t.Fatalf("unrelated meta dropped")
	}
}

func TestStampScopesAnonymousStripsClient(t *testing.T) {
	// 匿名（scope 空）：剔除客户端伪造值，不注入（交下游 fail-open 兜底）。
	meta := map[string]string{"granted_scopes": "vehicle.control"}
	out := stampScopes(meta, "")
	if _, present := out["granted_scopes"]; present {
		t.Fatalf("client-forged granted_scopes should be stripped in anonymous mode")
	}
}

func TestStampScopesNilMeta(t *testing.T) {
	if out := stampScopes(nil, ""); out != nil {
		t.Fatalf("nil meta with empty scopes should stay nil")
	}
	out := stampScopes(nil, "media.control")
	if out["granted_scopes"] != "media.control" {
		t.Fatalf("nil meta should be initialized with scopes")
	}
}

func TestE2EGateDisabledTreatsSignedPrefixAsOrdinaryUnknown(t *testing.T) {
	a := authConfig{
		defaultUserID:  "u1",
		defaultVehicle: "v1",
		now:            func() time.Time { return time.Unix(1700000000, 0) },
	}
	_, claims, ok, hardReject := a.resolveSession("e2e.v1.not-a-token")
	if ok || hardReject || claims != nil {
		t.Fatalf("disabled gate changed existing unknown-token behavior")
	}
}

func TestStaticAuthTokenHasPriorityOverE2EGate(t *testing.T) {
	token := "e2e.v1.normal-auth-token"
	a := authConfig{
		tokens: map[string]identity{
			token: {userID: "normal", vehicleID: "v9", scopes: "normal.scope"},
		},
		e2eEnabled:    true,
		e2eConfigErr:  errE2EIdentityConfig,
		defaultUserID: "u1",
		now:           func() time.Time { return time.Unix(1700000000, 0) },
	}
	id, claims, ok, hardReject := a.resolveSession(token)
	if !ok || hardReject || claims != nil || id.userID != "normal" {
		t.Fatalf("normal auth must win before E2E prefix handling: %+v", id)
	}
}

func TestEnabledE2EGateFailsClosedForMissingSecretAndBadToken(t *testing.T) {
	a := authConfig{
		e2eEnabled:    true,
		e2eConfigErr:  errE2EIdentityConfig,
		defaultUserID: "u1",
		now:           func() time.Time { return time.Unix(1700000000, 0) },
	}
	if _, _, ok, hard := a.resolveSession("e2e.v1.anything"); ok || !hard {
		t.Fatal("enabled gate with missing secret trusted an E2E prefix")
	}
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	a.e2eSecret, a.e2eConfigErr = secret, nil
	if _, _, ok, hard := a.resolveSession("e2e.v1.malformed"); ok || !hard {
		t.Fatal("malformed E2E token fell back to anonymous")
	}
}

func TestEnabledE2EGateHardRejectsTamperedAndMalformedV1Only(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	a := authConfig{
		e2eEnabled:     true,
		e2eSecret:      secret,
		defaultUserID:  "u1",
		defaultVehicle: "v1",
		now:            func() time.Time { return time.Unix(fixture.Now, 0) },
	}
	for _, token := range []string{
		"e2e.v1.malformed",
		fixture.Vectors[1].Token,
		fixture.Vectors[2].Token,
	} {
		if _, _, ok, hard := a.resolveSession(token); ok || !hard {
			t.Fatalf("invalid e2e.v1 token was not hard rejected: %q", token)
		}
	}
}

func TestWrongVersionVerifierRejectsButAuthKeepsUnknownTokenPolicy(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	if _, err := verifyE2EIdentity(
		"e2e.v2.payload.signature",
		secret,
		time.Unix(fixture.Now, 0),
	); err == nil {
		t.Fatal("verifier accepted an unsupported token version")
	}
	a := authConfig{
		e2eEnabled:     true,
		e2eSecret:      secret,
		defaultUserID:  "u1",
		defaultVehicle: "v1",
		now:            func() time.Time { return time.Unix(fixture.Now, 0) },
	}
	if _, claims, ok, hard := a.resolveSession("e2e.v2.payload.signature"); ok ||
		hard || claims != nil {
		t.Fatal("wrong-version token did not preserve ordinary unknown-token policy")
	}
}

func TestE2EAuthClaimsOnlyExactV1DotPrefix(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	a := authConfig{
		e2eEnabled:     true,
		e2eSecret:      secret,
		defaultUserID:  "u1",
		defaultVehicle: "v1",
		now:            func() time.Time { return time.Unix(fixture.Now, 0) },
	}
	for _, token := range []string{
		"e2e.v1x.payload.signature",
		"e2e.v10.payload.signature",
	} {
		if _, claims, ok, hard := a.resolveSession(token); ok ||
			hard || claims != nil {
			t.Fatalf("near-prefix token did not preserve unknown policy: %q", token)
		}
	}
	tampered := fixture.Vectors[1].Token
	if _, _, ok, hard := a.resolveSession(tampered); ok || !hard {
		t.Fatal("tampered exact e2e.v1. token was not hard rejected")
	}
}

func TestValidE2ETokenOwnsIdentityAndScopes(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	a := authConfig{
		e2eEnabled:     true,
		e2eSecret:      secret,
		defaultUserID:  "u1",
		defaultVehicle: "v-default",
		now:            func() time.Time { return time.Unix(fixture.Now, 0) },
	}
	id, claims, ok, hardReject := a.resolveSession(fixture.Vectors[0].Token)
	if !ok || hardReject || claims == nil {
		t.Fatal("valid signed identity rejected")
	}
	if id.userID != claims.UserID || id.vehicleID != claims.VehicleID {
		t.Fatalf("identity not taken from signed payload: %+v %#v", id, claims)
	}
	if id.scopes != "memory.read,memory.write" {
		t.Fatalf("scope authority did not come from signed payload: %q", id.scopes)
	}
}

func TestInvalidE2ETokenReturns401BeforeUpgradeWithoutLoggingToken(t *testing.T) {
	var logs bytes.Buffer
	previous := log.Writer()
	log.SetOutput(&logs)
	defer log.SetOutput(previous)
	a := authConfig{
		e2eEnabled:    true,
		e2eConfigErr:  errE2EIdentityConfig,
		defaultUserID: "u1",
		now:           time.Now,
	}
	request := httptest.NewRequest(http.MethodGet, "/ws?token=e2e.v1.secret-value", nil)
	response := httptest.NewRecorder()
	handleWS(response, request, nil, a)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("want pre-upgrade 401, got %d", response.Code)
	}
	if strings.Contains(logs.String(), "e2e.v1.secret-value") {
		t.Fatal("identity token leaked to logs")
	}
}

func TestValidE2ETokenReceivesOwnerAckAsFirstUpgradedFrame(t *testing.T) {
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
	_, raw, err := conn.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	var ack map[string]any
	if err := json.Unmarshal(raw, &ack); err != nil {
		t.Fatal(err)
	}
	if ack["type"] != "e2e_identity_ack" ||
		ack["run_id"] != fixture.Vectors[0].Claims.RunID ||
		ack["user_id"] != fixture.Vectors[0].Claims.UserID ||
		ack["vehicle_id"] != fixture.Vectors[0].Claims.VehicleID {
		t.Fatalf("first frame is not the signed owner ACK: %#v", ack)
	}
}
