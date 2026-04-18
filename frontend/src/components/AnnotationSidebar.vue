<template>
  <div class="annotation-sidebar">
    <div class="sidebar-header">
      <h3>批注</h3>
      <el-button type="primary" size="small" @click="openGlobalForm">
        {{ globalAnnotation ? '编辑全文批注' : '添加全文批注' }}
      </el-button>
    </div>

    <div class="annotation-list">
      <div v-if="!globalAnnotation && inlineAnnotations.length === 0" class="empty-tip">
        暂无批注，选中段落文字或点击按钮添加批注
      </div>

      <!-- 全局批注（置顶，至多一条） -->
      <div v-if="globalAnnotation" class="annotation-item is-global" :class="{ 'is-active': activeId === globalAnnotation.id }" @click="activeId = globalAnnotation.id">
        <div class="ann-scope-tag">
          <el-tag size="small" type="info">全文批注</el-tag>
        </div>
        <div class="ann-content" v-html="renderMd(globalAnnotation.content)" />
        <div class="ann-footer">
          <el-button text size="small" @click.stop="openGlobalForm">编辑</el-button>
          <el-button text size="small" type="danger" @click.stop="deleteAnnotation(globalAnnotation.id)">删除</el-button>
        </div>
      </div>

      <!-- 局部批注（按段落位置排序） -->
      <div
        v-for="ann in inlineAnnotations"
        :key="ann.id"
        class="annotation-item is-inline"
        :class="{ 'is-active': activeId === ann.id }"
        @click="highlight(ann)"
      >
        <div class="ann-scope-tag">
          <el-tag size="small" type="warning">划选批注</el-tag>
        </div>
        <div class="ann-content" v-html="renderMd(ann.content)" />
        <div v-if="ann.selected_text" class="ann-quote">
          "{{ ann.selected_text.slice(0, 60) }}{{ ann.selected_text.length > 60 ? '…' : '' }}"
        </div>
        <div class="ann-footer">
          <el-button text size="small" @click.stop="openInlineEditForm(ann)">编辑</el-button>
          <el-button text size="small" type="danger" @click.stop="deleteAnnotation(ann.id)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- 全局批注弹窗（新建 / 编辑复用） -->
    <el-dialog v-model="showGlobalForm" :title="globalAnnotation ? '编辑全文批注' : '添加全文批注'" width="720px" append-to-body>
      <MdEditor v-model="editContent" />
      <template #footer>
        <el-button @click="showGlobalForm = false">取消</el-button>
        <el-button type="primary" @click="submitGlobal">保存</el-button>
      </template>
    </el-dialog>

    <!-- 局部批注弹窗（新建 / 编辑复用） -->
    <el-dialog v-model="showInlineForm" :title="editingInline ? '编辑批注' : '添加批注'" width="720px" append-to-body>
      <div v-if="pendingInlineInfo && !editingInline" class="selected-preview">
        选中文字：<em>{{ pendingInlineInfo.selectedText?.slice(0, 80) }}</em>
      </div>
      <div v-if="editingInline?.selected_text" class="selected-preview">
        选中文字：<em>{{ editingInline.selected_text.slice(0, 80) }}</em>
      </div>
      <MdEditor v-model="editContent" style="margin-top:12px" />
      <template #footer>
        <el-button @click="showInlineForm = false">取消</el-button>
        <el-button type="primary" @click="submitInline">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/index.js'
import MdEditor from './MdEditor.vue'

const props = defineProps({
  resultId: String,
  contentRef: Object,
})

const annotations = ref([])
const activeId = ref(null)

// ── 派生状态 ──────────────────────────────────────────────────────────────────

const globalAnnotation = computed(() =>
  annotations.value.find(a => a.scope === 'global') ?? null
)

const inlineAnnotations = computed(() => {
  const inline = annotations.value.filter(a => a.scope === 'inline')
  // 按 block_id 中的数字索引排序（p-3, h-1 等）
  return inline.slice().sort((a, b) => {
    const na = parseInt((a.block_id || '').replace(/\D+/g, '') || '0', 10)
    const nb = parseInt((b.block_id || '').replace(/\D+/g, '') || '0', 10)
    return na - nb
  })
})

// ── 弹窗状态 ──────────────────────────────────────────────────────────────────

const showGlobalForm = ref(false)
const showInlineForm = ref(false)
const editContent = ref('')
const pendingInlineInfo = ref(null)  // { blockId, selectedText } — 新建时使用
const editingInline = ref(null)      // Annotation 对象 — 编辑时使用

// ── 打开弹窗 ──────────────────────────────────────────────────────────────────

