# Luna Decision Model

**保留 Jev 工作流，用你已有的 Codex 订阅完成结构化决策。**

一个小型本机服务，让现有的 Jev / TypeSafe 决策代码（是/否、选择和评分问题；语义 `ask` / `find`；Read Hook；selector MCP）通过你本人的 Codex ChatGPT 登录在 OpenAI GPT-6 Luna 上运行，无需按量计费的 API key。

[English](README.md)

## 为什么用决策模型

Agent 和应用的工作流里有大量类型化小判断：这个工具调用是否有破坏性、这段文本是否不安全、哪个文件能回答问题。决策模型直接返回概率或标签而不是生成文本，原生 Jev 已经被这样使用：

- **Agent 上下文：** [jev](https://github.com/BorisLeMeec/jev) 按描述找代码（P@1 0.96，grep 为 0.08），配对测试中 agent 计费输入 token 减少 30–41%；[jev-codex-token-saver](https://github.com/jcressler/jev-codex-token-saver) 在 108 次配对运行中让 Codex 输入 token 减少 39.2%，质量不变。
- **安全护栏：** [Red Hat 的基准测试](https://developers.redhat.com/articles/2026/10/02/benchmarking-ai-decision-models-against-traditional-guardrails)中 Jev 内容安全排名第一（86.20%，中位 360 ms）。
- **工具调用风险：** [jev-benchmark](https://github.com/themsquared/jev-benchmark) 中 Jev 与 Claude Sonnet 5 准确率相同（91.7%），中位延迟 422 ms 对 1,371 ms，单次成本约低 40 倍。

Luna Decision Model 让这些调用方通过你的 Codex 订阅在 GPT-6 Luna 上运行，对比见[基准测试](#基准测试)。

## 工作原理

![你的 Jev / TypeSafe 应用调用本机 Luna Decision Model 服务，服务用你的 Codex 登录调用 GPT-6 Luna。](docs/assets/architecture.svg)

## 成本

![每 1,000 次请求的成本：原生 Jev $0.026，GPT-6 Luna API $0.075，Luna Decision Model 按 GPT-6 Luna credit 费率约 1.9 credits。](docs/assets/cost.svg)

## 基准测试

![Decision Bench v4，1,071 行：原生 Jev、经 Decisions API 的 GPT-6 Luna 与经本项目的 GPT-6 Luna，在整体和各任务组上的准确率、Brier 和中位延迟。](docs/assets/public-comparison.svg)

逐任务结果、运行细节、推理强度和费率来源见 [docs/COMPARISON.md](docs/COMPARISON.md)（英文）。你自己套餐的用量见 [docs/QUOTA.md](docs/QUOTA.md)（英文）。

## 快速开始

需要 Python 3.10+、`PATH` 中的 Codex CLI，以及有 Luna 权限的 ChatGPT 登录（[详情](INSTALL.md#requirements)）。

**1. 安装并启动**（在本仓库的 checkout 中）：

```bash
python3 -m venv .venv
.venv/bin/pip install '.[openai]'
.venv/bin/luna-decision start
```

服务随即在当前终端运行于 `127.0.0.1:20141`；按 `Ctrl-C` 停止。Codex Fast 模式可选：用 `LDM_SERVICE_TIER=fast .venv/bin/luna-decision start` 启动即可请求该模式，它按标准速率的 2.5 倍消耗内含额度（[详情](TECHNICAL_REFERENCE.md#environment-variables)）。

**2. 检查**：在同一目录打开第二个终端：

```bash
.venv/bin/luna-decision doctor
```

**3. 提出第一个问题：**

```bash
.venv/bin/python -I - <<'PY'
from luna_decision_model import openai_client

with openai_client() as client:
    result = client.decisions.create(
        model="gpt-6-luna",
        input="The parcel arrived with a broken screen.",
        questions=[{"type": "predicate", "instructions": "Is the item damaged?"}],
    )
    print(result.answers[0].probability)
PY
```

## 连接你的项目

```bash
.venv/bin/luna-decision config --integration typesafe --project /absolute/path/to/your-project
```

此命令只预览改动；加 `--write` 应用，`--restore` 撤销。它写入一条规则，让编码 agent 用 `typesafe_client()` 创建客户端。使用 OpenAI SDK 的代码选 `--integration openai`。原生 Jev CLI、Read Hook 和 selector 的配置见 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)（英文）。

## 文档

| 文档 | 内容 |
| --- | --- |
| [INSTALL.md](INSTALL.md) | 安装选项、诊断、MCP、卸载 |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | SDK、TypeSafe 插件、原生 Jev 调用方 |
| [docs/COMPARISON.md](docs/COMPARISON.md) · [docs/QUOTA.md](docs/QUOTA.md) | 完整基准、价格、额度估算 |
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) | API、CLI、环境变量、安全 |
| [CHANGELOG.md](CHANGELOG.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md) | 版本历史、开发、AI agent 入口 |

其余文档为英文。

## 说明

非官方项目，与 OpenAI 无关联；能否使用 GPT-6 Luna 取决于你的账号，且不会转用付费 API。基准和成本数据来自历史的非配对运行（[条件](docs/COMPARISON.md)）。

[MIT](LICENSE) · [第三方 notices](THIRD_PARTY_NOTICES.md)
