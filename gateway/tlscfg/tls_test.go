package tlscfg

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"testing"
	"time"
)

func TestEnabled(t *testing.T) {
	for _, v := range []string{"on", "true", "1", "yes"} {
		t.Setenv("GRPC_TLS", v)
		if !Enabled() {
			t.Fatalf("GRPC_TLS=%q should enable", v)
		}
	}
	for _, v := range []string{"", "off", "false", "0", "no"} {
		t.Setenv("GRPC_TLS", v)
		if Enabled() {
			t.Fatalf("GRPC_TLS=%q should NOT enable", v)
		}
	}
}

func TestServerNameDefaultAndOverride(t *testing.T) {
	t.Setenv("GRPC_TLS_SERVER_NAME", "")
	if serverName() != "cockpit-mesh" {
		t.Fatalf("default serverName = %q, want cockpit-mesh", serverName())
	}
	t.Setenv("GRPC_TLS_SERVER_NAME", "custom-name")
	if serverName() != "custom-name" {
		t.Fatalf("override serverName = %q", serverName())
	}
}

func TestCredsErrorOnMissingCerts(t *testing.T) {
	t.Setenv("GRPC_TLS_CERT", "/nonexistent/server.crt")
	t.Setenv("GRPC_TLS_KEY", "/nonexistent/server.key")
	t.Setenv("GRPC_TLS_CA", "/nonexistent/ca.crt")
	if _, err := ClientCreds(); err == nil {
		t.Fatal("ClientCreds should error on missing certs")
	}
	if _, err := ServerCreds(); err == nil {
		t.Fatal("ServerCreds should error on missing certs")
	}
}

func TestCredsLoadValidCerts(t *testing.T) {
	dir := t.TempDir()
	caPEM, certPEM, keyPEM := genTestCert(t)
	writeFile(t, dir+"/ca.crt", caPEM)
	writeFile(t, dir+"/server.crt", certPEM)
	writeFile(t, dir+"/server.key", keyPEM)
	t.Setenv("GRPC_TLS_CA", dir+"/ca.crt")
	t.Setenv("GRPC_TLS_CERT", dir+"/server.crt")
	t.Setenv("GRPC_TLS_KEY", dir+"/server.key")
	if _, err := ClientCreds(); err != nil {
		t.Fatalf("ClientCreds: %v", err)
	}
	if _, err := ServerCreds(); err != nil {
		t.Fatalf("ServerCreds: %v", err)
	}
}

// genTestCert 生成一张自签证书（同时作 CA），可作 server+client 双身份（含两 EKU）。
func genTestCert(t *testing.T) (caPEM, certPEM, keyPEM []byte) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "cockpit-mesh"},
		DNSNames:              []string{"cockpit-mesh"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	keyPEM = pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	return certPEM, certPEM, keyPEM
}

func writeFile(t *testing.T, path string, b []byte) {
	t.Helper()
	if err := os.WriteFile(path, b, 0o600); err != nil {
		t.Fatal(err)
	}
}
