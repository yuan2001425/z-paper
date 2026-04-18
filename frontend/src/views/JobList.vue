<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <h2>我的任务处理进度</h2>
      <div style="display:flex;gap:12px;margin-bottom:16px">
        <el-button type="primary" @click="$router.push('/translate')">上传新论文</el-button>
        <el-button
          v-if="jobs.some(j => j.status === 'completed')"
          @click="clearCompleted"
          :loading="clearingCompleted"
        >清空已完成</el-button>
        <el-button
          v-if="jobs.some(j => j.status === 'failed')"
          type="danger" plain
          @click="clearFailed"
          :loading="clearingFailed"
        >清空失败</el-button>
      </div>

      <div v-if="jobs.length === 0">
        <el-empty description="还没有翻译任务" />
      </div>

      <el-card v-for="job in jobs" :key="job.id" class="job-card">
        <div class="job-header">
          <span class="job-id">任务 {{ job.id.slice(0, 8) }}</span>
          <el-tag size="small" type="info" v-if="job.job_type === 'archive'" style="margin-right:4px">中文</el-tag>
          <el-tag :type="statusTagType(job.status)">{{ statusLabel(job.status, job.job_type) }}</el-tag>
        </div>

        <!-- 进行中：进度条 -->
        <template v-if="isActive(job.status)">
          <el-progress :percentage="job.progress" :stroke-width="6" style="margin: 6px 0 4px" />
          <div class="stage-text">
            <el-icon class="is-loading" style="vertical-align:middle;margin-right:6px"><Loading /></el-icon>
            <span>{{ job.current_stage || statusLabel(job.status) }}</span>
            <span style="margin-left:8px;color:#c0c4cc">{{ job.progress }}%</span>
          </div>
        </template>

        <!-- 待术语审查 -->
        <div v-if="job.status === 'waiting_term_review'" class="term-review-block">
          <div class="term-review-header">
            <span>{{ job.current_stage }}</span>
            <el-button
              size="small" type="warning"
              @click="loadTerms(job.id)"
              :loading="termState[job.id]?.loading"
            >
              {{ termState[job.id]?.terms ? '重新加载' : '展开审查' }}
            </el-button>
          </div>

          <template v-if="termState[job.id]?.terms">
            <div style="font-size:0.8rem;color:#909399;margin:8px 0 4px">
              所属领域：<strong>{{ termState[job.id].paperDomain || '（未设置）' }}</strong>
              &nbsp;·&nbsp; 术语将归入该领域的词库
            </div>
            <el-table :data="termState[job.id].terms" size="small" style="margin-top:4px" border>
              <el-table-column label="英文术语" prop="en" min-width="150" />
              <el-table-column label="处理方式" min-width="200">
                <template #default="{ row }">
                  <el-select v-model="row.status" size="small" style="width:100%">
                    <el-option value="translate"                 label="仅翻译" />
                    <el-option value="translate_with_annotation" label="翻译并保留原文" />
                    <el-option value="never_translate"           label="保留原文（不翻译）" />
                    <el-option value="skip"                      label="跳过（不加入词库）" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="中文译名" min-width="170">
                <template #default="{ row }">
                  <el-input
                    v-model="row.zh"
                    size="small"
                    :disabled="row.status === 'never_translate' || row.status === 'skip'"
                    placeholder="输入中文译名"
                  />
                </template>
              </el-table-column>
            </el-table>

            <div class="term-review-footer">
              <span style="font-size:0.8rem;color:#909399">
                「跳过」的术语本次不写入词库；「保留原文」无需填写中文
              </span>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <el-button size="small" @click="setAllStatus(job.id, 'translate')">全部「仅翻译」</el-button>
                <el-button size="small" @click="setAllStatus(job.id, 'translate_with_annotation')">全部「翻译保留原文」</el-button>
                <el-button size="small" @click="setAllStatus(job.id, 'never_translate')">全部「保留原文」</el-button>
                <el-button size="small" @click="setAllStatus(job.id, 'skip')">全部「跳过」</el-button>
                <el-button
                  type="primary" size="small"
                  :loading="termState[job.id]?.confirming"
                  @click="confirmTerms(job.id)"
                >
                  确认并继续翻译
                </el-button>
              </div>
            </div>
          </template>
        </div>

        <!-- 完成 -->
        <div v-if="job.status === 'completed'" class="completed-info">
          <el-button type="success" @click="viewResult(job.id)">查看译文</el-button>
        </div>

        <!-- 失败 -->
        <el-alert
          v-if="job.status === 'failed'"
          type="error"
          :title="job.error_message || '翻译失败'"
          :closable="false"
          style="margin-top:8px"
        />

        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
          <span class="job-time">提交时间：{{ formatTime(job.created_at) }}</span>
          <el-button type="danger" size="small" text @click="deleteJob(job.id)">删除</el-button>
        </div>
      </el-card>
    </main>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useJobStore } from '@/stores/job'
