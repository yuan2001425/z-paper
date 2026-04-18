<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <div class="page-header">
        <h2>个性翻译</h2>
        <div class="header-actions">
          <el-select
            v-model="selectedDomain"
            placeholder="全部学科"
            clearable filterable
            style="width:240px"
            @change="onDomainChange"
          >
            <el-option v-for="d in DISCIPLINES" :key="d.value" :label="d.label" :value="d.value" />
          </el-select>
          <el-button type="primary" @click="openAddDialog">+ 添加术语</el-button>
        </div>
      </div>

      <el-alert type="info" :closable="false" style="margin-bottom:16px">
        个性翻译设置会在所有翻译任务中自动应用。每条术语归属一个学科，可在上方切换查看。
      </el-alert>

      <!-- 添加术语对话框 -->
      <el-dialog v-model="addDialogVisible" width="500px" :close-on-click-modal="false">
        <template #header>
          <div style="display:flex;align-items:center;justify-content:space-between;padding-right:24px">
            <span>手动添加术语</span>
            <a href="https://dictionary.cambridge.org/zhs/#" target="_blank" style="font-size:0.82rem;color:#409eff;text-decoration:none">📖 剑桥英汉词典</a>
          </div>
        </template>
        <el-form :model="addForm" label-width="90px">
          <el-form-item label="外文术语" required>
            <el-input v-model="addForm.foreign_term" placeholder="原文术语" clearable />
          </el-form-item>
          <el-form-item label="语言">
            <el-select v-model="addForm.source_language" style="width:100%">
              <el-option
                v-for="lang in SUPPORTED_LANGUAGES"
                :key="lang.code"
                :label="lang.disabled ? lang.label + '（即将支持）' : lang.label"
                :value="lang.code"
                :disabled="lang.disabled"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="处理方式">
            <div class="action-options">
              <div
                v-for="opt in STATUS_OPTIONS"
                :key="opt.value"
                class="action-option"
                :class="{ active: addForm.status === opt.value }"
                @click="addForm.status = opt.value"
              >
                <div class="action-option-label">{{ opt.label }}</div>
                <div class="action-option-desc">{{ opt.desc }}</div>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="中文翻译" v-if="addForm.status !== 'never_translate'">
            <el-input
              v-model="addForm.zh_term"
              :placeholder="addForm.status === 'translate_with_annotation' ? '翻译时保留原文' : '输入中文翻译'"
              clearable
            />
          </el-form-item>
          <el-form-item label="所属学科" required>
            <el-select v-model="addForm.domain" placeholder="请选择学科（必填）" filterable style="width:100%">
              <el-option v-for="d in DISCIPLINES" :key="d.value" :label="d.label" :value="d.value" />
            </el-select>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="addDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="submitAddTerm" :loading="addingTerm" :disabled="!addForm.foreign_term.trim()">
            添加
          </el-button>
        </template>
      </el-dialog>

      <!-- 批量操作栏 -->
      <div v-if="selectedIds.length > 0" class="batch-bar">
        <span>已选 {{ selectedIds.length }} 条</span>
        <el-button type="danger" size="small" @click="batchDelete">批量删除</el-button>
        <el-button size="small" @click="tableRef?.clearSelection()">取消选择</el-button>
      </div>

      <el-table
        ref="tableRef"
        :data="terms"
        style="width:100%"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column label="外文术语" prop="foreign_term" width="200" />
        <el-table-column label="语言" width="70">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ langLabel(row.source_language) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="处理方式" width="160">
          <template #default="{ row }">
            <el-select :model-value="row.status" size="small" style="width:148px" @change="(v) => updateStatus(row, v)">
              <el-option v-for="opt in STATUS_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="中文翻译">
          <template #default="{ row }">
            <el-input
              v-if="row.status !== 'never_translate'"
              v-model="row.zh_term"
              size="small"
              :placeholder="row.status === 'translate_with_annotation' ? '中文名（原文）格式' : '中文译文'"
              @blur="updateTerm(row)"
            />
            <span v-else style="color:#909399;font-size:0.85rem">保留原文</span>
          </template>
        </el-table-column>
        <el-table-column label="所属学科" width="150">
          <template #default="{ row }">
            <span style="font-size:0.88rem;color:#606266">{{ domainLabel(row.domain) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" prop="updated_at" width="100">
          <template #default="{ row }">{{ new Date(row.updated_at).toLocaleDateString('zh-CN') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" size="small" text @click="deleteTerm(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 底部哨兵：进入视口时触发加载下一批 -->
      <div ref="sentinelEl" class="load-sentinel">
        <span v-if="loadingMore">
          <el-icon class="is-loading"><Loading /></el-icon> 加载中...
        </span>
        <span v-else-if="!hasMore && terms.length > 0" style="color:#c0c4cc">
          共 {{ total }} 条，已全部加载
        </span>
      </div>

      <div v-if="!loadingMore && !loading && terms.length === 0" style="text-align:center;padding:40px;color:#909399">
        {{ selectedDomain ? '该学科暂无个性翻译条目' : '暂无条目，翻译论文后会自动积累' }}
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import { DISCIPLINES, SUPPORTED_LANGUAGES } from '@/constants/disciplines.js'
import { useDefaultDomain } from '@/composables/useDefaultDomain.js'

const { defaultDomain } = useDefaultDomain()
const STATUS_OPTIONS = [
  { value: 'translate', label: '完全翻译', desc: '直接翻译为中文' },
  { value: 'translate_with_annotation', label: '翻译并标注原文', desc: '翻译时保留原文' },
  { value: 'never_translate', label: '保留原文', desc: '不翻译，原样保留' },
]

const PAGE_SIZE = 50

const tableRef = ref(null)
const sentinelEl = ref(null)
const terms = ref([])
const loading = ref(false)       // 首次加载（表格为空时）
const loadingMore = ref(false)   // 追加加载
const total = ref(0)
const offset = ref(0)
const selectedDomain = ref(defaultDomain.value)
const selectedIds = ref([])

const hasMore = computed(() => terms.value.length < total.value)

const addDialogVisible = ref(false)
const addingTerm = ref(false)
const addForm = ref({ foreign_term: '', zh_term: '', source_language: 'en', domain: defaultDomain.value, status: 'translate' })

function openAddDialog() {
  addForm.value = { foreign_term: '', zh_term: '', source_language: 'en', domain: selectedDomain.value || defaultDomain.value, status: 'translate' }
  addDialogVisible.value = true
}

async function submitAddTerm() {
  if (!addForm.value.foreign_term.trim()) return
  if (!addForm.value.domain) { ElMessage.warning('请选择所属学科'); return }
  addingTerm.value = true
  try {
    const payload = {
      foreign_term: addForm.value.foreign_term.trim(),
      source_language: addForm.value.source_language,
      status: addForm.value.status,
      zh_term: addForm.value.status !== 'never_translate' ? (addForm.value.zh_term.trim() || null) : null,
      domain: addForm.value.domain || null,
    }
    const res = await api.post('/glossary', payload)
    const newItem = res.data
    // 按字母序插入已加载列表，total +1
    const insertAt = terms.value.findIndex(t => t.foreign_term.localeCompare(newItem.foreign_term) > 0)
    if (insertAt === -1) terms.value.push(newItem)
    else terms.value.splice(insertAt, 0, newItem)
    total.value += 1
    offset.value += 1
    addDialogVisible.value = false
    ElMessage.success('术语已添加')
  } catch (err) {
    if (err.response?.status === 409) ElMessage.error('该术语已存在（同语言下外文术语不可重复）')
  } finally {
    addingTerm.value = false
  }
}

function langLabel(code) {
  return SUPPORTED_LANGUAGES.find(l => l.code === code)?.label ?? code
}

function domainLabel(domain) {
  return DISCIPLINES.find(d => d.value === domain)?.label ?? domain ?? '—'
}

// ── 无限滚动 ──────────────────────────────────────────────────────────────────

let observer = null

function setupObserver() {
  if (observer) observer.disconnect()
  observer = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting && hasMore.value && !loadingMore.value) {
      loadMore()
    }
  }, { rootMargin: '100px' })
  if (sentinelEl.value) observer.observe(sentinelEl.value)
}

