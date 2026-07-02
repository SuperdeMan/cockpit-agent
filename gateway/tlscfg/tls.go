// R3.2 服务间 mTLS：从环境变量装配 gRPC TLS 传输凭证（client/server），全 env 门控、默认关。
// 与 Python 侧 runtime/grpcio.py 对称——单张共享 mesh 证书作 server+client 双身份；客户端把校验
// ServerName 固定为 GRPC_TLS_SERVER_NAME（无视拨号 authority，解决 agent 用动态 hostname 注册）。
// 见 docs/design/2026-07-02-r3.2-service-mtls.md。
package tlscfg

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"

	"google.golang.org/grpc/credentials"
)

// Enabled 报告是否启用 mTLS（GRPC_TLS=on/true/1/yes）。默认关 = 保持现状 insecure。
func Enabled() bool {
	switch os.Getenv("GRPC_TLS") {
	case "on", "true", "1", "yes":
		return true
	}
	return false
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func serverName() string { return env("GRPC_TLS_SERVER_NAME", "cockpit-mesh") }

// load 读取共享 mesh 证书（server.crt/key）+ CA 池。
func load() (tls.Certificate, *x509.CertPool, error) {
	cert, err := tls.LoadX509KeyPair(
		env("GRPC_TLS_CERT", "/certs/server.crt"),
		env("GRPC_TLS_KEY", "/certs/server.key"))
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("load keypair: %w", err)
	}
	ca, err := os.ReadFile(env("GRPC_TLS_CA", "/certs/ca.crt"))
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("read CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return tls.Certificate{}, nil, fmt.Errorf("append CA cert failed")
	}
	return cert, pool, nil
}

// ClientCreds 校验服务端（RootCAs + ServerName 固定）并出示客户端证书（mTLS）。
func ClientCreds() (credentials.TransportCredentials, error) {
	cert, pool, err := load()
	if err != nil {
		return nil, err
	}
	return credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      pool,
		ServerName:   serverName(),
	}), nil
}

// ServerCreds 出示服务端证书并强制校验客户端证书（mTLS）。
func ServerCreds() (credentials.TransportCredentials, error) {
	cert, pool, err := load()
	if err != nil {
		return nil, err
	}
	return credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientCAs:    pool,
		ClientAuth:   tls.RequireAndVerifyClientCert,
	}), nil
}
