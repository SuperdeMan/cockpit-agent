package main

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// fakeOrch 只实现 DescribeSession；Handle 不该被这条路径碰到——**只读查询走不到编排**
// 是 AR05 §6.1 的前提（零业务副作用），所以它被调到就是缺陷，直接让测试炸。
type fakeOrch struct {
	orchpb.EdgeOrchestratorClient
	seenMeta map[string]string
	seenCtx  *orchpb.SessionInfoRequest
	resp     *orchpb.SessionInfoResponse
	err      error
}

func (f *fakeOrch) DescribeSession(
	_ context.Context, in *orchpb.SessionInfoRequest, _ ...grpc.CallOption,
) (*orchpb.SessionInfoResponse, error) {
	f.seenCtx = in
	f.seenMeta = in.GetMeta()
	if f.err != nil {
		return nil, f.err
	}
	return f.resp, nil
}

func (f *fakeOrch) Handle(
	context.Context, *orchpb.HandleRequest, ...grpc.CallOption,
) (grpc.ServerStreamingClient[orchpb.HandleEvent], error) {
	panic("session info must not go through the orchestration path")
}

func getSession(t *testing.T, a authConfig, orch orchpb.EdgeOrchestratorClient,
	target string, header map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, target, nil)
	for k, v := range header {
		request.Header.Set(k, v)
	}
	response := httptest.NewRecorder()
	handleSessionInfo(response, request, orch, a)
	return response
}

func decodeBody(t *testing.T, r *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal(r.Body.Bytes(), &body); err != nil {
		t.Fatalf("response is not JSON: %v (%s)", err, r.Body.String())
	}
	return body
}

func okSummary() *orchpb.SessionInfoResponse {
	return &orchpb.SessionInfoResponse{
		ContractVersion:     "ar05.1",
		Authenticated:       true,
		UserId:              "u1",
		VehicleId:           "v1",
		AuthorizationSource: "token",
		GrantedScopes:       []string{"vehicle.control"},
		Capabilities: []*orchpb.CapabilityStatus{
			{Id: "edge-vehicle", DisplayName: "车辆控制", Status: "available"},
			{Id: "edge-media", Status: "unauthorized", ReasonCode: "scope_missing"},
		},
		GeneratedAtMs:  100,
		ExpiresAtMs:    60_100,
		SummaryStatus:  "complete",
	}
}

func tokenAuth() authConfig {
	return authConfig{
		tokens: map[string]identity{
			"good": {userID: "u1", vehicleID: "v1", scopes: "vehicle.control"},
		},
		defaultUserID:  "u1",
		defaultVehicle: "v1",
		now:            time.Now,
	}
}

func TestSessionInfoStampsScopesFromTheTokenNotTheClient(t *testing.T) {
	orch := &fakeOrch{resp: okSummary()}
	// 客户端在查询串里塞了自己的 scope——网关必须按 token 覆写（同 stampScopes 的唯一权威）
	r := getSession(t, tokenAuth(), orch, "/api/session?token=good&granted_scopes=admin", nil)

	if r.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", r.Code)
	}
	if got := orch.seenMeta["granted_scopes"]; got != "vehicle.control" {
		t.Fatalf("scopes must come from the token, got %q", got)
	}
	if orch.seenMeta["authenticated"] != "true" {
		t.Fatalf("authenticated flag missing: %#v", orch.seenMeta)
	}
	if orch.seenCtx.GetContext().GetUserId() != "u1" {
		t.Fatalf("identity not forwarded: %#v", orch.seenCtx.GetContext())
	}
}

func TestSessionInfoAcceptsBearerHeaderSoTheTokenStaysOutOfTheURL(t *testing.T) {
	orch := &fakeOrch{resp: okSummary()}
	r := getSession(t, tokenAuth(), orch, "/api/session",
		map[string]string{"Authorization": "Bearer good"})

	if r.Code != http.StatusOK {
		t.Fatalf("want 200 for bearer auth, got %d", r.Code)
	}
	if orch.seenMeta["granted_scopes"] != "vehicle.control" {
		t.Fatalf("bearer token was not resolved: %#v", orch.seenMeta)
	}
}

