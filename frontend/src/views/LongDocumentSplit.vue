<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <div class="header-row">
        <div>
          <h2>长文档分章</h2>
          <p v-if="paper" class="sub-title">{{ paper.title_zh || paper.title }}</p>
        </div>
        <el-button @click="$router.push('/jobs')">返回任务</el-button>
      </div>

      <el-skeleton v-if="loading" :rows="8" animated />

      <template v-else-if="paper && parentJob">
        <el-alert
          v-if="parentJob.status === 'waiting_chapters'"
          type="info"
          :closable="false"
          title="请按章节填写页码范围。未覆盖的封面、目录、附录页可以保留跳过。"
          style="margin-bottom:16px"
        />

        <section v-if="chapters.length === 0" class="split-layout">
          <el-card class="editor-card">
            <div class="card-title">章节范围</div>
            <el-table :data="chapterDrafts" size="small" border>
              <el-table-column label="#" width="48" type="index" />
              <el-table-column label="章节名" min-width="180">
                <template #default="{ row, $index }">
                  <el-input v-model="row.title" size="small" :placeholder="`第 ${$index + 1} 章`" />
                </template>
              </el-table-column>
              <el-table-column label="起始页" width="120">
                <template #default="{ row }">
                  <el-input-number v-model="row.start_page" :min="1" :max="pageCount" size="small" controls-position="right" />
                </template>
              </el-table-column>
              <el-table-column label="结束页" width="120">
                <template #default="{ row }">
                  <el-input-number v-model="row.end_page" :min="1" :max="pageCount" size="small" controls-position="right" />
                </template>
              </el-table-column>
              <el-table-column label="预览" width="80">
                <template #default="{ row }">
                  <el-button size="small" text @click="previewPage = row.start_page">查看</el-button>
                </template>
              </el-table-column>
              <el-table-column width="70">
                <template #default="{ $index }">
                  <el-button size="small" text type="danger" :disabled="chapterDrafts.length <= 1" @click="removeChapter($index)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>

            <div class="editor-actions">
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <el-button @click="addChapter">新增章节</el-button>
                <el-button @click="loadOutlineDraft" :loading="outlineLoading">用 PDF 书签生成</el-button>
              </div>
              <el-button type="primary" :loading="submitting" :disabled="!!validationError" @click="submitChapters">
                确认分章并开始处理
              </el-button>
            </div>

            <el-alert v-if="validationError" type="error" :title="validationError" :closable="false" style="margin-top:12px" />
            <el-alert v-else-if="uncoveredText" type="warning" :title="uncoveredText" :closable="false" style="margin-top:12px" />
          </el-card>

          <el-card class="preview-card">
            <div class="preview-toolbar">
              <span>页码预览</span>
              <el-input-number v-model="previewPage" :min="1" :max="pageCount" size="small" controls-position="right" />
            </div>
            <img class="page-thumb" :src="thumbnailUrl" :alt="`page ${previewPage}`" />
          </el-card>
        </section>

        <section v-else>
          <el-card>
            <div class="status-head">
              <div>
                <div class="card-title">章节任务</div>
                <p class="sub-title">{{ parentJob.current_stage || statusLabel(parentJob.status) }}</p>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <el-button v-if="parentJob.status === 'completed'" type="success" @click="$router.push(`/results/by-paper/${paper.id}`)">阅读合并结果</el-button>
                <el-button @click="loadDetail">刷新</el-button>
              </div>
            </div>
            <el-progress :percentage="parentJob.progress || 0" :status="parentJob.status === 'failed' ? 'exception' : undefined" />

            <el-table :data="chapters" size="small" style="margin-top:16px">
              <el-table-column label="#" prop="chapter_index" width="56" />
              <el-table-column label="章节" min-width="180">
                <template #default="{ row }">
                  {{ row.chapter_title || `第 ${row.chapter_index} 章` }}
                </template>
              </el-table-column>
              <el-table-column label="页码" width="110">
                <template #default="{ row }">{{ row.start_page }}-{{ row.end_page }}</template>
              </el-table-column>
              <el-table-column label="状态" width="140">
                <template #default="{ row }">
                  <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="进度" min-width="160">
                <template #default="{ row }">
                  <el-progress :percentage="row.progress || 0" :show-text="false" />
                  <div class="tiny-stage">{{ row.current_stage }}</div>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="180">
                <template #default="{ row }">
                  <el-button v-if="row.status === 'completed'" size="small" @click="$router.push(`/results/by-job/${row.job_id}`)">阅读</el-button>
                  <el-button v-if="row.status === 'waiting_term_review'" size="small" type="warning" @click="openTermReview(row)">术语审查</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </section>
      </template>

      <el-dialog v-model="termDialog.visible" title="章节术语审查" width="760px">
        <el-table v-if="termDialog.terms" :data="termDialog.terms" size="small" border>
          <el-table-column label="英文术语" prop="en" min-width="160" />
          <el-table-column label="处理方式" min-width="220">
            <template #default="{ row }">
              <el-select v-model="row.status" size="small" style="width:100%">
                <el-option value="translate" label="仅翻译" />
                <el-option value="translate_with_annotation" label="翻译并保留原文" />
                <el-option value="never_translate" label="保留原文" />
                <el-option value="skip" label="跳过" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="中文译名" min-width="180">
            <template #default="{ row }">
              <el-input v-model="row.zh" size="small" :disabled="row.status === 'never_translate' || row.status === 'skip'" />
            </template>
          </el-table-column>
        </el-table>
        <template #footer>
          <el-button @click="termDialog.visible = false">取消</el-button>
          <el-button type="primary" :loading="termDialog.confirming" @click="confirmTerms">确认并继续</el-button>
        </template>
      </el-dialog>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'

