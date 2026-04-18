# z-paper

> 本地部署的学术论文精准翻译与 AI 知识库对话工具，专为个人研究者设计。

所有数据保存在本地，不依赖任何外部数据库或消息队列服务。

---

## 下载安装（Windows）

无需配置 Python / Node.js 环境，下载后直接安装运行：

**百度网盘：** https://pan.baidu.com/s/1ZdRe2TixlulmjIyZI0UEzQ?pwd=6zqp  提取码：6zqp

安装完成后桌面出现快捷方式，双击即可启动，浏览器自动打开。

---

## 一、精准翻译

学术论文翻译的核心难题不是语言，而是**术语一致性**和**排版还原**。z-paper 通过六阶段并行流水线解决这两个问题。

```
上传 PDF
  → MinerU 解析（识别多栏/公式/表格/图注）
  → 并行文本清理（8 并发）：合并 PDF 断行、修正 OCR 错误、规范 LaTeX 格式
  → 术语提取：自动识别领域专有名词
  → [暂停] 用户审查新术语，确认每个词的译法（仅翻译 / 保留原文 / 翻译并标注原文）
  → 带术语表并行翻译段落（8 并发，DeepSeek）+ 图片翻译（3 并发，Qwen）
  → 生成双栏译文
```

**关键设计：术语审查检查点**

大多数翻译工具在遇到新术语时会随机翻译，导致同一术语在论文不同位置出现不同译名。z-paper 在翻译前强制暂停，让用户确认每一个新术语的译法，确认后保存到个人词库，后续所有翻译自动复用，保证全文一致。

**双层词汇系统**

- **个人词库**：跨论文通用，自定义每个术语的处理策略
- **领域词库**：11 个预设学科（计算机科学、数学、物理学、生物学等）公用参考词表
- 每次翻译快照词库状态，译文可复现

**阅读体验**

- 中英双栏并排，支持切换单栏
- LaTeX 数学公式 KaTeX 实时渲染
- 图片 Lightbox 放大，原图与译图对比
- 文内引用 `[1]` 可点击跳转参考文献列表
- 精确到段落的划选批注系统

---

## 二、知识库对话

### 设计思想：工具优先，而非塞满 Context

目前大多数"论文问答"产品的做法是 RAG：把检索出的文本块塞进 context，让模型回答。这个方案有明显上限——检索质量决定回答质量，模型没有主动探索的能力。

z-paper 的设计思想借鉴自 **[Claude Code](https://claude.ai/code)**：**不预先决定给模型看什么，而是给 Agent 配备一套工具，让模型自主决定每一步该读哪里、读多少**。就像 Claude Code 不会把整个代码库塞进 context，而是通过工具按需读取文件——context 是稀缺资源，Agent 的价值在于它知道如何高效使用它。

### 7 个工具构成完整的导航能力

| 工具 | 功能 |
| --- | --- |
| `search_papers` | 在全库论文元数据（标题/摘要/关键词）中搜索 |
| `get_paper_outline` | 获取论文章节大纲，决定要不要深入 |
| `search_in_paper` | 在单篇论文全文中关键词检索 |
| `get_paper_section` | 获取指定章节全部段落 |
| `get_references` | 获取参考文献列表 |
| `get_annotations` | 获取某篇论文的所有个人批注 |
| `search_annotations` | **跨全库**搜索个人批注（中文分词 + 子串匹配） |

Agent 的典型推理过程：先用 `search_papers` 找到相关论文，再用 `get_paper_outline` 判断哪个章节相关，再用 `get_paper_section` 精确读取，而不是一次性把所有内容放入 context。

### 三层记忆架构，支持长对话

- **L1 工作记忆**：当前对话上下文
- **L2 压缩记忆**：超过 12 轮后，LLM 自动将旧轮次压缩为摘要，保留最近 3 轮原始内容
- **L3 持久记忆**：整个论文库 + 词库，通过工具随时可查

### 其他细节

- **双语搜索**：中文提问时自动同时搜索中英文关键词，中文人名还会尝试拼音，最大化召回率
- **流式输出**：工具调用阶段非流式保证 JSON 格式准确，最终回答 SSE 流式推送，实时展示每步工具调用过程
- **引用溯源**：每条回答附带引用卡片，点击后打开原文并**自动滚动定位到对应段落**（精确到 block 级别）
- **会话 Minimap**：对话页面右侧缩略导航栏，实时追踪当前位置

---

## 技术栈

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 后端框架 | FastAPI + Uvicorn | 异步 HTTP + WebSocket 实时进度推送 |
| 数据库 | SQLite（WAL 模式） | 零依赖，无需安装任何数据库服务 |
| ORM | SQLAlchemy 2.0 | |
| 异步 | Python asyncio | 无 Celery / Redis，纯线程池并发 |
| 前端框架 | Vue 3 + Vite 5 | |
| UI 组件库 | Element Plus | |
| 公式渲染 | KaTeX | |
| 翻译 LLM | DeepSeek Chat | 段落翻译 + Agent 对话 |
| 视觉 LLM | Qwen3-VL-Flash / Qwen-Image-2.0-Pro | 文字处理 + 图片翻译 |
| PDF 解析 | MinerU API | |

---

## 快速启动（源码）

**系统要求：** Python 3.11+、Node.js 18+

```bash
git clone https://github.com/yuan2001425/z-paper.git
cd z-paper

# Windows
start.bat

# Linux / macOS
bash start.sh
```

首次启动跳转配置页，填写三个 API Key：

| Key | 用途 | 申请 |
| --- | --- | --- |
| DeepSeek API Key | 翻译 + 对话 | https://platform.deepseek.com |
| 通义千问 API Key | 图片识别 + 文本处理 | https://dashscope.console.aliyun.com |
| MinerU API Key | PDF 解析 | https://mineru.net |

---

## 数据说明

所有数据完全保存在本地：`backend/data/zpaper.db`（数据库）和 `backend/uploads/`（文件）。备份这两个目录即可完整迁移。

---

## 许可证

MIT
