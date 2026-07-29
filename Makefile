.PHONY: proto up down logs test e2e e2e-check e2e-ci e2e-nightly e2e-milestone clean help

# 唯一 Compose 入口：根 compose.yaml 显式加载根目录 .env，避免 deploy/.env 覆盖。
# 新 clone 没有 .env 时仍可按 .env.example 创建后再启动。
COMPOSE := docker compose -f compose.yaml

help:
	@echo "proto  - 由 proto/ 生成 Go/Python gRPC 代码 (需 buf)"
	@echo "up     - docker-compose 起全栈 (PoC)"
	@echo "down   - 停全栈"
	@echo "logs   - 跟踪日志"
	@echo "test   - 各服务单测 + 契约测试"
	@echo "e2e           - 默认分组端到端场景测试"
	@echo "e2e-check     - E2E 清单、协议与 stale 结构门禁"
	@echo "e2e-ci        - 普通 CI 的确定性 E2E lane"
	@echo "e2e-nightly   - nightly E2E lane（需真栈）"
	@echo "e2e-milestone - 里程碑 canonical（需 MILESTONE/PROVIDER/MODEL）"

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
	python scripts/run_e2e.py --full

e2e-check:
	python scripts/run_e2e.py --check --stale-policy warn

e2e-ci:
	python scripts/run_e2e.py --lane ci --full --stale-policy warn

e2e-nightly:
	python scripts/run_e2e.py --lane nightly --full --stale-policy warn

e2e-milestone:
	python scripts/run_e2e.py --milestone $(MILESTONE) --lane milestone --full --canonical --provider $(PROVIDER) --model $(MODEL) --stale-policy error

clean:
	rm -rf gen/
