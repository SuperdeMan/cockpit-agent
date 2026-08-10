// Package deployprofile 是 B3 部署形态闸的 Go 网关侧实现（方案
// docs/design/2026-08-10-b3-deploy-profile-fail-closed.md §2.3）。
//
// 三档语义与 Python 侧 runtime/profile.py 完全一致：
//
//	dev （默认，含 DEPLOY_PROFILE 未设）零校验，逐字保持现状；
//	demo 软校验，启动打一段聚合 warning，不阻断；
//	prod 硬校验，任一项不满足即 os.Exit(78)（sysexits.h EX_CONFIG）。
//
// # 与 Python 侧的分工：只校验网关自己消费的键
//
// Python 侧校验整张强制表（它的服务持有全部这些键）。网关只校验它**真的会读**的那几个：
// AUTH_REQUIRED / AUTH_TOKENS / CLOUD_CHANNEL_TOKEN(S) / GRPC_TLS。刻意不把 Postgres、
// Grafana 口令灌进网关容器换一个「校验得更全」的读数——那是把凭据铺得更广换一份重复。
// 整栈层面不漏：任一项不满足时，持有该键的 Python 服务会拒绝启动。
//
// # 为什么每项要按 role 声明适用范围
//
// edge-gateway 的环境里没有 CLOUD_CHANNEL_TOKENS，cloud-gateway 的环境里没有 AUTH_TOKENS。
// 如果两个二进制共用一张不分角色的表，缺键的那一边会被判成违规（假红）；反过来「键不在
// 我的环境里就跳过」又是**按缺席 fail-open**——那正是本批要消灭的形态。所以每项显式声明
// 它属于哪个角色，缺席即违规。
//
// # 为什么判定要复刻消费方的解析
//
// AUTH_REQUIRED 由 auth.go 用 strings.EqualFold(v,"true") 读——AUTH_REQUIRED=1 对它就是关；
// GRPC_TLS 由 tlscfg.Enabled() 用 switch 精确匹配读——大写 ON 对它也是关。一个「看起来是真」
// 的通用真值判断会在这两处报绿，而开关其实没打开。
package deployprofile

import (
	"fmt"
	"log"
	"os"
	"strings"
)

// 档位。
const (
	Dev  = "dev"
	Demo = "demo"
	Prod = "prod"
)

// ExitConfig 是 sysexits.h 的 EX_CONFIG——「配置有误」，与「进程崩了」区分开。
const ExitConfig = 78

// ProfileEnv 是档位键名。
const ProfileEnv = "DEPLOY_PROFILE"

// Role 标识调用方是哪个网关（决定强制表里哪些项适用）。
type Role string

const (
	RoleEdgeGateway  Role = "edge-gateway"
	RoleCloudGateway Role = "cloud-gateway"
)

// sampleTokens 是 .env.example 里成文的示例 token 字面。它们是公开示例、不是密钥，
// 可以出现在报错里——配错的人需要知道自己抄了示例值。
var sampleTokens = map[string]bool{
	"demo-u1":         true,
	"demo-channel-v1": true,
}

// secretKeys 的值是凭据，任何情况下都不进日志（CLAUDE.md 红线），只回显形状。
var secretKeys = map[string]bool{
	"AUTH_TOKENS":          true,
	"CLOUD_CHANNEL_TOKEN":  true,
	"CLOUD_CHANNEL_TOKENS": true,
}

// Env 是一次 env 查询（值, 是否存在）。用它而不是 os.Getenv，是为了把「未设」与
// 「设成空串」区分开——报错里这两者对排查是不同的信息。
type Env func(key string) (string, bool)

// OSEnv 是进程环境。
func OSEnv(key string) (string, bool) { return os.LookupEnv(key) }

// Violation 是强制表的一项不满足。Actual 已脱敏。
type Violation struct {
	Idx      int
	Key      string
	Actual   string
	Expected string
	Why      string
}

func (v Violation) String() string {
	return fmt.Sprintf("  [%2d] %s = %s\n       要求：%s\n       原因：%s",
		v.Idx, v.Key, v.Actual, v.Expected, v.Why)
}

