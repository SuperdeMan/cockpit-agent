package main

import "testing"

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
