package deployprofile

import (
	"testing"
)

// envOf 把一张表变成 Env；表里没有的键 = 未设。
func envOf(m map[string]string) Env {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

// okEdge / okCloud 是**逐项满足**各自角色强制表的 env；所有突变都从它们派生，只改一个键。
func okEdge() map[string]string {
	return map[string]string{
		ProfileEnv:      Prod,
		"AUTH_REQUIRED": "true",
		"GRPC_TLS":      "on",
		"AUTH_TOKENS":   "prod-tok-1:u1:v1:vehicle.control,media.control",
	}
}

func okCloud() map[string]string {
	return map[string]string{
		ProfileEnv:             Prod,
		"AUTH_REQUIRED":        "true",
		"GRPC_TLS":             "on",
		"CLOUD_CHANNEL_TOKEN":  "prod-channel-1",
		"CLOUD_CHANNEL_TOKENS": "prod-channel-1,prod-channel-2",
	}
}

func mutate(base map[string]string, patch map[string]*string) map[string]string {
	out := map[string]string{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range patch {
		if v == nil {
			delete(out, k)
		} else {
			out[k] = *v
		}
	}
	return out
}

func sp(s string) *string { return &s }

func TestResolveProfile(t *testing.T) {
	cases := []struct{ raw, want string }{
		{"", Dev}, {"dev", Dev}, {"DEV", Dev}, {" Prod ", Prod}, {"demo", Demo},
	}
	for _, c := range cases {
		got, err := Resolve(envOf(map[string]string{ProfileEnv: c.raw}))
		if err != nil || got != c.want {
			t.Fatalf("Resolve(%q) = (%q, %v), want %q", c.raw, got, err, c.want)
		}
	}
	if got, err := Resolve(envOf(map[string]string{})); err != nil || got != Dev {
		t.Fatalf("未设 DEPLOY_PROFILE 应为 dev，得 (%q,%v)", got, err)
	}
}

// 未知档位不静默回落 dev——拼错 profile 却按零校验跑正是本闸要消灭的形态。
func TestUnknownProfileRefusesToFallBackToDev(t *testing.T) {
	for _, raw := range []string{"production", "stage", "prod1", "true"} {
		if _, err := Resolve(envOf(map[string]string{ProfileEnv: raw})); err == nil {
			t.Fatalf("Resolve(%q) 应报错，实际放行", raw)
		}
	}
	var code int
	enforce(envOf(map[string]string{ProfileEnv: "production"}), RoleEdgeGateway,
		func(c int) { code = c })
	if code != ExitConfig {
		t.Fatalf("未知档位应 exit %d，得 %d", ExitConfig, code)
	}
}

// dev 档零校验：本方案的硬约束。
func TestDevProfileChecksNothing(t *testing.T) {
	envs := []map[string]string{
		{},
		{ProfileEnv: "dev"},
		{ProfileEnv: "dev", "AUTH_REQUIRED": "false", "GRPC_TLS": "off"},
	}
	for _, e := range envs {
		code := -1
		got := enforce(envOf(e), RoleEdgeGateway, func(c int) { code = c })
		if got != Dev || code != -1 {
			t.Fatalf("dev 档不应校验/退出，得 profile=%q code=%d", got, code)
		}
	}
}

// 对照组：合规 env 在 prod 档零 violation，证明没修过头。
func TestProdCompliantEnvPasses(t *testing.T) {
	for _, tc := range []struct {
		role Role
		env  map[string]string
	}{
		{RoleEdgeGateway, okEdge()},
		{RoleCloudGateway, okCloud()},
	} {
		if vs := Audit(envOf(tc.env), tc.role); len(vs) != 0 {
			t.Fatalf("%s 合规 env 应零 violation，得 %v", tc.role, vs)
		}
		code := -1
		if got := enforce(envOf(tc.env), tc.role, func(c int) { code = c }); got != Prod || code != -1 {
			t.Fatalf("%s 合规 env 不应退出，得 profile=%q code=%d", tc.role, got, code)
		}
	}
}

// 单项不满足 → 拒绝启动（验收判据 §4.2 的矩阵，Go 侧子集）。
func TestProdSingleFaultRefusesToStart(t *testing.T) {
	cases := []struct {
		name  string
		role  Role
		base  map[string]string
		idx   int
		patch map[string]*string
	}{
		{"edge/auth_required_off", RoleEdgeGateway, okEdge(), 1,
			map[string]*string{"AUTH_REQUIRED": sp("false")}},
		{"edge/auth_required_unset", RoleEdgeGateway, okEdge(), 1,
			map[string]*string{"AUTH_REQUIRED": nil}},
		// 「看起来是真」的值在 auth.go 的 EqualFold 眼里其实是关的。
		{"edge/auth_required_truthy_but_not_true", RoleEdgeGateway, okEdge(), 1,
			map[string]*string{"AUTH_REQUIRED": sp("1")}},
		{"edge/grpc_tls_off", RoleEdgeGateway, okEdge(), 3,
			map[string]*string{"GRPC_TLS": sp("off")}},
		// tlscfg.Enabled 是 switch 精确匹配、不 lower——大写 ON 那边读成关。
		{"edge/grpc_tls_uppercase", RoleEdgeGateway, okEdge(), 3,
			map[string]*string{"GRPC_TLS": sp("ON")}},
		{"edge/auth_tokens_empty", RoleEdgeGateway, okEdge(), 4,
			map[string]*string{"AUTH_TOKENS": sp("")}},
		{"edge/auth_tokens_sample", RoleEdgeGateway, okEdge(), 4,
			map[string]*string{"AUTH_TOKENS": sp("demo-u1:u1:v1:vehicle.control")}},
		// 畸形条目被 parseAuthTokens 跳过 ⇒ 实际 token 表仍是空的，这里也不能算数。
		{"edge/auth_tokens_malformed_only", RoleEdgeGateway, okEdge(), 4,
			map[string]*string{"AUTH_TOKENS": sp("justatoken")}},
		{"cloud/channel_token_unset", RoleCloudGateway, okCloud(), 5,
			map[string]*string{"CLOUD_CHANNEL_TOKEN": nil}},
		{"cloud/channel_allowlist_empty", RoleCloudGateway, okCloud(), 5,
			map[string]*string{"CLOUD_CHANNEL_TOKENS": sp("")}},
		{"cloud/channel_token_not_in_allowlist", RoleCloudGateway, okCloud(), 5,
			map[string]*string{"CLOUD_CHANNEL_TOKEN": sp("other")}},
		{"cloud/channel_token_sample", RoleCloudGateway, okCloud(), 5,
			map[string]*string{"CLOUD_CHANNEL_TOKEN": sp("demo-channel-v1"),
				"CLOUD_CHANNEL_TOKENS": sp("demo-channel-v1")}},
	}
	seen := map[int]bool{}
	for _, tc := range cases {
		env := envOf(mutate(tc.base, tc.patch))
		vs := Audit(env, tc.role)
		if len(vs) != 1 || vs[0].Idx != tc.idx {
			t.Fatalf("%s: 应只破第 %d 项，得 %v", tc.name, tc.idx, vs)
		}
		code := -1
		enforce(env, tc.role, func(c int) { code = c })
		if code != ExitConfig {
			t.Fatalf("%s: prod 档应 exit %d，得 %d", tc.name, ExitConfig, code)
		}
		seen[tc.idx] = true
	}
	// 强制表里不许有「谁也测不到」的项——加一项就得配一条突变。
	for _, c := range checks {
		if !seen[c.idx] {
			t.Fatalf("强制表第 %d 项（%s）没有任何突变用例覆盖", c.idx, c.key)
		}
	}
}

// 角色隔离：edge 不该因为「自己环境里本就没有 CLOUD_CHANNEL_TOKENS」而被判违规，
// 反过来也不许「键不在我环境里就跳过」——那是按缺席 fail-open。
func TestRoleScopingIsExplicit(t *testing.T) {
	if vs := Audit(envOf(okEdge()), RoleEdgeGateway); len(vs) != 0 {
		t.Fatalf("edge 合规 env 不该被 cloud 专属项判红：%v", vs)
	}
	if vs := Audit(envOf(okCloud()), RoleCloudGateway); len(vs) != 0 {
		t.Fatalf("cloud 合规 env 不该被 edge 专属项判红：%v", vs)
	}
	// 反向：把 cloud 的 env 拿去按 edge 角色判，缺的 AUTH_TOKENS 必须判红（不许跳过）。
	if vs := Audit(envOf(okCloud()), RoleEdgeGateway); len(vs) != 1 || vs[0].Idx != 4 {
		t.Fatalf("缺 AUTH_TOKENS 应判第 4 项，得 %v", vs)
	}
}

// demo 档只告警不阻断。
func TestDemoWarnsButDoesNotExit(t *testing.T) {
	env := mutate(okEdge(), map[string]*string{
		ProfileEnv: sp(Demo), "AUTH_REQUIRED": sp("false")})
	code := -1
	if got := enforce(envOf(env), RoleEdgeGateway, func(c int) { code = c }); got != Demo {
		t.Fatalf("demo 档应返回 demo，得 %q", got)
	}
	if code != -1 {
		t.Fatalf("demo 档不应退出，得 code=%d", code)
	}
}

// 密钥/token 不进日志（CLAUDE.md 红线）——只回显形状。
func TestShowRedactsCredentials(t *testing.T) {
	env := envOf(map[string]string{
		"AUTH_TOKENS": "very-secret-session-token:u1:v1:vehicle.control",
		"GRPC_TLS":    "off",
	})
	if got := show(env, "AUTH_TOKENS"); got != "<已设，47 字符>" {
		t.Fatalf("AUTH_TOKENS 不该回显原值，得 %q", got)
	}
	if got := show(env, "GRPC_TLS"); got != `"off"` {
		t.Fatalf("非凭据键应回显原值，得 %q", got)
	}
	if got := show(env, "AUTH_REQUIRED"); got != "<未设>" {
		t.Fatalf("未设应显示 <未设>，得 %q", got)
	}
	// 示例值是 .env.example 里的公开值——要指名道姓，配错的人需要看到自己抄了它。
	sample := envOf(map[string]string{"AUTH_TOKENS": "demo-u1:u1:v1:vehicle.control"})
	if got := show(sample, "AUTH_TOKENS"); got != "<含 .env.example 示例 token：demo-u1>" {
		t.Fatalf("示例 token 应被指名，得 %q", got)
	}
}
