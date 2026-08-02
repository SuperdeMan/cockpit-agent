# 安全策略 / Security Policy

本项目是个人维护的智能座舱 Multi-Agent 工程化 PoC，无版本化发行，`main` 即最新。

## 上报安全问题

- **请勿在公开 issue 里披露漏洞细节。**
- 首选渠道：本仓库 **Security → Report a vulnerability**（GitHub Private Vulnerability Reporting）。
- 车控安全是本项目的架构红线（`CLAUDE.md` §5：车控只经 VAL、LLM 不直连车控、危险动作二次
  确认、S2S 会话内无执行通道）。**任何能绕过这些闸门的路径都视为高危漏洞**，欢迎优先上报；
  这些红线均有契约测试固化，附带能让测试变红的复现最好。

Please do not disclose vulnerabilities in public issues — use GitHub's private
vulnerability reporting on this repository instead. Any path that lets an LLM or
agent bypass the deterministic vehicle-control gates (VAL, double-confirmation)
is treated as high severity.
