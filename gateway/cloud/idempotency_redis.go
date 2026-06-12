//go:build redis

package main

import (
	"context"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
)

type redisIdempotency struct {
	client *redis.Client
}

func newRedisIdempotency(addr string) (*redisIdempotency, error) {
	opt, err := redis.ParseURL(addr)
	if err != nil {
		opt = &redis.Options{Addr: addr}
	}
	client := redis.NewClient(opt)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		return nil, err
	}
	return &redisIdempotency{client: client}, nil
}

func (r *redisIdempotency) MarkIfNew(ctx context.Context, corrID string, ttl time.Duration) bool {
	// SetNX 天然原子：key 不存在时写入并返回 true，已存在返回 false。
	ok, err := r.client.SetNX(ctx, "idem:"+corrID, "1", ttl).Result()
	if err != nil {
		log.Printf("[idempotency] redis SetNX error: %v (fail-open)", err)
		return true // 出错时放行（fail-open）
	}
	return ok
}