function openGlobalForm() {
  editContent.value = globalAnnotation.value?.content ?? ''
  showGlobalForm.value = true
}

// 由 TranslationViewer 通过 ref 调用
function openInlineForm(info) {
  const existing = annotations.value.find(
    a => a.scope === 'inline' && a.block_id === info.blockId
  )
  if (existing) {
    // 该段落已有批注 → 编辑模式
    editingInline.value = existing
    pendingInlineInfo.value = null
  } else {
    // 新建模式
    editingInline.value = null
    pendingInlineInfo.value = info
  }
  editContent.value = existing?.content ?? ''
  showInlineForm.value = true
}

function openInlineEditForm(ann) {
  editingInline.value = ann
  pendingInlineInfo.value = null
  editContent.value = ann.content
  showInlineForm.value = true
}

defineExpose({ openInlineForm })

// ── 提交 ──────────────────────────────────────────────────────────────────────

async function submitGlobal() {
  if (!editContent.value.trim()) return
  if (globalAnnotation.value) {
    await api.patch(`/results/${props.resultId}/annotations/${globalAnnotation.value.id}`, {
      content: editContent.value,
    })
  } else {
    await api.post(`/results/${props.resultId}/annotations`, {
      scope: 'global',
      content: editContent.value,
    })
  }
  showGlobalForm.value = false
  editContent.value = ''
  await fetchAnnotations()
  ElMessage.success('批注已保存')
}

async function submitInline() {
  if (!editContent.value.trim()) return
  if (editingInline.value) {
    // 更新已有批注
    await api.patch(`/results/${props.resultId}/annotations/${editingInline.value.id}`, {
      content: editContent.value,
    })
  } else {
    // 新建
    await api.post(`/results/${props.resultId}/annotations`, {
      scope: 'inline',
      content: editContent.value,
      block_id: pendingInlineInfo.value?.blockId,
      selected_text: pendingInlineInfo.value?.selectedText,
    })
  }
  showInlineForm.value = false
  editContent.value = ''
  editingInline.value = null
  pendingInlineInfo.value = null
  await fetchAnnotations()
  ElMessage.success('批注已保存')
}

// ── 其他操作 ──────────────────────────────────────────────────────────────────

async function deleteAnnotation(annotationId) {
  await api.delete(`/results/${props.resultId}/annotations/${annotationId}`)
  annotations.value = annotations.value.filter(a => a.id !== annotationId)
  ElMessage.success('批注已删除')
}

function highlight(ann) {
  activeId.value = ann.id
  if (!ann.block_id) return
  const el = document.querySelector(`[data-block-id="${ann.block_id}"]`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('highlighted')
    setTimeout(() => el.classList.remove('highlighted'), 3000)
  }
}

function renderMd(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code class="ann-code">$1</code>')
    .replace(/\n/g, '<br>')
}

onMounted(fetchAnnotations)

async function fetchAnnotations() {
  const res = await api.get(`/results/${props.resultId}/annotations`)
  annotations.value = res.data
}
</script>

<style scoped>
.annotation-sidebar { padding: 8px; height: 100%; display: flex; flex-direction: column; }
.sidebar-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-shrink: 0; }
.sidebar-header h3 { margin: 0; font-size: 0.95rem; }
.annotation-list { flex: 1; overflow-y: auto; min-height: 0; }
.annotation-item { border: 1px solid #e4e7ed; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; cursor: pointer; transition: border-color 0.2s; }
.annotation-item:hover, .annotation-item.is-active { border-color: #409eff; }
.annotation-item.is-inline { border-left: 3px solid #e6a23c; }
.annotation-item.is-global { border-left: 3px solid #909399; }
.ann-scope-tag { margin-bottom: 6px; }
.ann-content { color: #303133; font-size: 0.88rem; line-height: 1.6; word-break: break-word; }
.ann-quote { color: #909399; font-size: 0.8rem; font-style: italic; margin-top: 6px; border-left: 2px solid #e4e7ed; padding-left: 8px; }
.ann-footer { display: flex; justify-content: flex-end; gap: 4px; margin-top: 6px; }
.selected-preview { color: #606266; font-size: 0.9rem; margin-bottom: 4px; }
.selected-preview em { font-style: italic; color: #303133; }
.empty-tip { color: #c0c4cc; font-size: 0.85rem; text-align: center; padding: 24px 8px; line-height: 1.6; }
</style>

<style>
.highlighted { background: #fef9e7 !important; transition: background 0.5s; }
.ann-code { background: #f5f7fa; padding: 1px 4px; border-radius: 3px; font-family: monospace; font-size: 0.85em; }
</style>
