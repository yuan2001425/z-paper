<template>
  <div class="chat-layout">
    <AppHeader />
    <div class="chat-body">

      <!-- 左侧：会话列表 -->
      <aside class="session-panel">
        <div class="session-panel-header">
          <span class="session-panel-title">对话历史</span>
          <el-button size="small" type="primary" @click="newSession">+ 新对话</el-button>
        </div>
        <div class="session-list">
          <div
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: currentSessionId === s.id }"
            @click="switchSession(s.id)"
          >
            <div class="session-item-title">{{ s.title }}</div>
            <div class="session-item-meta">{{ formatTime(s.updated_at) }}</div>
            <el-button
              class="session-delete-btn"
              size="small" text type="danger"
              @click.stop="deleteSession(s.id)"
            >删</el-button>
          </div>
          <div v-if="sessions.length === 0" class="session-empty">暂无对话</div>
        </div>
      </aside>

      <!-- 右侧：对话区 -->
      <main class="chat-main">
        <!-- 消息列表 -->
        <div class="messages-area" ref="messagesEl">
          <div v-if="messages.length === 0" class="chat-welcome">
            <div class="welcome-icon">📚</div>
            <h2>知识库对话</h2>
            <p>向您的论文库提问，Agent 会自动搜索相关论文和您的批注来回答。</p>
            <div class="welcome-examples">
              <div class="example-chip" @click="fillInput('我的论文库里有哪些主题？')">我的论文库里有哪些主题？</div>
              <div class="example-chip" @click="fillInput('总结一下最近读的论文的核心发现')">总结一下最近读的论文的核心发现</div>
              <div class="example-chip" @click="fillInput('我在哪些地方做过批注？')">我在哪些地方做过批注？</div>
            </div>
          </div>

          <div v-for="msg in messages" :key="msg.id" class="message-row" :class="msg.role" :data-msg-id="msg.id">

            <!-- 用户消息 -->
            <div v-if="msg.role === 'user'" class="bubble user-bubble">
              {{ msg.content }}
            </div>

            <!-- AI 回复 -->
            <div v-else class="bubble ai-bubble">
              <!-- 等待第一个 token（无工具、无内容） -->
              <div v-if="msg.isStreaming && !msg.tool_calls?.length && !msg.content" class="thinking">
                <span class="dot" /><span class="dot" /><span class="dot" />
              </div>

              <!-- 工具调用过程（可折叠） -->
              <div v-if="msg.tool_calls?.length" class="tool-steps" :class="{ expanded: expandedSteps[msg.id] }">
                <div class="tool-steps-header" @click="toggleSteps(msg.id)">
                  <el-icon class="steps-arrow"><ArrowRight /></el-icon>
                  <span>
                    查询了 {{ toolSummary(msg.tool_calls) }}
                    <span v-if="msg.isStreaming && msg.tool_calls.some(t => t.running)" class="tool-running-badge">运行中…</span>
                  </span>
                </div>
                <div v-if="expandedSteps[msg.id]" class="tool-steps-body">
                  <div v-for="(tc, i) in msg.tool_calls" :key="i" class="tool-step-item">
                    <el-icon v-if="tc.running" class="tool-running-icon"><Loading /></el-icon>
                    <span v-else class="tool-done-dot">✓</span>
                    <span class="tool-name">{{ toolLabel(tc.name) }}</span>
                    <span class="tool-args">{{ formatArgs(tc.args) }}</span>
                  </div>
                </div>
              </div>

              <!-- 回答正文（Markdown 简单渲染） + 流式光标 -->
              <div v-if="msg.content" class="ai-answer">
                <span v-html="renderAnswer(msg.content)" />
                <span v-if="msg.isStreaming" class="stream-cursor" />
              </div>

              <!-- 引用卡片（按论文分组） -->
              <div v-if="msg.citations?.length" class="citations-block">
                <div class="citations-toggle" @click="toggleCitations(msg.id)">
                  <el-icon class="steps-arrow" :class="{ 'is-expanded': expandedCitations[msg.id] }"><ArrowRight /></el-icon>
                  <span>{{ msg.citations.length }} 条引用 · 来自 {{ groupCitations(msg.citations).length }} 篇论文</span>
                </div>
                <div v-if="expandedCitations[msg.id]" class="citations-list">
                  <div v-for="group in groupCitations(msg.citations)" :key="group.paper_id" class="paper-cit-card">
                    <!-- 论文标题行 -->
                    <div class="pcc-header" @click="openPaperNewTab(group.paper_id)">
                      <div class="pcc-title">{{ group.paper_title }}</div>
                      <div class="pcc-meta">
                        <span v-if="group.author_label" class="pcc-authors">{{ group.author_label }}</span>
                        <span v-if="group.year" class="pcc-year">{{ group.year }}</span>
                        <span class="pcc-count">{{ group.items.length }} 条</span>
                      </div>
                    </div>
                    <!-- 条目列表 -->
                    <div class="pcc-items">
                      <div
                        v-for="(item, idx) in group.items"
                        :key="idx"
                        class="pcc-item"
                        @click.stop="openCitationItem(item)"
                      >
                        <span class="pcc-item-icon">{{ item.type === 'annotation' ? '🖊' : '📄' }}</span>
                        <div class="pcc-item-body">
                          <div v-if="item.heading" class="pcc-item-heading">§ {{ item.heading }}</div>
                          <div v-if="item.selected_text" class="pcc-item-selected">"{{ item.selected_text.slice(0, 60) }}"</div>
                          <div class="pcc-item-text">{{ item.text?.slice(0, 100) }}{{ item.text?.length > 100 ? '…' : '' }}</div>
                        </div>
                        <span class="pcc-item-arrow">↗</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="input-area">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="2"
            :autosize="{ minRows: 1, maxRows: 5 }"
            placeholder="向您的论文库提问… (Ctrl+Enter 发送)"
            resize="none"
            :disabled="streaming"
            @keydown.ctrl.enter.prevent="sendMessage"
          />
          <el-button
            type="primary"
            :loading="streaming"
            :disabled="!inputText.trim() || streaming"
            @click="sendMessage"
          >
            发送
          </el-button>
        </div>
      </main>

      <!-- 右侧缩略导航（VS Code 风格） -->
      <aside class="chat-minimap" v-show="messages.length > 0">
        <!-- 比例色块：每条消息一个，高度按内容长度等比分配 -->
        <div class="mm-content" ref="mmContentRef">
          <div
            v-for="msg in messages"
            :key="msg.id"
            class="mm-block"
            :class="msg.role"
            :title="msgPreview(msg)"
            @click="jumpToMessage(msg.id)"
          >
            <span class="mm-text">{{ msgPreview(msg) }}</span>
          </div>
        </div>
        <!-- 视口指示器：半透明蓝框，指示当前可见区域位置 -->
        <div
          class="mm-viewport"
          :style="{
            top:    mmViewportTopPct    + '%',
            height: mmViewportHeightPct + '%',
          }"
        />
      </aside>

    </div>
  </div>

