.PHONY: proto up down logs test e2e clean help

COMPOSE := docker compose -f deploy/docker-compose.yaml

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
	cd agents && python -m pytest -q || true
	cd orchestrator && python -m pytest -q || true

e2e:
	cd test && python -m pytest -q

clean:
	rm -rf gen/