const route = useRoute()
const loading = ref(true)
const submitting = ref(false)
const outlineLoading = ref(false)
const paper = ref(null)
const parentJob = ref(null)
const chapters = ref([])
const pageCount = ref(1)
const previewPage = ref(1)
const chapterDrafts = ref([])
let timer = null

const termDialog = reactive({
  visible: false,
  jobId: null,
  terms: null,
  confirming: false,
})

const thumbnailUrl = computed(() => `/api/v1/papers/long-drafts/${parentJob.value?.id}/pages/${previewPage.value}/thumbnail`)

const validationError = computed(() => {
  const ranges = []
  for (let i = 0; i < chapterDrafts.value.length; i++) {
    const row = chapterDrafts.value[i]
    if (!row.title?.trim()) return `第 ${i + 1} 行缺少章节名`
    if (!row.start_page || !row.end_page) return `第 ${i + 1} 行缺少页码`
    if (row.start_page < 1 || row.end_page > pageCount.value) return `第 ${i + 1} 行页码超出范围`
    if (row.start_page > row.end_page) return `第 ${i + 1} 行起始页不能大于结束页`
    ranges.push({ start: row.start_page, end: row.end_page, index: i + 1 })
  }
  ranges.sort((a, b) => a.start - b.start)
  for (let i = 1; i < ranges.length; i++) {
    if (ranges[i].start <= ranges[i - 1].end) return `第 ${ranges[i - 1].index} 行和第 ${ranges[i].index} 行页码重叠`
  }
  return ''
})

const uncoveredText = computed(() => {
  if (!pageCount.value || validationError.value) return ''
  const covered = new Set()
  for (const row of chapterDrafts.value) {
    for (let p = row.start_page; p <= row.end_page; p++) covered.add(p)
  }
  const missing = []
  for (let p = 1; p <= pageCount.value; p++) {
    if (!covered.has(p)) missing.push(p)
  }
  if (!missing.length) return ''
  return `未覆盖 ${missing.length} 页，例如第 ${missing.slice(0, 8).join('、')} 页；这些页面会被跳过。`
})

