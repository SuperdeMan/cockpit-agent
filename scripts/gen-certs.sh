#!/usr/bin/env bash
# 生成服务间 mTLS 自签证书。等价于 scripts/gen-certs.ps1。需安装 openssl。
# 产物：certs/{ca.crt,ca.key,server.crt,server.key}——单张共享 mesh 证书（CN/SAN=cockpit-mesh），
# 所有服务共用为 server+client 双身份；客户端经 ssl_target_name_override/ServerName 固定校验此名。
# 证书/私钥已 gitignore，切勿提交。设计见 docs/design/2026-07-02-r3.2-service-mtls.md。
set -euo pipefail
cd "$(dirname "$0")/.."
certs="$(pwd)/certs"
mkdir -p "$certs"
name="${GRPC_TLS_SERVER_NAME:-cockpit-mesh}"
days=3650

echo "[gen-certs] CA ..."
openssl genrsa -out "$certs/ca.key" 4096
openssl req -x509 -new -nodes -key "$certs/ca.key" -sha256 -days "$days" -subj "/CN=cockpit-mesh-ca" -out "$certs/ca.crt"

echo "[gen-certs] shared mesh cert (CN/SAN=$name) ..."
ext="$certs/san.ext"
cat > "$ext" <<EOF
subjectAltName=DNS:$name,DNS:localhost,IP:127.0.0.1
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
EOF
openssl genrsa -out "$certs/server.key" 4096
openssl req -new -key "$certs/server.key" -subj "/CN=$name" -out "$certs/server.csr"
openssl x509 -req -in "$certs/server.csr" -CA "$certs/ca.crt" -CAkey "$certs/ca.key" -CAcreateserial \
  -sha256 -days "$days" -extfile "$ext" -out "$certs/server.crt"
rm -f "$certs/server.csr" "$ext"

echo "[gen-certs] done -> $certs (ca.crt/ca.key/server.crt/server.key). 切勿提交私钥。"
