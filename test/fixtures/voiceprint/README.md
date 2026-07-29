# 声纹 E2E 合成工件

这里不存放音频。`scripts/prepare_voiceprint_fixtures.py` 会在每次 runner
运行的独占临时 artifact 目录中，使用当前 `/api/voices` 与 `/api/tts`
生成 A/B 两个音色的 8 份 PCM，并写入严格的
`voiceprint-fixtures.json` 后离线复核。

- 不要把生成的 `.pcm` 或 manifest 提交进仓库。
- 这些合成音频只证明声纹注册、识别、乘员隔离和删除链路的功能真值，
  不代表真人识别率、误认率或真实车内声学表现。
- 仓库不主张外部 TTS 供应商生成音频的再分发许可；工件仅在本次授权的
  验收运行中临时使用，许可边界仍以对应供应商条款为准。
