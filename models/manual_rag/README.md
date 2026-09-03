# manual-rag 私有索引资产

这里只放 `scripts/build_manual_index.py` 生成的车型手册索引包。约定：

- 源 PDF 不复制到本目录、不进入 Git；
- `*.json.gz` 兼容索引只含手册抽取正文；v2 `.mrag` 同时含文本索引、受控视觉目录与
  去重后的 JPEG/PNG blob。两者默认全部 gitignore；
- 一份索引只绑定一个 `vehicle_model`、源 PDF SHA-256 和内容 SHA-256；
- 索引还必须命中 `agents/manual_rag/resources/manual_catalog.yaml` 的批准指纹；
- 默认运行文件名为 `xiaomi-su7-2024.v2.mrag`；旧 `v1.json.gz` 仍可读但不返回图片；
- 构建和核验命令见 `agents/manual_rag/README.md`；
- 本机镜像构建前须在受控工作区放入已核验索引；缺失时显式 real 配置会 fail-fast，
  不会回退 mock；
- cloud release 只打包 Git commit，不直接携带本目录 ignored 索引；发布通过既有
  shared-model bootstrap 按固定 SHA 安装，再只读挂载给 `manual-rag-agent`。
