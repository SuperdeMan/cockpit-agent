# 安全策略 / Security Policy

本项目是个人维护的智能座舱 Multi-Agent 工程化 PoC，无版本化发行，`main` 即最新。

## 上报安全问题

- **请勿在公开 issue 里披露漏洞细节。**
- 首选渠道：本仓库 **Security → Report a vulnerability**（GitHub Private Vulnerability Reporting）。
- 车控安全是本项目的架构红线（`CLAUDE.md` §5：车控只经 VAL、LLM 不直连车控、危险动作二次
  确认、S2S 会话内无执行通道）。**任何能绕过这些闸门的路径都视为高危漏洞**，欢迎优先上报；
  这些红线均有契约测试固化，附带能让测试变红的复现最好。
- 同样按高危处理：确认被重放或跨车、跨参数复用；只响应能力产生了动作或挂起；第三方（MCP、搜索）
  返回的文本改变了权限、车辆或操作参数；未经授权读到别人的记忆、订单或车况。

Please do not disclose vulnerabilities in public issues — use GitHub's private
vulnerability reporting on this repository instead. Any path that lets an LLM or
agent bypass the deterministic vehicle-control gates (VAL, double-confirmation)
is treated as high severity, as are confirmation replay across vehicles or
parameters, actions produced by response-only capabilities, third-party text
changing permissions or operation parameters, and cross-user data access.
