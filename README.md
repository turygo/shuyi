# Shuyi · 书衣

为 EPUB 换一身适合中文阅读的书衣，不翻译、不改写正文。

A Chinese reading theme for your EPUBs—without translating or rewriting the prose.

[简体中文](#简体中文) · [English](#english)

## 简体中文

### 书衣是什么？

书衣是一个命令行工具，为已有的 EPUB 电子书调整排版，让正文、标题、引文和注释更适合中文阅读。它会生成一本新书，保留原文件，不需要重新制作电子书。

### 主要功能

- **中文阅读排版**：统一正文缩进、标题、引文和注释的视觉样式，也支持自定义样式。
- **内容保留与检查**：输出前检查正文、图片、目录和链接，避免排版调整破坏书籍内容。
- **注释标记整理**：将确认识别的注释引用和返回标记统一为“注”，保留注释正文和跳转关系。
- **复用分析结果**：重复调整同一本书时，可复用已完成的分析，减少模型调用。

### 快速开始

需要 Python 3.12 或更新版本，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

安装：

```sh
git clone https://github.com/turygo/shuyi.git
cd shuyi
uv tool install .
```

参照 [配置示例](config.example.toml)，在 `~/.config/shuyi/config.toml` 中配置兼容 OpenAI API 的模型服务，并设置对应的 API 密钥环境变量。模型分析可能产生费用。

为电子书应用默认排版：

```sh
shuyi style book.epub
```

默认在原文件旁生成 `book.shuyi.epub`，不会覆盖原书或已有文件。

查看全部用法：

```sh
shuyi --help
shuyi style --help
```

### 使用前须知

- 支持不含 DRM 的 EPUB 2/3，不提供格式转换、翻译或 DRM 移除。
- **隐私**：分析时会将部分原文及排版信息发送至你配置的模型服务。请确认自己有权处理相关内容，并了解服务商的数据政策。
- **密钥与书籍**：不要将 API 密钥、私人书籍、书籍摘录或含敏感信息的运行记录提交到公开仓库。
- **阅读效果**：不同阅读器的显示和注释交互可能不同，建议在常用阅读器中检查生成的书籍。

### 反馈与贡献

欢迎通过 [Issues](https://github.com/turygo/shuyi/issues) 报告问题或提出建议，也欢迎提交 Pull Request。报告排版问题时，请提供可公开分享的最小示例，不要上传受版权保护或含私人信息的书籍。

参与开发前，请阅读 [开发与验证指南](HARNESS.md) 和 [项目规格](SPEC.md)。

## English

### What is Shuyi?

Shuyi is a command-line tool that restyles existing EPUB books for Chinese reading. It gives body text, headings, quotations, and notes a consistent appearance, then saves a new book without replacing the original. There is no need to rebuild the ebook.

### Highlights

- **Chinese reading typography**: consistent paragraph indentation, headings, quotations, and notes, with support for custom styles.
- **Content preservation checks**: checks prose, images, navigation, and links before saving to help prevent styling changes from damaging the book.
- **Consistent note markers**: replaces confirmed note reference and return labels with “注” while preserving note text and navigation.
- **Reusable analysis**: reuses completed analysis when restyling the same book to reduce model calls.
- **Chinese-first bilingual reading**: `--bilingual` keeps confirmed Chinese text primary and presents English as smaller, muted, regular sans-serif reference text. Standalone reference paragraphs have a fine left rule; inline fragments, lists and tables retain their structure.
- **Source CSS compatibility**: understands common publisher namespaces, static media/supports conditions and link/pseudo-element evidence without rewriting the original stylesheets or relaxing custom CSS restrictions.

### Quick start

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).

Install:

```sh
git clone https://github.com/turygo/shuyi.git
cd shuyi
uv tool install .
```

Use the [example configuration](config.example.toml) to configure an OpenAI-compatible model service in `~/.config/shuyi/config.toml`, then set the corresponding API-key environment variable. Model analysis may incur charges.

Apply the default reading theme:

```sh
shuyi style book.epub
```

By default, Shuyi creates `book.shuyi.epub` beside the original. It never overwrites the source or an existing file.

For an existing Chinese/English bilingual EPUB:

```sh
shuyi inspect bilingual.epub
shuyi style bilingual.epub --bilingual
shuyi check bilingual.shuyi.epub
```

Shuyi recognizes explicit adjacent bilingual metadata, compatible per-node language pairs and corroborated AiNiee export structures. It never guesses by paragraph parity or styling alone. Uncertain unmarked material stays unchanged and is reported with resource/path warnings; explicit identity/language conflicts stop conversion. The command does not translate, reorder or hide either language. Standard single-language use is unchanged.

The built-in English reference layer uses roughly 82% of primary text size, a distinct sans-serif font, regular container text and separate light/dark colors. Original inline emphasis remains. Custom CSS replaces the built-in appearance and may target `[data-sy-content="source"]`, `"target"` or `"mixed"` within the restricted style language. Reader rendering still varies by device.

Explore the available options:

```sh
shuyi --help
shuyi style --help
```

### Before you begin

- Supports DRM-free EPUB 2/3. Does not convert formats, translate books, or remove DRM.
- **Privacy**: analysis sends selected source text and formatting information to your configured model service. Make sure you have permission to process the content and understand the provider’s data policies.
- **Keys and books**: never commit API keys, private books, book excerpts, or sensitive run records to a public repository.
- **Reader compatibility**: appearance and note interactions vary across reading apps. Check the output in your preferred reader.

### Feedback and contributions

Report bugs and suggest improvements through [Issues](https://github.com/turygo/shuyi/issues), or open a Pull Request. For formatting problems, provide a minimal example that is safe to share publicly. Do not upload copyrighted or private books.

Before contributing, read the [development and verification guide](HARNESS.md) and [project specification](SPEC.md).
