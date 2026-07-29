package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"time"
)

const (
	e2eIdentityPrefix       = "e2e.v1."
	e2eIdentityMaxTTL       = int64(1920)
	e2eIdentityFutureLeeway = int64(5)
)

var (
	e2eSafeClaimPattern     = regexp.MustCompile(`^[A-Za-z0-9._:-]+$`)
	e2eRunPattern           = regexp.MustCompile(`^e2e-[A-Za-z0-9._:-]+$`)
	e2eSessionNumberPattern = regexp.MustCompile(`^[1-9][0-9]*$`)
	errE2EIdentityConfig    = errors.New("invalid E2E identity configuration")
)

type e2eIdentityClaims struct {
	RunID     string   `json:"run_id"`
	UserID    string   `json:"user_id"`
	VehicleID string   `json:"vehicle_id"`
	Scopes    []string `json:"scopes"`
	IssuedAt  int64    `json:"iat"`
	ExpiresAt int64    `json:"exp"`
}

func validateE2ESessionID(sessionID string, userID string) error {
	prefix := userID + "-session-"
	if userID == "" || !strings.HasPrefix(sessionID, prefix) {
		return errors.New("invalid signed E2E session")
	}
	number := strings.TrimPrefix(sessionID, prefix)
	if !e2eSessionNumberPattern.MatchString(number) {
		return errors.New("invalid signed E2E session")
	}
	return nil
}

func decodeRawBase64URL(value string) ([]byte, error) {
	if value == "" || strings.Contains(value, "=") {
		return nil, errors.New("malformed base64url")
	}
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil || base64.RawURLEncoding.EncodeToString(decoded) != value {
		return nil, errors.New("malformed base64url")
	}
	return decoded, nil
}

func decodeE2ESecret(value string) ([]byte, error) {
	secret, err := decodeRawBase64URL(value)
	if err != nil {
		return nil, errors.New("invalid E2E identity secret")
	}
	if len(secret) != 32 {
		return nil, errors.New("E2E identity secret must be exactly 32 bytes")
	}
	return secret, nil
}

func validateE2EClaims(claims e2eIdentityClaims, now int64) error {
	if !e2eRunPattern.MatchString(claims.RunID) {
		return errors.New("invalid E2E run namespace")
	}
	if !e2eSafeClaimPattern.MatchString(claims.UserID) ||
		!strings.HasPrefix(claims.UserID, claims.RunID+"-") ||
		len(claims.UserID) == len(claims.RunID)+1 {
		return errors.New("invalid E2E user namespace")
	}
	if !e2eSafeClaimPattern.MatchString(claims.VehicleID) {
		return errors.New("invalid E2E vehicle identity")
	}
	if len(claims.Scopes) == 0 {
		return errors.New("invalid E2E scopes")
	}
	for _, scope := range claims.Scopes {
		if !e2eSafeClaimPattern.MatchString(scope) {
			return errors.New("invalid E2E scopes")
		}
	}
	ttl := claims.ExpiresAt - claims.IssuedAt
	if ttl <= 0 || ttl > e2eIdentityMaxTTL {
		return errors.New("invalid E2E identity TTL")
	}
	if claims.IssuedAt > now+e2eIdentityFutureLeeway {
		return errors.New("E2E identity issued in the future")
	}
	if now >= claims.ExpiresAt {
		return errors.New("E2E identity expired")
	}
	return nil
}

func verifyE2EIdentity(token string, secret []byte, now time.Time) (e2eIdentityClaims, error) {
	var claims e2eIdentityClaims
	if len(secret) != 32 {
		return claims, errors.New("invalid E2E identity configuration")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 4 || parts[0] != "e2e" || parts[1] != "v1" {
		return claims, errors.New("invalid E2E identity version")
	}
	payload, err := decodeRawBase64URL(parts[2])
	if err != nil {
		return claims, errors.New("invalid E2E identity payload")
	}
	signature, err := decodeRawBase64URL(parts[3])
	if err != nil || len(signature) != sha256.Size {
		return claims, errors.New("invalid E2E identity signature")
	}
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(e2eIdentityPrefix + parts[2]))
	// hmac.Equal performs a constant-time comparison for equal-length inputs.
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return claims, errors.New("invalid E2E identity signature")
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(payload, &fields); err != nil || len(fields) != 6 {
		return claims, errors.New("invalid E2E identity claims")
	}
	for _, key := range []string{"run_id", "user_id", "vehicle_id", "scopes", "iat", "exp"} {
		if _, ok := fields[key]; !ok {
			return claims, errors.New("invalid E2E identity claims")
		}
	}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return claims, errors.New("invalid E2E identity claims")
	}
	canonical, err := json.Marshal(claims)
	if err != nil || !bytes.Equal(canonical, payload) {
		return claims, errors.New("non-canonical E2E identity claims")
	}
	if err := validateE2EClaims(claims, now.Unix()); err != nil {
		return e2eIdentityClaims{}, err
	}
	return claims, nil
}
