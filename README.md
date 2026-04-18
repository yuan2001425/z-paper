# z-paper

> 本地部署的学术论文管理与翻译工具，专为个人研究者设计。

将英文 PDF 论文一键翻译为中英双栏格式，并通过 AI 知识库对话系统与自己的论文库直接交流。所有数据保存在本地，不依赖任何外部数据库或消息队列服务。

---

## 核心亮点

### 📄 智能 PDF 翻译流水线

- **MinerU 深度解析**：识别多栏布局、数学公式、表格、图注等复杂学术排版，将 PDF 转化为结构化 Markdown
- **六阶段并行流水线**：解析 → 文本清理（8 并发）→ 术语提取 → 结构分类 → 段落翻译（8 并发）→ 图片翻译（3 并发）
- **术语审查检查点**：流水线在翻译前自动暂停，提取论文中的领域专有名词请用户确认译法，确认后保存到词库并自动继续
- **图片文字翻译**：图表、示意图、截图中的文字也可翻译（Qwen-Image-2.0-Pro），支持单张按需翻译
- **中文论文归档**：中文论文走独立通道，保留结构化索引但跳过翻译

### 📖 双栏阅读器

- 英文原文 / 中文译文左右并排，随时切换单栏模式
- 数学公式用 KaTeX 实时渲染
- 图片支持放大查看（Lightbox），原图与译图对比
- 参考文献列表独立展示，文内引用标注可点击跳转

### 🖊 批注系统

- **全文批注**：对整篇论文添加总体笔记
- **划选批注**：精确到具体段落的选中文字批注，保存段落位置（block_id + 文字偏移量）
- 批注侧边栏与正文同步滚动，点击批注自动定位到对应段落

### 🗂️ 双层词汇系统

- **个人词库**：自定义术语，支持三种策略：`正常翻译` / `永不翻译` / `翻译并标注原文`
- **领域词库**：11 个预设学科领域（计算机科学、数学、物理学、生物学等）的公用参考词表
- 翻译时自动融合两层词库（个人词库优先覆盖领域词库），每次翻译快照词库状态以保证可复现

### 🤖 AI 知识库对话

基于工具调用的 Agent，能够跨论文检索、推理与综合回答。

**7 个工具：**

| 工具                   | 功能                                                    |
| ---------------------- | ------------------------------------------------------- |
| `search_papers`      | 在论文元数据（标题/摘要/关键词）中全文搜索              |
| `get_paper_outline`  | 获取论文章节大纲                                        |
| `search_in_paper`    | 在单篇论文全文中关键词检索                              |
| `get_paper_section`  | 获取指定章节全部段落                                    |
| `get_references`     | 获取参考文献列表                                        |
| `get_annotations`    | 获取某篇论文的所有个人批注                              |
| `search_annotations` | **跨全库**搜索个人批注（支持中文分词 + 子串匹配） |

**三层记忆架构：**

- **L1 工作记忆**：当前对话上下文，直接放入 API 请求
- **L2 压缩记忆**：超过 12 轮后，用 LLM 将旧轮次压缩为摘要，保留最近 3 轮原始内容
- **L3 持久记忆**：整个论文库 + 词库，通过工具随时可查

**流式输出**：工具调用阶段非流式保证格式准确，最终回答阶段 SSE 流式推送，实时显示打字效果，并实时展示每步工具调用过程。

**引用溯源**：每条 AI 回答附带来源引用卡片，按论文分组展示，点击单条引用在新标签页打开原文并**自动滚动定位到对应段落**（精确到 block）。批注类引用同样可以定位到批注所在段落。

**双语搜索策略**：Agent 被指导对中文查询同时搜索中英文关键词，词表之外的术语由模型自行翻译，中文人名还会尝试拼音形式，最大化召回率。

**会话 Minimap**：对话页面右侧有缩略导航栏，显示整段对话的缩略概览，滚动时蓝色视口指示器实时追踪当前位置，点击任意位置可跳转。

### ⚙️ 零配置启动

- 首次启动自动跳转配置页，填写三个 API Key 后即可使用
- API Key 保存在本地 SQLite 数据库，立即生效无需重启
- 路由守卫全程拦截：未完成配置时，任何页面都会被重定向到配置页

---

## 技术栈

| 层            | 技术                                   |
| ------------- | -------------------------------------- |
| 后端框架      | FastAPI + Uvicorn                      |
| 数据库        | SQLite（WAL 模式，无需安装数据库服务） |
| ORM           | SQLAlchemy 2.0                         |
| 异步          | Python asyncio（无 Celery / Redis）    |
| HTTP 客户端   | httpx                                  |
| 前端框架      | Vue 3 (Composition API)                |
| 构建工具      | Vite 5                                 |
| UI 组件库     | Element Plus                           |
| 路由 / 状态   | Vue Router 4 + Pinia                   |
| Markdown 渲染 | marked                                 |
| 公式渲染      | KaTeX                                  |
| 翻译 LLM      | DeepSeek Chat                          |
| 视觉 LLM      | Qwen-VL-Max / Qwen-Image-2.0-Pro       |
| PDF 解析      | MinerU API                             |

