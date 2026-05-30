package main

import (
	"context"
	"log"
	"os"
	"sync"
	"time"
)

// IdempotencyStore 幂等存储。Redis 可用时用 Redis（跨实例持久），否则内存兜底。
// TTL 10 分钟：重连后端可能重发未确认请求，10 分钟后自动过期。
type IdempotencyStore interface {
	// Seen 检查 correlation_id 是否已处理。返回 true 表示重复。
	Seen(ctx context.Context, corrID string) bool
	// Mark 标记 correlation_id 为已处理。
	Mark(ctx context.Context, corrID string, ttl time.Duration)
}

// ─── 内存实现（PoC / Redis 不可用时） ───

type memoryIdempotency struct {
	mu      sync.RWMutex
	seen    map[string]time.Time
	ttl     time.Duration
}

func newMemoryIdempotency() *memoryIdempotency {
	return &memoryIdempotency{
		seen: make(map[string]time.Time),
		ttl:  10 * time.Minute,
	}
}

func (m *memoryIdempotency) Seen(_ context.Context, corrID string) bool {
	m.mu.RLock()
	expire, ok := m.seen[corrID]
	m.mu.RUnlock()
	if !ok {
		return false
	}
	if time.Now().After(expire) {
		m.mu.Lock()
		delete(m.seen, corrID)
		m.mu.Unlock()
		return false
	}
	return true
}

func (m *memoryIdempotency) Mark(_ context.Context, corrID string, ttl time.Duration) {
	m.mu.Lock()
	m.seen[corrID] = time.Now().Add(ttl)
	m.mu.Unlock()
}

// ─── Redis 实现 ───
// 需要 go-redis：go get github.com/redis/go-redis/v9
// 当前用 build tag 控制，未安装 go-redis 时自动降级内存。

// buildIdempotencyStore 创建幂等存储。有 REDIS_URL 用 Redis，否则内存。
func buildIdempotencyStore() IdempotencyStore {
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = os.Getenv("REDIS_ADDR")
	}
	if redisURL != "" {
		// 尝试连接 Redis（go-redis 可选依赖）
		if store, err := newRedisIdempotency(redisURL); err == nil {
			log.Printf("[idempotency] using Redis: %s", redisURL)
			return store
		}
		log.Printf("[idempotency] Redis unavailable, falling back to in-memory")
	}
	log.Printf("[idempotency] using in-memory store")
	return newMemoryIdempotency()
}