onMounted(async () => {
  await loadDetail()
  timer = setInterval(() => {
    if (parentJob.value && !['completed', 'failed', 'waiting_chapters'].includes(parentJob.value.status)) {
      loadDetail()
    }
  }, 8000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function loadDetail() {
  loading.value = !paper.value
  try {
    const res = await api.get(`/papers/long-documents/${route.params.paperId}/chapters`)
    paper.value = res.data.paper
    parentJob.value = res.data.parent_job
    chapters.value = res.data.chapters || []
    pageCount.value = paper.value?.page_count || 1
    previewPage.value = Math.min(previewPage.value, pageCount.value)
    if (chapters.value.length === 0 && chapterDrafts.value.length === 0) {
      await loadOutlineDraft({ silent: true })
      if (chapterDrafts.value.length === 0) {
        chapterDrafts.value = [{ title: '第 1 章', start_page: 1, end_page: pageCount.value }]
      }
    }
  } finally {
    loading.value = false
  }
}

function addChapter() {
  const last = chapterDrafts.value[chapterDrafts.value.length - 1]
  const start = Math.min((last?.end_page || 0) + 1, pageCount.value)
  chapterDrafts.value.push({
    title: `第 ${chapterDrafts.value.length + 1} 章`,
    start_page: start,
    end_page: pageCount.value,
  })
}

function removeChapter(index) {
  chapterDrafts.value.splice(index, 1)
}

async function loadOutlineDraft(options = {}) {
  if (!parentJob.value?.id) return
  outlineLoading.value = true
  try {
    const res = await api.get(`/papers/long-drafts/${parentJob.value.id}/outline`)
    const suggestions = res.data?.chapters || []
    if (suggestions.length > 0) {
      chapterDrafts.value = suggestions.map((row, index) => ({
        title: row.title || `第 ${index + 1} 章`,
        start_page: row.start_page,
        end_page: row.end_page,
      }))
      previewPage.value = chapterDrafts.value[0]?.start_page || 1
      if (!options.silent) ElMessage.success(`已根据 ${suggestions.length} 个一级书签生成章节草稿`)
    } else if (!options.silent) {
      ElMessage.warning('这个 PDF 没有可用的一级书签')
    }
  } finally {
    outlineLoading.value = false
  }
}

async function submitChapters() {
  if (validationError.value) return
  submitting.value = true
  try {
    await api.post(`/papers/long-drafts/${parentJob.value.id}/chapters`, {
      chapters: chapterDrafts.value.map(row => ({
        title: row.title.trim(),
        start_page: row.start_page,
        end_page: row.end_page,
      })),
    })
    ElMessage.success('已创建章节任务')
    await loadDetail()
  } finally {
    submitting.value = false
  }
}

async function openTermReview(chapter) {
  termDialog.visible = true
  termDialog.jobId = chapter.job_id
  termDialog.terms = null
  const res = await api.get(`/jobs/${chapter.job_id}/pending-terms`)
  termDialog.terms = (res.data.terms || []).map(t => ({
    en: t.en,
    zh: t.zh,
    status: 'translate',
  }))
}

async function confirmTerms() {
  if (!termDialog.jobId || !termDialog.terms) return
  termDialog.confirming = true
  try {
    await api.post(`/jobs/${termDialog.jobId}/confirm-terms`, termDialog.terms)
    ElMessage.success('已确认术语，章节继续处理')
    termDialog.visible = false
    await loadDetail()
  } finally {
    termDialog.confirming = false
  }
}

function statusLabel(status) {
  const map = {
    waiting_chapters: '待分章',
    pending: '等待中',
    parsing: '解析中',
    polishing: '整理中',
    waiting_term_review: '待术语审查',
    translating: '处理中',
    image_translating: '图表处理中',
    completed: '已完成',
    failed: '失败',
  }
  return map[status] || status
}

function statusTag(status) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'waiting_term_review' || status === 'waiting_chapters') return 'warning'
  return 'primary'
}
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 1120px; margin: 0 auto; padding: 24px; }
.header-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.sub-title { margin: 4px 0 0; color: #909399; font-size: 0.9rem; }
.split-layout { display: grid; grid-template-columns: minmax(0, 1.5fr) 360px; gap: 16px; align-items: start; }
.card-title { font-weight: 600; margin-bottom: 12px; color: #303133; }
.editor-actions { display: flex; justify-content: space-between; gap: 12px; margin-top: 14px; }
.preview-card { position: sticky; top: 16px; }
.preview-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-weight: 600; }
.page-thumb { width: 100%; border: 1px solid #dcdfe6; border-radius: 4px; background: white; display: block; }
.status-head { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.tiny-stage { color: #909399; font-size: 0.78rem; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 900px) {
  .split-layout { grid-template-columns: 1fr; }
  .preview-card { position: static; }
}
</style>