---

## 系统要求

- Python 3.11+
- Node.js 18+
- 三个 API Key（首次启动后在界面填写）：
  - [DeepSeek API Key](https://platform.deepseek.com)（论文翻译 + 知识库对话）
  - [通义千问 API Key](https://dashscope.console.aliyun.com)（图片识别 + 文本处理）
  - [MinerU API Key](https://mineru.net)（PDF 解析）

---

## 快速启动

```bash
# 克隆项目
git clone <repo-url>
cd z-paper

# Windows 一键启动
# 自动创建虚拟环境、安装依赖、初始化数据库、启动前后端
start.bat
```

启动后浏览器会自动打开，首次使用跳转配置页填写 API Key 即可。

| 地址                       | 说明             |
| -------------------------- | ---------------- |
| http://localhost:3000      | 前端界面         |
| http://localhost:8000      | 后端 API         |
| http://localhost:8000/docs | Swagger API 文档 |

---

## 项目结构

```
z-paper/
├── start.bat                        # Windows 一键启动
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口，WebSocket 实时进度
│   │   ├── config.py                # 三层配置（.env → DB → 内存）
│   │   ├── api/
│   │   │   ├── papers.py            # 上传、元数据提取、搜索、CRUD
│   │   │   ├── jobs.py              # 任务管理、术语审查流程
│   │   │   ├── results.py           # 译文查询、批注 CRUD、按需图片翻译
│   │   │   ├── chat.py              # 知识库对话（流式 SSE + 普通）
│   │   │   ├── glossary.py          # 个人词库 CRUD
│   │   │   ├── domain_glossary.py   # 领域词库 CRUD
│   │   │   └── settings.py          # API Key 配置读写与状态检查
│   │   ├── models/                  # 11 个 SQLAlchemy 模型
│   │   │   ├── paper.py             # 论文元数据
│   │   │   ├── job.py               # 翻译任务状态机
│   │   │   ├── result.py            # 译文结构 JSON
│   │   │   ├── annotation.py        # 全文/划选批注
│   │   │   ├── user_glossary.py     # 个人词库
│   │   │   ├── domain_glossary.py   # 领域词库
│   │   │   ├── chat.py              # 对话会话 + 消息
│   │   │   ├── app_config.py        # 运行时 Key-Value 配置
│   │   │   └── ...
│   │   ├── services/
│   │   │   ├── pipeline.py          # 翻译六阶段流水线编排
│   │   │   ├── chat_agent.py        # DeepSeek Agent（工具循环 + 记忆压缩）
│   │   │   ├── chat_tools.py        # 7 个知识库工具实现
│   │   │   └── image_translation.py # 图片文字翻译
│   │   └── translation/
│   │       ├── pipeline_core.py     # LLM 调用、分块、分类、翻译核心
│   │       ├── metadata_extractor.py
│   │       └── title_translator.py
│   └── data/                        # SQLite 数据库（zpaper.db）
├── frontend/
│   └── src/
│       ├── views/
│       │   ├── Home.vue             # 论文库主页
│       │   ├── TranslateUpload.vue  # 上传英文论文
│       │   ├── ChineseUpload.vue    # 上传中文论文（归档）
│       │   ├── JobList.vue          # 任务队列 + 术语审查弹窗
│       │   ├── ResultReader.vue     # 双栏阅读器 + 批注侧边栏
│       │   ├── UserGlossary.vue     # 个人词库管理
│       │   ├── ChatView.vue         # 知识库对话（minimap 导航）
│       │   ├── PaperSearch.vue      # 全文搜索
│       │   └── SettingsView.vue     # API Key 配置
│       └── components/
│           ├── TranslationViewer.vue   # 双栏内容渲染，block 精确定位
│           ├── AnnotationSidebar.vue   # 批注侧边栏
│           ├── AppHeader.vue
│           └── UploadTypeModal.vue
```

---

## 主要工作流

### 翻译一篇英文论文

```
上传 PDF
  → MinerU 解析结构
  → 并行清理文本 & 提取领域术语
  → [暂停] 用户确认新术语的译法，保存到个人词库
  → 并行翻译段落（DeepSeek）+ 图片（Qwen）
  → 生成双栏译文，可立即阅读 & 添加批注
```

### 与知识库对话

```
提问（中文或英文均可）
  → Agent 自动搜索相关论文 & 个人批注（中英双语 + 拼音）
  → 工具调用过程实时展示（可折叠）
  → 流式输出回答（Markdown 渲染）
  → 引用按论文分组，点击可跳转到原文对应段落
```

---

## 数据说明

所有数据完全保存在本地：

- 数据库：`backend/data/zpaper.db`（SQLite）
- 上传文件：`backend/uploads/`

备份这两个目录即可完整迁移数据。

---

## 许可证

MIT