</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Loading } from '@element-plus/icons-vue'
import { marked } from 'marked'
import AppHeader from '@/components/AppHeader.vue'
import api from '@/api/index.js'

// marked 配置：换行保留，不自动加段落包裹
marked.setOptions({ breaks: true, gfm: true })

const sessions         = ref([])
const currentSessionId = ref(null)
const messages         = ref([])
const inputText        = ref('')
const streaming        = ref(false)   // true while SSE stream is open
const messagesEl       = ref(null)
const paperTitles      = reactive({}) // paper_id → title cache

const expandedSteps     = reactive({})
const expandedCitations = reactive({})

// ── Minimap ────────────────────────────────────────────────────────────────────
const mmContentRef        = ref(null)
const mmViewportTopPct    = ref(0)
const mmViewportHeightPct = ref(100)

function updateMmViewport() {
  const msg = messagesEl.value
  const mm  = mmContentRef.value
  if (!msg || !mm) return

  // 把主对话区的滚动进度同步到 mm-content
  const progress = msg.scrollHeight > msg.clientHeight
    ? msg.scrollTop / (msg.scrollHeight - msg.clientHeight)
    : 0
  mm.scrollTop = progress * Math.max(0, mm.scrollHeight - mm.clientHeight)

  // 视口指示器：相对于 mm-content 滚动空间的百分比
  if (mm.scrollHeight === 0) return
  mmViewportTopPct.value    = (mm.scrollTop / mm.scrollHeight) * 100
  mmViewportHeightPct.value = Math.min(100, (mm.clientHeight / mm.scrollHeight) * 100)
}

function onMessagesScroll() {
  updateMmViewport()
}

