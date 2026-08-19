// R3.1 会话鉴权最小闭环 · 层 1（用户/请求鉴权）：把 HMI WebSocket 的 ?token= 解析成会话身份
// {user_id, vehicle_id, granted_scopes}。全 env 门控、默认关（AUTH_REQUIRED=false）——命中 token
// 即用其身份+scope；未命中时 AUTH_REQUIRED 决定拒绝(true)还是匿名放行(false，逐字保持现状)。
// 网关对 granted_scopes 有唯一权威（剔除客户端伪造值，只按 token 注入）。
// 设计见 docs/design/2026-07-02-r3.1-session-auth.md。
package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

// errAuthTokensConfig：AUTH_TOKENS 里有畸形条目。**消费方拒绝启动**（fail closed）。
//
// 为什么不能像原来那样静默跳过：2026-08-19 云端实测过它的代价——一条
// `<token>:v1:<scope串>:<scope串>` 的错配（漏写了 user_id 段）被逐字接受，
// 于是 `parts[1]` 这个 **user_id 位被解析成 `v1`**，而全部长期记忆在 `u1` 名下。
// 后果是**权限全通、功能全正常，只有记忆一条都召不回**——因为第 4 段 scopes 恰好还在
// 正确的位置上。这种「看起来全绿、实际身份错了」的形态，靠人读配置是发现不了的。
//
// 报错**绝不包含 token 值**（只给序号与形状），日志里不该出现凭证。
var errAuthTokensConfig = errors.New("invalid AUTH_TOKENS")

// identity 是一个 token 解析出的会话身份 + 授权。
type identity struct {
	userID    string
	vehicleID string
	scopes    string // 逗号分隔，直接注入 HandleRequest.meta["granted_scopes"]
}

// authConfig 汇总层 1 鉴权配置（进程启动时装配一次）。
type authConfig struct {
	required       bool
	tokens         map[string]identity
	defaultUserID  string
	defaultVehicle string
	e2eEnabled     bool
	e2eSecret      []byte
	e2eConfigErr   error
	// tokensConfigErr：AUTH_TOKENS 畸形。**由 main 拒绝启动**——同 e2eConfigErr 的形态
	// （配置错误存进 struct、由消费点决定怎么 fail），不在装配函数里 panic。
	tokensConfigErr error
	now             func() time.Time
}

// loadAuthConfig 从环境变量装配层 1 鉴权配置。
func loadAuthConfig() authConfig {
	enabled := strings.EqualFold(os.Getenv("E2E_IDENTITY_ENABLED"), "true")
	var secret []byte
	var configErr error
	if enabled {
		rawSecret := os.Getenv("E2E_IDENTITY_SECRET")
		if rawSecret == "" {
			configErr = errE2EIdentityConfig
		} else if decoded, err := decodeE2ESecret(rawSecret); err != nil {
			configErr = errE2EIdentityConfig
		} else {
			secret = decoded
		}
	}
	tokens, tokensErr := parseAuthTokens(os.Getenv("AUTH_TOKENS"))
	return authConfig{
		required:        strings.EqualFold(os.Getenv("AUTH_REQUIRED"), "true"),
		tokens:          tokens,
		tokensConfigErr: tokensErr,
		defaultUserID:   getenv("AUTH_DEFAULT_USER_ID", "u1"),
		defaultVehicle:  getenv("VEHICLE_ID", "v1"),
		e2eEnabled:      enabled,
		e2eSecret:       secret,
		e2eConfigErr:    configErr,
		now:             time.Now,
	}
}

