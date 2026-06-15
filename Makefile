.PHONY: proto up down logs test e2e clean help

# compose 文件在 deploy/，Compose 默认不读仓库根的 .env；根 .env 存在时显式加载
# （条件式：缺 .env 的新 clone 不传 --env-file，避免 compose 报"文件不存在"）
ENV_FILE := $(wildcard .env)
COMPOSE := docker compose $(if $(ENV_FILE),--env-file .env,) -f deploy/docker-compose.yaml

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
	cd test && python -m pytest -q

clean:
	rm -rf gen/