import AppHeader from '@/components/AppHeader.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import api from '@/api/index.js'

const router = useRouter()
const jobStore = useJobStore()
const { jobs } = storeToRefs(jobStore)
const clearingCompleted = ref(false)
const clearingFailed = ref(false)

// termState[jobId] = { loading, confirming, terms: [{en, zh, include}] | null }
const termState = reactive({})

onMounted(() => jobStore.startPolling(10000))
onUnmounted(() => jobStore.stopPolling())

async function loadTerms(jobId) {
  if (!termState[jobId]) termState[jobId] = { loading: false, confirming: false, terms: null, paperDomain: null }
  termState[jobId].loading = true
  try {
    const res = await api.get(`/jobs/${jobId}/pending-terms`)
    const { paper_domain, terms } = res.data
    termState[jobId].paperDomain = paper_domain
    termState[jobId].terms = terms.map(t => ({
      en: t.en,
      zh: t.zh,
      status: 'translate',
    }))
  } catch (err) {
    ElMessage.error('加载术语失败：' + (err.response?.data?.detail || err.message))
  } finally {
    termState[jobId].loading = false
  }
}

function setAllStatus(jobId, status) {
  termState[jobId]?.terms?.forEach(t => { t.status = status })
}

async function confirmTerms(jobId) {
  const state = termState[jobId]
  if (!state?.terms) return
  state.confirming = true
  try {
    const res = await api.post(`/jobs/${jobId}/confirm-terms`, state.terms)
    ElMessage.success(`已确认，${res.data.saved} 条术语加入词库，翻译继续进行`)
    state.terms = null
    await jobStore.fetchJobs()
  } catch (err) {
    ElMessage.error('确认失败：' + (err.response?.data?.detail || err.message))
  } finally {
    state.confirming = false
  }
}

async function deleteJob(jobId) {
  await ElMessageBox.confirm('确认删除此任务？', '提示', { type: 'warning' })
  await api.delete(`/jobs/${jobId}`)
  await jobStore.fetchJobs()
}

async function clearCompleted() {
  await ElMessageBox.confirm(
    '此操作将永久删除所有已完成任务对应的论文、译文及上传文件，无法恢复。确认继续？',
    '危险操作：清空已完成',
    {
      type: 'error',
      confirmButtonText: '确认删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    }
  )
  clearingCompleted.value = true
  try {
    await api.delete('/jobs', { params: { status: 'completed' } })
    await jobStore.fetchJobs()
    ElMessage.success('已清空已完成任务')
  } finally {
    clearingCompleted.value = false
  }
}

async function clearFailed() {
  await ElMessageBox.confirm(
    '此操作将永久删除所有失败任务对应的论文及上传文件，无法恢复。确认继续？',
    '危险操作：清空失败',
    {
      type: 'error',
      confirmButtonText: '确认删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    }
  )
  clearingFailed.value = true
  try {
    await api.delete('/jobs', { params: { status: 'failed' } })
    await jobStore.fetchJobs()
    ElMessage.success('已清空失败任务')
  } finally {
    clearingFailed.value = false
  }
}

function statusLabel(status, jobType) {
  if (jobType === 'archive') {
    const map = {
      pending: '等待中', parsing: 'PDF 解析中',
      polishing: '文本整理中', translating: '结构分析中',
      completed: '已完成', failed: '失败',
    }
    return map[status] || status
  }
  const map = {
    pending: '等待中',
    parsing: 'PDF 解析中',
    polishing: '内容整理中',
    waiting_term_review: '待术语审查',
    translating: '翻译中',
    image_translating: '图表处理中',
    completed: '已完成',
    failed: '失败',
  }
  return map[status] || status
}

function statusTagType(status) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'waiting_term_review') return 'warning'
  return 'primary'
}

function isActive(status) {
  return ['pending', 'parsing', 'polishing', 'translating', 'image_translating'].includes(status)
}

async function viewResult(jobId) {
  router.push(`/results/by-job/${jobId}`)
}

function formatTime(t) {
  return new Date(t).toLocaleString('zh-CN')
}
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 800px; margin: 0 auto; padding: 24px; }
.job-card { margin-bottom: 16px; }
.job-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.job-id { font-family: monospace; color: #909399; }
.stage-text { color: #606266; font-size: 0.85rem; }
.completed-info { display: flex; align-items: center; gap: 16px; margin-top: 8px; }
.job-time { color: #c0c4cc; font-size: 0.8rem; margin-top: 8px; }

/* 术语审查块 */
.term-review-block {
  margin-top: 10px;
  border: 1px solid #faecd8;
  border-radius: 6px;
  padding: 12px;
  background: #fdf6ec;
}
.term-review-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.9rem;
  color: #e6a23c;
  font-weight: 500;
}
.term-review-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
  gap: 12px;
}
</style>
