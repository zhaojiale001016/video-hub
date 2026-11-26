# 贡献指南

感谢对 video-hub 的兴趣！

## 提 Issue

- **Bug**：附上 `cli.py doctor` 输出、相关日志片段（脱敏 cookie/路径）、复现步骤
- **功能建议**：说明使用场景和期望行为
- 涉及账号/cookie/私有路径的信息请打码后再贴

## 提 PR

1. Fork + 新分支，改动保持小步快跑
2. 遵循现有代码风格（紧凑、少注释、跟随周边惯例）
3. 提交前跑一遍：`python -m compileall pipeline web cli.py`
4. 不引入新的硬依赖；新增配置走 `config/overrides.json` 分层，不硬编码
5. 涉及发布链路的改动请在 PR 描述里说明回归范围

## 本地开发

```bash
cp config/sources.example.yaml config/sources.yaml
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python3 cli.py serve
# http://127.0.0.1:8600
```

`config/sources.yaml`/`overrides.json`/`data/` 均为本地私有文件，不会被提交。
