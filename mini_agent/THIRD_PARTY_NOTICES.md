# 第三方引用

mini agent 是独立的 Python 教学项目。教程保留外部项目名称与原始标识符，便于识别引用来源；这些名称不是本项目的品牌。

## pi

教程在文件变更队列、上下文压缩和系统提示词章节对照了 pi 的设计，并引用了 TypeScript 代码与提示词片段。

- 上游项目：https://github.com/badlogic/pi-mono
- 教程标注的源码位置包括 `packages/agent/src/harness/compaction/compaction.ts`。
- 许可证：MIT，Copyright (c) 2025 Mario Zechner。完整声明见 [licenses/pi-MIT.txt](licenses/pi-MIT.txt)。
- 引用来自教程原有快照；仓库当前版本的目录结构和行为可能已变化。

## Codex

教程引用 Codex 0.156.0 的上下文压缩提示词及相关字符串，并标明 `core/src/compact.rs`。这是原教程记录的历史版本对照，不代表当前版本行为。

- 上游项目：https://github.com/openai/codex
- 上游采用 Apache-2.0；完整许可证见 [licenses/codex-Apache-2.0.txt](licenses/codex-Apache-2.0.txt)。第三方引用的许可不由本项目 MIT 许可证替代。

## 其他

教程链接到 OpenAI API 文档作为协议参考。Pygments 仅用于本地生成代码高亮，运行 agent 无需它；本目录不分发 Pygments 库本身。