func TestSessionInfoNeverEchoesTheToken(t *testing.T) {
	var logs bytes.Buffer
	previous := log.Writer()
	log.SetOutput(&logs)
	defer log.SetOutput(previous)

	orch := &fakeOrch{resp: okSummary()}
	r := getSession(t, tokenAuth(), orch, "/api/session?token=good", nil)

	if strings.Contains(r.Body.String(), "good") {
		t.Fatalf("token leaked into the response: %s", r.Body.String())
	}
	if strings.Contains(logs.String(), "token=good") {
		t.Fatalf("token leaked into logs: %s", logs.String())
	}
}

func TestSessionInfoRejectsBadTokenOnlyWhenAuthIsRequired(t *testing.T) {
	a := tokenAuth()
	a.required = true
	orch := &fakeOrch{resp: okSummary()}

	r := getSession(t, a, orch, "/api/session?token=nope", nil)
	if r.Code != http.StatusUnauthorized {
		t.Fatalf("want 401 under AUTH_REQUIRED, got %d", r.Code)
	}
	if body := decodeBody(t, r); body["authenticated"] != false {
		t.Fatalf("401 body must say so plainly: %#v", body)
	}

	// 默认模式（AUTH_REQUIRED=false）仍匿名放行，逐字保持既有 WS 行为
	anon := getSession(t, tokenAuth(), orch, "/api/session?token=nope", nil)
	if anon.Code != http.StatusOK {
		t.Fatalf("anonymous fallback must stay, got %d", anon.Code)
	}
	if _, present := orch.seenMeta["granted_scopes"]; present {
		t.Fatalf("anonymous request must not carry forged scopes: %#v", orch.seenMeta)
	}
	if orch.seenMeta["authenticated"] == "true" {
		t.Fatal("anonymous fallback must not claim it authenticated")
	}
}

func TestSessionInfoBackendFailureIsUnknownNotUnauthorized(t *testing.T) {
	// 后端不可达**不是** token 失效：判错方向会让用户去重配一份完全正确的配置。
	orch := &fakeOrch{err: status.Error(codes.Unavailable, "orchestrator down")}
	r := getSession(t, tokenAuth(), orch, "/api/session?token=good", nil)

	if r.Code != http.StatusServiceUnavailable {
		t.Fatalf("want 503 for a dead backend, got %d", r.Code)
	}
	body := decodeBody(t, r)
	if body["summary_status"] != "partial" {
		t.Fatalf("must not fabricate a complete summary: %#v", body)
	}
	if _, present := body["capabilities"]; present {
		t.Fatalf("must not fabricate capabilities: %#v", body)
	}
}

func TestSessionInfoPassesThroughEveryContractField(t *testing.T) {
	orch := &fakeOrch{resp: okSummary()}
	body := decodeBody(t, getSession(t, tokenAuth(), orch, "/api/session?token=good", nil))

	for key, want := range map[string]any{
		"contract_version":     "ar05.1",
		"authenticated":        true,
		"user_id":              "u1",
		"vehicle_id":           "v1",
		"authorization_source": "token",
		"summary_status":       "complete",
	} {
		if body[key] != want {
			t.Fatalf("%s: want %v, got %v", key, want, body[key])
		}
	}
	caps, ok := body["capabilities"].([]any)
	if !ok || len(caps) != 2 {
		t.Fatalf("capabilities not passed through: %#v", body["capabilities"])
	}
	second, _ := caps[1].(map[string]any)
	if second["status"] != "unauthorized" || second["reason_code"] != "scope_missing" {
		t.Fatalf("capability status was rewritten: %#v", second)
	}
	// 空数组必须是 []，不是 null——「一个都没有」与「字段缺失」是两种语义
	if scopes, ok := body["granted_scopes"].([]any); !ok || len(scopes) != 1 {
		t.Fatalf("granted_scopes shape: %#v", body["granted_scopes"])
	}
}

func TestSessionInfoRejectsNonGet(t *testing.T) {
	orch := &fakeOrch{resp: okSummary()}
	request := httptest.NewRequest(http.MethodPost, "/api/session?token=good", nil)
	response := httptest.NewRecorder()
	handleSessionInfo(response, request, orch, tokenAuth())
	if response.Code != http.StatusMethodNotAllowed {
		t.Fatalf("want 405, got %d", response.Code)
	}
}
