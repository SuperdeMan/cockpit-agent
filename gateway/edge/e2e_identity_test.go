package main

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestE2EIdentityVerifierModuleExists(t *testing.T) {
	if _, err := os.Stat("e2e_identity.go"); err != nil {
		t.Fatalf("E2E identity verifier module is missing: %v", err)
	}
}

type identityFixture struct {
	Secret  string `json:"secret_b64url"`
	Now     int64  `json:"now"`
	SessionVectors []struct {
		Name      string `json:"name"`
		UserID    string `json:"user_id"`
		SessionID string `json:"session_id"`
		Valid     bool   `json:"valid"`
	} `json:"session_vectors"`
	Vectors []struct {
		Name   string            `json:"name"`
		Valid  bool              `json:"valid"`
		Token  string            `json:"token"`
		Claims e2eIdentityClaims `json:"claims"`
	} `json:"vectors"`
}

func TestValidateE2ESessionIDUsesSharedVectors(t *testing.T) {
	fixture := loadIdentityFixture(t)
	if len(fixture.SessionVectors) == 0 {
		t.Fatal("shared session vectors are missing")
	}
	for _, vector := range fixture.SessionVectors {
		t.Run(vector.Name, func(t *testing.T) {
			err := validateE2ESessionID(vector.SessionID, vector.UserID)
			if vector.Valid && err != nil {
				t.Fatalf("valid helper session rejected: %v", err)
			}
			if !vector.Valid && err == nil {
				t.Fatal("cross-owner or non-canonical session accepted")
			}
		})
	}
}

func loadIdentityFixture(t *testing.T) identityFixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "test", "fixtures", "e2e_identity_vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture identityFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestVerifyE2EIdentityUsesSharedVectors(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, err := decodeE2ESecret(fixture.Secret)
	if err != nil {
		t.Fatal(err)
	}
	for _, vector := range fixture.Vectors {
		t.Run(vector.Name, func(t *testing.T) {
			claims, err := verifyE2EIdentity(
				vector.Token,
				secret,
				time.Unix(fixture.Now, 0),
			)
			if vector.Valid {
				if err != nil {
					t.Fatalf("valid vector rejected: %v", err)
				}
				if claims.RunID != vector.Claims.RunID ||
					claims.UserID != vector.Claims.UserID ||
					claims.VehicleID != vector.Claims.VehicleID ||
					claims.IssuedAt != vector.Claims.IssuedAt ||
					claims.ExpiresAt != vector.Claims.ExpiresAt ||
					len(claims.Scopes) != len(vector.Claims.Scopes) {
					t.Fatalf("claims mismatch: %#v != %#v", claims, vector.Claims)
				}
				return
			}
			if err == nil {
				t.Fatalf("invalid vector %s was accepted", vector.Name)
			}
		})
	}
}

func TestVerifyE2EIdentityReconnectBoundary(t *testing.T) {
	fixture := loadIdentityFixture(t)
	secret, _ := decodeE2ESecret(fixture.Secret)
	var token string
	for _, vector := range fixture.Vectors {
		if vector.Name == "ttl_1920" {
			token = vector.Token
		}
	}
	if _, err := verifyE2EIdentity(token, secret, time.Unix(1700000119, 0)); err != nil {
		t.Fatalf("timeout+119 reconnect must remain valid: %v", err)
	}
	if _, err := verifyE2EIdentity(token, secret, time.Unix(1700000120, 0)); err == nil {
		t.Fatal("now == exp must be expired")
	}
}

func TestDecodeE2ESecretRequiresUnpadded32Bytes(t *testing.T) {
	short := base64.RawURLEncoding.EncodeToString(make([]byte, 31))
	if _, err := decodeE2ESecret(short); err == nil {
		t.Fatal("31-byte secret accepted")
	}
	fixture := loadIdentityFixture(t)
	if _, err := decodeE2ESecret(fixture.Secret + "="); err == nil {
		t.Fatal("padded secret accepted")
	}
}

func TestSignedOwnerAckPrecedesHubRegistration(t *testing.T) {
	source, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(source)
	ack := strings.Index(text, `if e2eClaims != nil {`)
	register := strings.Index(text, "hub.register(client)")
	if ack < 0 || register < 0 {
		t.Fatal("owner ACK or hub registration wiring is missing")
	}
	if ack > register {
		t.Fatal("signed client joins broadcasts before owner ACK is sent")
	}
}