type check struct {
	idx       int
	key       string
	expected  string
	why       string
	roles     []Role
	satisfied func(Env) bool
}

func (c check) appliesTo(role Role) bool {
	for _, r := range c.roles {
		if r == role {
			return true
		}
	}
	return false
}

func get(env Env, key string) string {
	v, _ := env(key)
	return v
}

// ── 逐项复刻消费方的解析 ───────────────────────────────────────────────

// authRequiredOn 复刻 gateway/edge/auth.go::loadAuthConfig 与 gateway/cloud/main.go。
func authRequiredOn(env Env) bool {
	return strings.EqualFold(strings.TrimSpace(get(env, "AUTH_REQUIRED")), "true")
}

// grpcTLSOn 复刻 gateway/tlscfg::Enabled（switch 精确匹配，不做 lower）。
func grpcTLSOn(env Env) bool {
	switch get(env, "GRPC_TLS") {
	case "on", "true", "1", "yes":
		return true
	}
	return false
}

// authTokenIDs 复刻 gateway/edge/auth.go::parseAuthTokens 的取 token id 部分：
// 条目用 ; 分隔，每条 token:user_id:vehicle_id:scope-csv，不足 4 段的条目被消费方跳过。
func authTokenIDs(raw string) []string {
	var ids []string
	for _, entry := range strings.Split(raw, ";") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		parts := strings.SplitN(entry, ":", 4)
		if len(parts) < 4 {
			continue
		}
		if tok := strings.TrimSpace(parts[0]); tok != "" {
			ids = append(ids, tok)
		}
	}
	return ids
}

// channelTokens 复刻 gateway/cloud/main.go::parseChannelTokens。
func channelTokens(raw string) map[string]bool {
	set := map[string]bool{}
	for _, t := range strings.Split(raw, ",") {
		if t = strings.TrimSpace(t); t != "" {
			set[t] = true
		}
	}
	return set
}

func authTokensOK(env Env) bool {
	ids := authTokenIDs(get(env, "AUTH_TOKENS"))
	if len(ids) == 0 {
		return false
	}
	for _, id := range ids {
		if sampleTokens[id] {
			return false
		}
	}
	return true
}

// channelIdentityOK：层 2（edge→cloud）通道身份可用且非示例值。
//
// channelTokenAllowed 在 AUTH_REQUIRED=false 时恒放行；翻 true 之后若允许集为空则**恒拒绝**
// （端云直接断链）。两种都是 prod 不该出现的形态，一条判据同时挡住。
func channelIdentityOK(env Env) bool {
	token := strings.TrimSpace(get(env, "CLOUD_CHANNEL_TOKEN"))
	allowed := channelTokens(get(env, "CLOUD_CHANNEL_TOKENS"))
	if token == "" || len(allowed) == 0 || !allowed[token] {
		return false
	}
	if sampleTokens[token] {
		return false
	}
	for t := range allowed {
		if sampleTokens[t] {
			return false
		}
	}
	return true
}

// checks 的 idx 与 Python 侧 runtime/profile.py::CHECKS 同号，便于交叉引用。
var checks = []check{
	{1, "AUTH_REQUIRED", "true（字面）",
		"关着=无 token 也能连，回落默认身份 u1；量产不允许匿名会话。",
		[]Role{RoleEdgeGateway, RoleCloudGateway}, authRequiredOn},
	{3, "GRPC_TLS", "on / true / 1 / yes（小写，Go 侧精确匹配）",
		"关着=服务间 gRPC 明文且互不校验身份；量产必须 mTLS。",
		[]Role{RoleEdgeGateway, RoleCloudGateway}, grpcTLSOn},
	{4, "AUTH_TOKENS", "非空，且不含 .env.example 示例 token",
		"AUTH_REQUIRED=true 但 token 表为空=谁也连不上；抄示例值=公开凭据。",
		[]Role{RoleEdgeGateway}, authTokensOK},
	{5, "CLOUD_CHANNEL_TOKEN", "非空、∈ CLOUD_CHANNEL_TOKENS、且非示例值",
		"层 2 端云通道的身份面：空允许集会让端云恒断链，示例值等于公开凭据。",
		[]Role{RoleCloudGateway}, channelIdentityOK},
}