// parseAuthTokens 解析静态 token 表。格式：条目用 ; 分隔，每条 token:user_id:vehicle_id:scope-csv
// （scope-csv 内部用 , 分隔，直接就是要注入 meta 的值）。空条目跳过。
//
// **畸形条目返回 error 而不是静默跳过**（见 errAuthTokensConfig 上的实测代价）。
// 三条形状判据，各自对应一种真实发生过或会静默错身份的写法：
//  1. 不足 4 段 —— 整条被忽略，token 表悄悄变空（本地 `.env` 当时就是这样）；
//  2. token 段为空 —— 无法索引；
//  3. user_id / vehicle_id 段含逗号 —— 那是 scope 串的特征，**几乎必然是漏写了一段
//     导致后面的字段整体前移**（云端当时就是这样）。
//
// 刻意**不**校验 user_id/vehicle_id 非空：`resolve` 有意支持空值回退进程默认，
// 那是既有设计不是缺陷。判据只挡「能机器判定的错位」，不替人裁定取值。
func parseAuthTokens(raw string) (map[string]identity, error) {
	table := map[string]identity{}
	var bad []string
	for i, entry := range strings.Split(raw, ";") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		// 只切前 3 个 ':'，第 4 段（scope-csv）保留其内部逗号
		parts := strings.SplitN(entry, ":", 4)
		if len(parts) < 4 {
			bad = append(bad, fmt.Sprintf(
				"第 %d 条只有 %d 段，需要 token:user_id:vehicle_id:scope-csv 四段", i+1, len(parts)))
			continue
		}
		token := strings.TrimSpace(parts[0])
		if token == "" {
			bad = append(bad, fmt.Sprintf("第 %d 条的 token 段为空", i+1))
			continue
		}
		userID := strings.TrimSpace(parts[1])
		vehicleID := strings.TrimSpace(parts[2])
		if strings.Contains(userID, ",") || strings.Contains(vehicleID, ",") {
			bad = append(bad, fmt.Sprintf(
				"第 %d 条的 user_id/vehicle_id 段含逗号——那是 scope 串的特征，"+
					"多半漏写了一段导致字段整体前移", i+1))
			continue
		}
		table[token] = identity{
			userID:    userID,
			vehicleID: vehicleID,
			scopes:    strings.TrimSpace(parts[3]),
		}
	}
	if len(bad) > 0 {
		return table, fmt.Errorf("%w: %s", errAuthTokensConfig, strings.Join(bad, "; "))
	}
	return table, nil
}

// resolve 把 WS 查询串里的 token 解析成会话身份，返回 (身份, 是否命中有效 token)。
// 命中时空 user_id/vehicle_id 回退进程默认（PoC 单车/单用户）。
func (a authConfig) resolve(token string) (identity, bool) {
	if token == "" {
		return identity{}, false
	}
	id, ok := a.tokens[token]
	if !ok {
		return identity{}, false
	}
	if id.userID == "" {
		id.userID = a.defaultUserID
	}
	if id.vehicleID == "" {
		id.vehicleID = a.defaultVehicle
	}
	return id, true
}

// resolveSession preserves normal auth priority, then optionally recognizes the
// runner-only signed prefix. hardReject means the prefix was claimed while the
// gate was enabled but could not be verified, so anonymous fallback is forbidden.
func (a authConfig) resolveSession(
	token string,
) (identity, *e2eIdentityClaims, bool, bool) {
	if id, ok := a.resolve(token); ok {
		return id, nil, true, false
	}
	if !a.e2eEnabled || !strings.HasPrefix(token, e2eIdentityPrefix) {
		return identity{}, nil, false, false
	}
	if a.e2eConfigErr != nil || len(a.e2eSecret) != 32 {
		return identity{}, nil, false, true
	}
	clock := a.now
	if clock == nil {
		clock = time.Now
	}
	claims, err := verifyE2EIdentity(token, a.e2eSecret, clock())
	if err != nil {
		return identity{}, nil, false, true
	}
	id := identity{
		userID:    claims.UserID,
		vehicleID: claims.VehicleID,
		scopes:    strings.Join(claims.Scopes, ","),
	}
	return id, &claims, true, false
}

// anonymous 返回匿名回退身份（AUTH_REQUIRED=false 且无有效 token 时）：user_id=默认、
// vehicle_id=默认、不带 scope（下游按 PERMISSIONS_FAIL_OPEN 处理），与今天逐字等价。
func (a authConfig) anonymous() identity {
	return identity{userID: a.defaultUserID, vehicleID: a.defaultVehicle}
}

// stampScopes 让网关对 granted_scopes 保持唯一权威：先剔除客户端可能伪造的值，
// 再按 token 解析结果注入（scope 为空=匿名，剥离后交下游 fail-open 兜底）。
func stampScopes(meta map[string]string, scopes string) map[string]string {
	if meta != nil {
		delete(meta, "granted_scopes")
	}
	if scopes == "" {
		return meta
	}
	if meta == nil {
		meta = map[string]string{}
	}
	meta["granted_scopes"] = scopes
	return meta
}