function jumpToMessage(msgId) {
  const el = messagesEl.value?.querySelector(`[data-msg-id="${msgId}"]`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function msgPreview(msg) {
  if (msg.role === 'user') return msg.content?.slice(0, 40) || '…'
  return msg.content?.slice(0, 40) || (msg.tool_calls?.length ? '查询中…' : '…')
}

// 消息数量变化（新消息到达）时刷新视口指示器
watch(() => messages.value.length, async () => {
  await nextTick()
  updateMmViewport()
})


// ── 会话管理 ──────────────────────────────────────────────────────────────────

async function loadSessions() {
  const res = await api.get('/chat/sessions/list')
  sessions.value = res.data
}

async function switchSession(id) {
  if (streaming.value) return
  currentSessionId.value = id
  const res = await api.get(`/chat/sessions/${id}/messages`)
  messages.value = res.data.messages
  await nextTick()
  scrollToBottom()
}

function newSession() {
  if (streaming.value) return
  currentSessionId.value = null
  messages.value = []
}

async function deleteSession(id) {
  await ElMessageBox.confirm('确认删除此对话？', '提示', { type: 'warning' })
  await api.delete(`/chat/sessions/${id}`)
  if (currentSessionId.value === id) newSession()
  await loadSessions()
}

// ── 流式发消息 ────────────────────────────────────────────────────────────────

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || streaming.value) return

  // 乐观渲染用户消息
  messages.value.push({ id: `local-${Date.now()}`, role: 'user', content: text })
  inputText.value = ''
  streaming.value = true
  await nextTick()
  scrollToBottom()

  // 创建 AI 消息占位（响应式对象，后续原地更新）
  const aiMsgId = `ai-${Date.now()}`
  const aiMsg = reactive({
    id:          aiMsgId,
    role:        'assistant',
    content:     '',
    tool_calls:  [],
    citations:   [],
    isStreaming: true,
  })
  messages.value.push(aiMsg)

  try {
    const resp = await fetch('/api/v1/chat/sessions/stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        message:    text,
        session_id: currentSessionId.value || undefined,
      }),
    })

    if (!resp.ok) {
      ElMessage.error(`请求失败 (${resp.status})`)
      messages.value.pop()
      return
    }

    const reader  = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() // 保留不完整的最后一行

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        try {
          handleStreamEvent(JSON.parse(raw), aiMsg)
        } catch { /* ignore parse errors */ }
      }
    }
  } catch (e) {
    ElMessage.error('连接中断，请重试')
    // 移除空的 AI 占位
    if (!aiMsg.content) messages.value.pop()
  } finally {
    aiMsg.isStreaming = false
    streaming.value  = false
    // 流结束后自动折叠工具调用面板
    expandedSteps[aiMsg.id] = false
    await nextTick()
    scrollToBottom()
  }
}

function handleStreamEvent(event, aiMsg) {
  switch (event.type) {
    case 'tool_start':
      aiMsg.tool_calls.push({ name: event.name, args: event.args, running: true })
      // 自动展开工具步骤面板
      expandedSteps[aiMsg.id] = true
      nextTick(scrollToBottom)
      break

    case 'tool_done': {
      const last = [...aiMsg.tool_calls].reverse().find(t => t.running)
      if (last) last.running = false
      break
    }

    case 'answer_chunk':
      aiMsg.content += event.content
      nextTick(scrollToBottom)
      break

    case 'done':
      aiMsg.citations = dedupCitations(event.citations || [])
      if (!currentSessionId.value) {
        currentSessionId.value = event.session_id
        loadSessions()
      }
      for (const c of event.citations || []) {
        if (c.paper_id && c.paper_title) paperTitles[c.paper_id] = c.paper_title
      }
      break

    case 'error':
      ElMessage.error(event.message || 'Agent 出错')
      break
  }
}

