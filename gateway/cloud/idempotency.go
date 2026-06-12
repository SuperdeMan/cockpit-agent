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
// 接口收敛为 MarkIfNew（F18）：原子化查验+标记，消除 Seen/Mark TOCTOU。
type IdempotencyStore interface {
	// MarkIfNew 原子化检查并标记。true=首次放行，false=重复跳过。
	// Redis 出错时 fail-open（按"首次"放行）：幂等保护体验，错杀比偶发重复更糟。
	MarkIfNew(ctx context.Context, corrID string, ttl time.Duration) bool
}

// ─── 内存实现（PoC / Redis 不可用时） ───

type memoryIdempotency struct {
	mu   sync.Mutex
	seen map[string]time.Time
}

func newMemoryIdempotency() *memoryIdempotency {
	return &memoryIdempotency{
		seen: make(map[string]time.Time),
	}
}

func (m *memoryIdempotency) MarkIfNew(_ context.Context, corrID string, ttl time.Duration) bool {
	m.mu.Lock()
	defer m.mu.Unlock()

	if expire, ok := m.seen[corrID]; ok {
		if time.Now().Before(expire) {
			return false // 已存在且未过期 → 重复
		}
		// 已过期，清理后继续
		delete(m.seen, corrID)
	}

	m.seen[corrID] = time.Now().Add(ttl)
	return true // 首次
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
