// R3.1 会话鉴权最小闭环 · 层 1（用户/请求鉴权）：把 HMI WebSocket 的 ?token= 解析成会话身份
// {user_id, vehicle_id, granted_scopes}。全 env 门控、默认关（AUTH_REQUIRED=false）——命中 token
// 即用其身份+scope；未命中时 AUTH_REQUIRED 决定拒绝(true)还是匿名放行(false，逐字保持现状)。
// 网关对 granted_scopes 有唯一权威（剔除客户端伪造值，只按 token 注入）。
// 设计见 docs/design/2026-07-02-r3.1-session-auth.md。
package main

import (
	"os"
	"strings"
	"time"
)

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
	now            func() time.Time
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
	return authConfig{
		required:       strings.EqualFold(os.Getenv("AUTH_REQUIRED"), "true"),
		tokens:         parseAuthTokens(os.Getenv("AUTH_TOKENS")),
		defaultUserID:  getenv("AUTH_DEFAULT_USER_ID", "u1"),
		defaultVehicle: getenv("VEHICLE_ID", "v1"),
		e2eEnabled:     enabled,
		e2eSecret:      secret,
		e2eConfigErr:   configErr,
		now:            time.Now,
	}
}

// parseAuthTokens 解析静态 token 表。格式：条目用 ; 分隔，每条 token:user_id:vehicle_id:scope-csv
// （scope-csv 内部用 , 分隔，直接就是要注入 meta 的值）。空/畸形（不足 4 段）条目跳过。
func parseAuthTokens(raw string) map[string]identity {
	table := map[string]identity{}
	for _, entry := range strings.Split(raw, ";") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		// 只切前 3 个 ':'，第 4 段（scope-csv）保留其内部逗号
		parts := strings.SplitN(entry, ":", 4)
		if len(parts) < 4 {
			continue
		}
		token := strings.TrimSpace(parts[0])
		if token == "" {
			continue
		}
		table[token] = identity{
			userID:    strings.TrimSpace(parts[1]),
			vehicleID: strings.TrimSpace(parts[2]),
			scopes:    strings.TrimSpace(parts[3]),
		}
	}
	return table
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
