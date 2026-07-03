.PHONY: proto up down logs test e2e clean help

# 唯一 Compose 入口：根 compose.yaml 显式加载根目录 .env，避免 deploy/.env 覆盖。
# 新 clone 没有 .env 时仍可按 .env.example 创建后再启动。
COMPOSE := docker compose -f compose.yaml

help:
	@echo "proto  - 由 proto/ 生成 Go/Python gRPC 代码 (需 buf)"
	@echo "up     - docker-compose 起全栈 (PoC)"
	@echo "down   - 停全栈"
	@echo "logs   - 跟踪日志"
	@echo "test   - 各服务单测 + 契约测试"
	@echo "e2e    - 端到端场景测试"

proto:
	buf generate proto

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test:
	python -m pytest --import-mode=importlib -q

e2e:
	bash scripts/run_e2e.sh

clean:
	rm -rf gen/