function fillInput(text) {
  inputText.value = text
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function scrollToBottom() {
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function toggleSteps(id) {
  expandedSteps[id] = !expandedSteps[id]
}

function toggleCitations(id) {
  expandedCitations[id] = !expandedCitations[id]
}

function toolSummary(toolCalls) {
  const total = toolCalls.length
  const names = [...new Set(toolCalls.map(t => toolLabel(t.name)))]
  const label = names.slice(0, 2).join('、') + (names.length > 2 ? ' 等' : '')
  return total > 1 ? `${label}（共 ${total} 次）` : label
}

function toolLabel(name) {
  const map = {
    search_papers:         '搜索论文',
    get_paper_outline:     '查看大纲',
    search_in_paper:       '搜索全文',
    get_paper_section:     '读取章节',
    get_references:        '查看参考文献',
    get_annotations:       '读取批注',
    search_annotations:    '搜索批注',
    search_across_papers:  '跨库搜索',
    get_paragraph_context: '读取上下文',
    get_paper_metadata:    '获取论文信息',
    search_chat_history:   '搜索历史对话',
    query_database:        '数据库查询',
  }
  return map[name] || name
}

function formatArgs(args) {
  if (!args) return ''
  const parts = []
  if (args.query)                    parts.push(`"${args.query}"`)
  if (args.keyword)                  parts.push(`"${args.keyword}"`)
  if (args.heading)                  parts.push(`§ ${args.heading}`)
  if (args.section)                  parts.push(`节: ${args.section}`)
  if (args.block_idx !== undefined)  parts.push(`段落 #${args.block_idx}`)
  if (args.paper_id && !args.query && !args.keyword && !args.heading)
    parts.push(`论文 ${String(args.paper_id).slice(0, 8)}…`)
  return parts.join(' · ')
}

function renderAnswer(text) {
  if (!text) return ''
  return marked.parse(text)
}

function formatTime(t) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function openPaperNewTab(paperId) {
  if (paperId) window.open(`/results/by-paper/${paperId}`, '_blank')
}

function openCitationItem(item) {
  if (!item?.paper_id) return
  let url
  if (item.type === 'annotation' && item.block_id) {
    // 批注用 block_id（"p-N" 格式，对应 data-block-id）
    url = `/results/by-paper/${item.paper_id}?bid=${encodeURIComponent(item.block_id)}`
  } else if (item.block_idx >= 0) {
    // 段落用原始数组下标（对应 data-block-idx）
    url = `/results/by-paper/${item.paper_id}?block=${item.block_idx}`
  } else {
    url = `/results/by-paper/${item.paper_id}`
  }
  window.open(url, '_blank')
}

function dedupCitations(citations) {
  const seen = new Set()
  return citations.filter(c => {
    const key = `${c.type}:${c.paper_id}:${c.block_idx ?? c.text?.slice(0, 60)}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function groupCitations(citations) {
  const groups = new Map()
  for (const c of citations) {
    const key = c.paper_id || '__unknown__'
    if (!groups.has(key)) {
      const authors = c.authors || []
      let authorLabel = ''
      if (authors.length === 1)      authorLabel = authors[0]
      else if (authors.length === 2) authorLabel = authors.join(' & ')
      else if (authors.length > 2)   authorLabel = authors[0] + ' et al.'
      groups.set(key, {
        paper_id:     c.paper_id,
        paper_title:  c.paper_title || paperTitles[c.paper_id] || '未知论文',
        author_label: authorLabel,
        year:         c.year,
        items:        [],
      })
    }
    groups.get(key).items.push(c)
  }
  return [...groups.values()]
}

onMounted(() => {
  loadSessions()
  nextTick(() => {
    messagesEl.value?.addEventListener('scroll', onMessagesScroll, { passive: true })
  })
})

onUnmounted(() => {
  messagesEl.value?.removeEventListener('scroll', onMessagesScroll)
})
</script>

<style scoped>
.chat-layout { height: 100vh; display: flex; flex-direction: column; background: #f5f7fa; overflow: hidden; }

.chat-body {
  flex: 1;
  display: flex;
  min-height: 0;          /* 关键：让子项可以收缩到容器以内 */
  overflow: hidden;
}

/* ── 左侧会话列表 ──────────────────────────────────────────────── */
.session-panel {
  width: 220px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.session-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.session-panel-title { font-size: 0.88rem; font-weight: 600; color: #303133; }

.session-list { flex: 1; overflow-y: auto; padding: 4px 0; }

.session-item {
  position: relative;
  padding: 8px 36px 8px 12px;
  cursor: pointer;
  transition: background 0.15s;
  border-left: 3px solid transparent;
}
.session-item:hover { background: #f5f7fa; }
.session-item.active { background: #ecf5ff; border-left-color: #409eff; }

.session-item-title {
  font-size: 0.82rem;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-item-meta { font-size: 0.72rem; color: #c0c4cc; margin-top: 2px; }
.session-delete-btn { position: absolute; right: 4px; top: 50%; transform: translateY(-50%); opacity: 0; }
.session-item:hover .session-delete-btn { opacity: 1; }

.session-empty { padding: 24px 12px; font-size: 0.82rem; color: #c0c4cc; text-align: center; }

/* ── 中间对话区 ───────────────────────────────────────────────── */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
}

/* 欢迎页 */
.chat-welcome { text-align: center; padding: 60px 24px 20px; color: #606266; }
.welcome-icon { font-size: 3rem; margin-bottom: 16px; }
.chat-welcome h2 { font-size: 1.4rem; color: #303133; margin-bottom: 8px; }
.chat-welcome p { font-size: 0.9rem; margin-bottom: 24px; }
.welcome-examples { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
.example-chip {
  padding: 6px 14px;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 16px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: background 0.15s;
}
.example-chip:hover { background: #d9ecff; }

/* 消息行 */
.message-row { display: flex; }
.message-row.user { justify-content: flex-end; }
.message-row.assistant { justify-content: flex-start; }

.bubble {
  max-width: 72%;
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 0.9rem;
  line-height: 1.7;
  word-break: break-word;
}

.user-bubble {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-bottom-left-radius: 4px;
  max-width: 80%;
}

/* 思考动画（嵌在 ai-bubble 内） */
.thinking { padding: 4px 2px 2px; }
.dot { display: inline-block; width: 6px; height: 6px; background: #c0c4cc; border-radius: 50%; margin: 0 2px; animation: bounce 1.2s infinite; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce { 0%, 80%, 100% { transform: scale(0.8); } 40% { transform: scale(1.2); } }

/* 流式光标 */
.stream-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: #409eff;
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 1s step-end infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* 工具调用折叠 */
.tool-steps {
  margin-bottom: 8px;
  background: #f5f7fa;
  border-radius: 6px;
  overflow: hidden;
  font-size: 0.8rem;
}
.tool-steps-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  cursor: pointer;
  color: #909399;
  user-select: none;
}
.tool-steps-header:hover { color: #606266; }
.steps-arrow { font-size: 12px; transition: transform 0.2s; flex-shrink: 0; }
.tool-steps.expanded .steps-arrow { transform: rotate(90deg); }
.tool-steps-body { padding: 4px 10px 8px; border-top: 1px solid #e4e7ed; }
.tool-step-item { display: flex; gap: 8px; padding: 2px 0; }
.tool-name { color: #409eff; font-weight: 500; white-space: nowrap; }
.tool-args { color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-running-badge { color: #e6a23c; font-size: 0.75rem; margin-left: 6px; }
.tool-running-icon { color: #e6a23c; animation: spin 1s linear infinite; flex-shrink: 0; }
.tool-done-dot { color: #67c23a; font-size: 0.75rem; flex-shrink: 0; width: 14px; text-align: center; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 回答正文 Markdown 样式 */
.ai-answer { padding: 0; line-height: 1.75; }
.ai-answer :deep(p)  { margin: 0 0 0.6em; }
.ai-answer :deep(p:last-child) { margin-bottom: 0; }
.ai-answer :deep(h1), .ai-answer :deep(h2) { font-size: 1.1em; font-weight: 700; margin: 0.8em 0 0.4em; }
.ai-answer :deep(h3), .ai-answer :deep(h4) { font-size: 1em;   font-weight: 700; margin: 0.6em 0 0.3em; }
.ai-answer :deep(h5), .ai-answer :deep(h6) { font-size: 0.95em; font-weight: 600; margin: 0.5em 0 0.2em; }
.ai-answer :deep(ul), .ai-answer :deep(ol) { padding-left: 1.4em; margin: 0.4em 0 0.6em; }
.ai-answer :deep(li) { margin: 0.15em 0; }
.ai-answer :deep(strong) { font-weight: 600; }
.ai-answer :deep(em)     { font-style: italic; }
.ai-answer :deep(code)   { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; font-family: monospace; font-size: 0.85em; }
.ai-answer :deep(pre)    { background: #f0f2f5; border-radius: 6px; padding: 10px 14px; overflow-x: auto; margin: 0.5em 0; }
.ai-answer :deep(pre code) { background: none; padding: 0; font-size: 0.85em; }
.ai-answer :deep(blockquote) { border-left: 3px solid #dcdfe6; padding-left: 10px; color: #909399; margin: 0.4em 0; }
.ai-answer :deep(hr) { border: none; border-top: 1px solid #e4e7ed; margin: 0.6em 0; }
.ai-answer :deep(table) { border-collapse: collapse; width: 100%; margin: 0.5em 0; font-size: 0.88em; }
.ai-answer :deep(th), .ai-answer :deep(td) { border: 1px solid #e4e7ed; padding: 5px 10px; text-align: left; }
.ai-answer :deep(th) { background: #f5f7fa; font-weight: 600; }

/* 引用区 */
.citations-block { margin-top: 10px; border-top: 1px solid #f0f0f0; padding-top: 8px; }
.citations-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.78rem;
  color: #909399;
  cursor: pointer;
  user-select: none;
}
.citations-toggle:hover { color: #606266; }
.citations-toggle .steps-arrow { transition: transform 0.2s; }
.citations-toggle .steps-arrow.is-expanded { transform: rotate(90deg); }
.citations-list { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }

/* 按论文分组的引用卡片 */
.paper-cit-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  font-size: 0.78rem;
}

.pcc-header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 12px;
  background: #f5f7fa;
  cursor: pointer;
  transition: background 0.15s;
}
.pcc-header:hover { background: #ecf5ff; }

.pcc-title {
  font-weight: 600;
  color: #303133;
  font-size: 0.82rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pcc-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #909399;
  font-size: 0.73rem;
}
.pcc-year::before { content: '· '; }
.pcc-count {
  margin-left: auto;
  background: #409eff;
  color: #fff;
  border-radius: 10px;
  padding: 0 6px;
  font-size: 0.7rem;
  line-height: 1.6;
}

/* 条目列表 */
.pcc-items { padding: 4px 0; }

.pcc-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 6px 12px;
  cursor: pointer;
  transition: background 0.12s;
  border-top: 1px solid #f5f5f5;
}
.pcc-item:hover { background: #f0f9ff; }
.pcc-item:first-child { border-top: none; }

.pcc-item-icon { flex-shrink: 0; font-size: 0.8rem; padding-top: 1px; }

.pcc-item-body { flex: 1; min-width: 0; }
.pcc-item-heading {
  color: #409eff;
  font-size: 0.72rem;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pcc-item-selected {
  color: #e6a23c;
  font-style: italic;
  font-size: 0.72rem;
  margin-bottom: 2px;
}
.pcc-item-text {
  color: #606266;
  line-height: 1.5;
  word-break: break-word;
}

.pcc-item-arrow {
  flex-shrink: 0;
  color: #c0c4cc;
  font-size: 0.8rem;
  padding-top: 2px;
  transition: color 0.12s;
}
.pcc-item:hover .pcc-item-arrow { color: #409eff; }

/* 输入区（固定在对话区底部） */
.input-area {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 24px 16px;
  border-top: 1px solid #e4e7ed;
  background: #fff;
  flex-shrink: 0;
}
.input-area .el-textarea { flex: 1; }

/* ── 右侧缩略导航 ─────────────────────────────────────────────── */
.chat-minimap {
  width: 140px;
  flex-shrink: 0;
  background: #fafafa;
  border-left: 1px solid #e4e7ed;
  position: relative;
  overflow: hidden;
}

/* 内容区：自然高度，隐藏滚动条，由 JS 同步滚动位置 */
.mm-content {
  position: absolute;
  inset: 0;
  overflow-y: scroll;
  background: #fff;
  scrollbar-width: none;       /* Firefox */
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 8px;
  box-sizing: border-box;
}
.mm-content::-webkit-scrollbar { display: none; }

.mm-block {
  border-radius: 4px;
  cursor: pointer;
  padding: 4px 7px;
  flex-shrink: 0;
  transition: filter 0.12s;
}
.mm-block:hover { filter: brightness(0.94); }
.mm-block.user      { background: #ecf5ff; border-left: 3px solid #409eff; }
.mm-block.assistant { background: #f5f7fa; border-left: 3px solid #dcdfe6; }

.mm-text {
  font-size: 9.5px;
  line-height: 1.5;
  word-break: break-all;
  pointer-events: none;
  user-select: none;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.mm-block.user      .mm-text { color: #409eff; }
.mm-block.assistant .mm-text { color: #606266; }

/* 视口指示器：叠在 mm-content 上方 */
.mm-viewport {
  position: absolute;
  left: 0;
  right: 0;
  background: rgba(64, 158, 255, 0.08);
  border-top:    1px solid rgba(64, 158, 255, 0.4);
  border-bottom: 1px solid rgba(64, 158, 255, 0.4);
  pointer-events: none;
  transition: top 0.07s linear, height 0.07s linear;
  min-height: 4px;
}
</style>
