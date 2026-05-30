//go:build !redis

package main

import (
	"fmt"
)

// Redis 未启用时的占位（编译时不包含 go-redis 依赖）。
func newRedisIdempotency(addr string) (IdempotencyStore, error) {
	return nil, fmt.Errorf("redis support not compiled in (build with -tags redis)")
}