onUnmounted(() => observer?.disconnect())

// ── 加载逻辑 ──────────────────────────────────────────────────────────────────

async function fetchTerms() {
  // 切换 domain 时重置
  terms.value = []
  offset.value = 0
  total.value = 0
  loading.value = true
  try {
    await _loadPage()
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value) return
  loadingMore.value = true
  try {
    await _loadPage()
  } finally {
    loadingMore.value = false
  }
}

async function _loadPage() {
  const params = { offset: offset.value, limit: PAGE_SIZE }
  if (selectedDomain.value) params.domain = selectedDomain.value
  const res = await api.get('/glossary', { params })
  const { items, total: t } = res.data
  total.value = t
  terms.value.push(...items)
  offset.value += items.length
}

onMounted(async () => {
  await fetchTerms()
  // 首次加载后挂载 Observer（等 DOM 稳定）
  setTimeout(setupObserver, 0)
})

function onDomainChange(val) {
  if (val) defaultDomain.value = val
  fetchTerms().then(() => setTimeout(setupObserver, 0))
}

function onSelectionChange(selection) {
  selectedIds.value = selection.map(r => r.id)
}

async function updateTerm(row) {
  await api.patch(`/glossary/${row.id}`, { zh_term: row.zh_term })
}

async function updateStatus(row, status) {
  await api.patch(`/glossary/${row.id}`, { status })
  row.status = status
}

async function deleteTerm(id) {
  await ElMessageBox.confirm('确认删除此术语？', '提示', { type: 'warning' })
  await api.delete(`/glossary/${id}`)
  terms.value = terms.value.filter(t => t.id !== id)
  ElMessage.success('已删除')
}

async function batchDelete() {
  const count = selectedIds.value.length
  await ElMessageBox.confirm(
    `确认删除选中的 ${count} 条术语？此操作不可撤销。`,
    '批量删除',
    { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
  )
  await Promise.all(selectedIds.value.map(id => api.delete(`/glossary/${id}`)))
  const deleted = new Set(selectedIds.value)
  terms.value = terms.value.filter(t => !deleted.has(t.id))
  tableRef.value?.clearSelection()
  ElMessage.success(`已删除 ${count} 条术语`)
}
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 1200px; margin: 0 auto; padding: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }
.page-header h2 { margin: 0; }
.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

/* 处理方式卡片选择 */
.action-options { display: flex; gap: 8px; width: 100%; }
.action-option {
  flex: 1;
  padding: 8px 10px;
  border: 1.5px solid #dcdfe6;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  text-align: center;
}
.action-option:hover { border-color: #409eff; }
.action-option.active { border-color: #409eff; background: #ecf5ff; }
.action-option-label { font-size: 0.85rem; font-weight: 600; color: #303133; }
.action-option.active .action-option-label { color: #409eff; }
.action-option-desc { font-size: 0.75rem; color: #909399; margin-top: 2px; }

/* 无限滚动哨兵 */
.load-sentinel {
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.85rem;
  color: #909399;
  gap: 6px;
}

/* 批量操作栏 */
.batch-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; margin-bottom: 8px;
  background: #fef0f0; border-radius: 6px;
  font-size: 0.88rem; color: #f56c6c;
}
</style>
