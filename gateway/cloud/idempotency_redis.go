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

func (r *redisIdempotency) Seen(ctx context.Context, corrID string) bool {
	exists, err := r.client.Exists(ctx, "idem:"+corrID).Result()
	if err != nil {
		log.Printf("[idempotency] redis EXISTS error: %v", err)
		return false // 出错时放行（fail-open）
	}
	return exists > 0
}

func (r *redisIdempotency) Mark(ctx context.Context, corrID string, ttl time.Duration) {
	if err := r.client.Set(ctx, "idem:"+corrID, "1", ttl).Err(); err != nil {
		log.Printf("[idempotency] redis SET error: %v", err)
	}
}
