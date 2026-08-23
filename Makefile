.PHONY: proto up down logs test gate-intent-l0 gate-capability e2e e2e-check e2e-ci e2e-nightly e2e-milestone clean help

# 唯一 Compose 入口：根 compose.yaml 显式加载根目录 .env，避免 deploy/.env 覆盖。
# 新 clone 没有 .env 时仍可按 .env.example 创建后再启动。
COMPOSE := docker compose -f compose.yaml

help:
	@echo "proto  - 由 proto/ 生成 Go/Python gRPC 代码 (需 buf)"
	@echo "up     - docker-compose 起全栈 (PoC)"
	@echo "down   - 停全栈"
	@echo "logs   - 跟踪日志"
	@echo "test   - 各服务单测 + 契约测试"
	@echo "gate-intent-l0 - 意图对抗 L0 门禁（strict 档，本地与 CI 唯一入口）"
	@echo "gate-capability- 端侧车控能力完整性门禁（六维逐对象，CI blocking）"
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

# 并行全量（2026-08-23 实测 25min → ~4.5min，三种并行形态各一趟均 6933/32 与串行基线
# 逐字一致）。--import-mode=importlib 口径的唯一声明处已收敛到根 pytest.ini 的 addopts，
# 这里不再抄一份。慢守卫（真子进程/全语料，§60.3 判定不可删）靠 worksteal 打散摊薄。
test:
	python -m pytest -q -n auto --dist worksteal

# B2：意图对抗 L0 门禁的唯一入口。封装脚本由 Python 读退出码——证据链上不出现
# shell 管道（2026-08-10 `cmd | tail; echo $$?` 把 exit 2 报成 exit 0 的机制化根治）。
gate-intent-l0:
	python scripts/check_intent_gate.py

# B4：新增/改动车控能力后跑它。漏一处（话术/等价类/对抗覆盖/可对账状态键）即具名红灯。
gate-capability:
	python test/eval_capability_integrity.py

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
