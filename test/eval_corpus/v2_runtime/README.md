# v2 Runtime / Jev 共用开发基线

CA2-01/JV00 共用 `seed.yaml` 的 20 组、按 family 分组的开发回归种子。
这些用例已经公开，不是 holdout；后续新标注/校准/冻结测试按家族隔离。

`scripts/probe_v2_baseline.py` 复用既有签名 E2E 身份、WS 多 final 收取、collector 稳定回读和 release 对账。
每组/每次重复使用新的 synthetic user/session，禁止自动确认，挂起只作点名取消；不授予商户写、支付、画像写或导航执行 scope。
每轮比较完整车态，发生动作/变化/证据缺失立即停；不自动恢复车辆，不删除数据。

冻结包分别登记 runner SHA、部署 SHA、各组代码/知识目录 Git tree OID 与总 SHA-256、语料摘要、
实际模型和原始 trace。未测的 APK/运行配置明确标未测，不把本机配置当远端证明。

```powershell
python scripts/dev_stack.py target show
python scripts/probe_v2_baseline.py --dry-run
# 先提交干净输入；以下 SHA 是精确待测生产版本，不是默认 HEAD。
python scripts/probe_v2_baseline.py --expected-sha <40位release> --repeat 3 --out .artifacts/v2-runtime/baseline-a.json
```

`--ids V201,V202` 可限定批次，报告会保留选择集，不伪装成完整 20 组。
返回 0 只表示测量完成且版本连续；业务红项单独留在 `summary.business_failures` 和逐轮 verdict，不能写成测试全绿。
`manual_dispatched` 与 `manual_presented` 分列，回答在话术里不能替代手册卡/完整结果验收。
新增 trace/设备/故障量尺先验证反例，再用来判断升级收益。