// ── 展示与脱敏 ─────────────────────────────────────────────────────────

func show(env Env, key string) string {
	raw, ok := env(key)
	if !ok {
		return "<未设>"
	}
	if raw == "" {
		return "<空>"
	}
	if !secretKeys[key] {
		return fmt.Sprintf("%q", raw)
	}
	var hit []string
	for s := range sampleTokens {
		if strings.Contains(raw, s) {
			hit = append(hit, s)
		}
	}
	if len(hit) > 0 {
		return fmt.Sprintf("<含 .env.example 示例 token：%s>", strings.Join(hit, "、"))
	}
	return fmt.Sprintf("<已设，%d 字符>", len(raw))
}

// ── 判定与执行 ─────────────────────────────────────────────────────────

// Resolve 读 DEPLOY_PROFILE。未设/空 → dev；未知值 → error（**不回落 dev**：拼错档位
// 却按零校验跑，正是本闸要消灭的形态）。
func Resolve(env Env) (string, error) {
	raw := strings.ToLower(strings.TrimSpace(get(env, ProfileEnv)))
	switch raw {
	case "":
		return Dev, nil
	case Dev, Demo, Prod:
		return raw, nil
	}
	original, _ := env(ProfileEnv)
	return "", fmt.Errorf("%s=%q 不是合法档位；可选：dev/demo/prod（未设=dev）。"+
		"**不回落 dev**：拼错档位却按零校验跑，正是本闸要消灭的形态", ProfileEnv, original)
}

// Audit 按角色适用的强制表逐项判定，返回不满足项（与当前档位无关，档位只决定怎么处置）。
func Audit(env Env, role Role) []Violation {
	var out []Violation
	for _, c := range checks {
		if !c.appliesTo(role) || c.satisfied(env) {
			continue
		}
		out = append(out, Violation{c.idx, c.key, show(env, c.key), c.expected, c.why})
	}
	return out
}

// FormatReport 渲染人可读的报告。
func FormatReport(profile string, role Role, vs []Violation) string {
	var b strings.Builder
	fmt.Fprintf(&b, "DEPLOY_PROFILE=%s（%s）：%d 项生产配置校验未通过\n", profile, role, len(vs))
	for _, v := range vs {
		b.WriteString(v.String())
		b.WriteString("\n")
	}
	b.WriteString("方案与判据：docs/design/2026-08-10-b3-deploy-profile-fail-closed.md §2.2；" +
		"键说明见 .env.example / docs/conventions.md §6。")
	return b.String()
}

// Enforce 是进程入口用的门面：按档位处置，prod 不满足即退出。
func Enforce(role Role) {
	enforce(OSEnv, role, func(code int) { os.Exit(code) })
}

// enforce 把「怎么退出」注入进来，好让测试证明它**真的**退出了而不是只打了日志。
func enforce(env Env, role Role, exit func(int)) string {
	profile, err := Resolve(env)
	if err != nil {
		emit(err.Error())
		exit(ExitConfig)
		return ""
	}
	if profile == Dev {
		return Dev
	}
	vs := Audit(env, role)
	if len(vs) == 0 {
		log.Printf("[profile] DEPLOY_PROFILE=%s（%s）：生产配置校验全部通过", profile, role)
		return profile
	}
	report := FormatReport(profile, role, vs)
	if profile == Prod {
		emit(report + "\nprod 档拒绝启动（fail-closed）。")
		exit(ExitConfig)
		return profile
	}
	emit(report + "\ndemo 档只告警不阻断；prod 档下这些项会拒绝启动。")
	return profile
}

func emit(text string) {
	banner := strings.Repeat("!", 72)
	fmt.Fprintf(os.Stderr, "\n%s\n%s\n%s\n\n", banner, text, banner)
	log.Printf("[profile] %s", text)
}
