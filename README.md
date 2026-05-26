# Trae Daily Report Skill

一键生成 Trae CN 每日工作报告的 AI Skill。

## 功能

- 自动解密 Trae CN 数据库并提取当日对话记录
- 调用 DeepSeek AI 模型自动生成日报总结
- 支持自定义提示词和输出目录
- 可注册为 Trae IDE Skill，输入 `/日报` 即可触发

## 安装

```bash
pip install pycryptodome requests
```

## 快速开始

### 1. 提取加密密钥（仅首次，需要 Trae CN 运行中）

```bash
python trae_daily_saver.py --scan-key
```

### 2. 生成今日日报

```bash
# 使用默认 DeepSeek 配置
python trae_daily_saver.py --api-key sk-xxxx

# 设置环境变量后直接运行
set OPENAI_API_KEY=sk-xxxx
python trae_daily_saver.py
```

### 3. 注册为 Trae Skill

将 `SKILL.md` 复制到 `.trae/skills/daily-report/` 目录下即可。

## 输出

```
trae_dialogues/
├── trae_dialogues_YYYYMMDD.md   # 完整对话记录
└── daily_report.md              # AI 生成的日报
```

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--scan-key` | 扫描内存提取密钥 | - |
| `--key-file` | 密钥文件路径 | `decrypted_key.json` |
| `--db-path` | 加密数据库路径 | 自动检测 |
| `--output-dir` | 输出目录 | `./trae_dialogues` |
| `--date` | 指定日期 | 今天 |
| `--api-key` | API Key | 环境变量 |
| `--api-base` | API 地址 | `api.deepseek.com` |
| `--model` | 模型名称 | `deepseek-chat` |
| `--system-prompt` | 自定义提示词 | 内置默认 |
| `--no-summary` | 仅导出对话 | - |

## 依赖项目

- [trae-db-decrypt](https://github.com/oh-my-trae/trae-db-decrypt) - Trae CN 数据库解密方案

## License

MIT
