<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <h2>批量上传论文</h2>

      <el-steps :active="phase" finish-status="success" style="margin-bottom:32px">
        <el-step title="选择文件" />
        <el-step title="逐篇录入信息" />
        <el-step title="提交处理" />
      </el-steps>

      <!-- ── 阶段 0：文件列表 ──────────────────────────────────────────────── -->
      <el-card v-if="phase === 0">
        <div class="drop-zone" @click="triggerPicker" @dragover.prevent @drop.prevent="onDrop">
          <el-icon style="font-size:40px;color:#c0c4cc"><Upload /></el-icon>
          <p style="color:#606266;margin:8px 0 4px">将 PDF 文件拖到此处，或 <em style="color:#409eff">点击选择</em></p>
          <p style="color:#909399;font-size:0.82rem">支持多选，仅接受 .pdf 格式</p>
          <input ref="fileInput" type="file" multiple accept=".pdf" style="display:none" @change="onFilePick" />
        </div>

        <template v-if="fileList.length">
          <!-- 默认设置（同单篇录入 Step 0） -->
          <div class="batch-defaults">
            <div class="batch-defaults-row">
              <span class="batch-defaults-label">论文类型：</span>
              <el-radio-group v-model="batchDefaultType">
                <el-radio value="journal">期刊论文</el-radio>
                <el-radio value="conference">会议论文</el-radio>
              </el-radio-group>
            </div>
            <div class="batch-defaults-row">
              <span class="batch-defaults-label">论文学科：</span>
              <el-select
                v-model="defaultDomain"
                placeholder="选择学科（可搜索）"
                filterable
                style="width:280px"
              >
                <el-option v-for="d in DISCIPLINES" :key="d.value" :label="d.label" :value="d.value" />
              </el-select>
            </div>
          </div>

          <div class="list-toolbar">
            <el-checkbox v-model="allChecked" @change="toggleAll" :indeterminate="someChecked && !allChecked">
              全选 / 取消
            </el-checkbox>
            <span style="color:#909399;font-size:0.85rem">已选 {{ checkedCount }} / {{ fileList.length }} 篇</span>
          </div>

          <el-table :data="fileList" row-key="uid" style="width:100%" size="small">
            <el-table-column width="48">
              <template #default="{ row }">
                <el-checkbox v-model="row.include" />
              </template>
            </el-table-column>
            <el-table-column label="文件名" min-width="260">
              <template #default="{ row }">
                <el-icon style="color:#e6a23c;margin-right:4px"><Document /></el-icon>
                <span>{{ row.file.name }}</span>
                <span style="color:#c0c4cc;font-size:0.78rem;margin-left:6px">
                  {{ (row.file.size / 1024 / 1024).toFixed(1) }} MB
                </span>
              </template>
            </el-table-column>
            <el-table-column label="语言" width="180">
              <template #default="{ row }">
                <el-radio-group v-model="row.language" size="small">
                  <el-radio-button value="en">英文</el-radio-button>
                  <el-radio-button value="zh">中文</el-radio-button>
                </el-radio-group>
              </template>
            </el-table-column>
            <el-table-column width="64" align="right">
              <template #default="{ row }">
                <el-button text type="danger" size="small" @click="removeFile(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div style="margin-top:20px;display:flex;justify-content:flex-end">
            <el-button type="primary" :disabled="checkedCount === 0" @click="startMetaEntry">
              开始逐篇录入（{{ checkedCount }} 篇）
            </el-button>
          </div>
        </template>
      </el-card>

      <!-- ── 阶段 1：逐篇录入元数据 ──────────────────────────────────────── -->
      <el-card v-if="phase === 1">
        <div class="entry-header">
          <div class="entry-progress">
            第 <strong>{{ entryIdx + 1 }}</strong> / {{ entryList.length }} 篇
          </div>
          <div class="entry-filename">
            <el-icon><Document /></el-icon>
            {{ entryList[entryIdx]?.file.name }}
          </div>
          <el-progress
            :percentage="Math.round(((entryIdx) / entryList.length) * 100)"
            :show-text="false"
            style="width:200px"
          />
        </div>

        <!-- 提取中 -->
        <div v-if="extracting" class="extracting-tip">
          <el-icon class="spin"><Loading /></el-icon>
          正在自动识别论文信息…
        </div>

        <!-- 元数据表单 -->
        <template v-else>
          <el-alert
            v-if="extractedAuto"
            type="success"
            title="已自动识别论文信息，请核对并补充"
            :closable="false"
            style="margin-bottom:16px"
          />

          <el-form :model="meta" label-width="140px" label-position="right">

            <el-form-item label="外文标题" required>
              <div style="display:flex;gap:8px;align-items:center;width:100%">
                <el-input v-model="meta.title" placeholder="论文的外文标题" style="flex:1" />
                <el-select v-model="meta.source_language" style="width:140px">
                  <el-option
                    v-for="lang in SUPPORTED_LANGUAGES"
                    :key="lang.code"
                    :label="lang.disabled ? lang.label + '（即将支持）' : lang.label"
                    :value="lang.code"
                    :disabled="lang.disabled"
                  />
                </el-select>
              </div>
            </el-form-item>

            <el-form-item label="中文标题" required>
              <el-input v-model="meta.title_zh" placeholder="论文的中文标题" />
            </el-form-item>

            <el-form-item label="论文类型" required>
              <el-radio-group v-model="meta.paper_type" @change="onTypeChange">
                <el-radio value="journal">期刊论文</el-radio>
                <el-radio value="conference">会议论文</el-radio>
              </el-radio-group>
            </el-form-item>

            <template v-if="meta.paper_type === 'journal'">
              <el-form-item label="期刊名称" required>
                <el-input v-model="meta.journal" placeholder="如：Nature / IEEE TPAMI" />
              </el-form-item>
              <el-form-item label="发表年份" required>
                <el-input-number v-model="meta.year" :min="1900" :max="2099" controls-position="right" />
              </el-form-item>
              <el-form-item label="期刊分区/收录" required>
                <el-select v-model="meta.divisionTags" multiple filterable allow-create clearable
                  collapse-tags collapse-tags-tooltip placeholder="可多选，也可直接输入自定义值"
                  style="width:400px" @change="onDivisionChange">
                  <el-option value="无分区/未分类" label="无分区/未分类" />
                  <el-option-group label="中科院分区">
                    <el-option v-for="v in ['中科院一区','中科院二区','中科院三区','中科院四区']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                  <el-option-group label="JCR 分区">
                    <el-option v-for="v in ['Q1','Q2','Q3','Q4']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                  <el-option-group label="检索收录">
                    <el-option v-for="v in ['SCI','SSCI','EI','Scopus','CSCD','北大核心','CSSCI']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                </el-select>
              </el-form-item>
            </template>

            <template v-if="meta.paper_type === 'conference'">
              <el-form-item label="会议名称" required>
                <el-input v-model="meta.journal" placeholder="如：NeurIPS 2024 / CVPR 2023" />
              </el-form-item>
              <el-form-item label="举办年份" required>
                <el-input-number v-model="meta.year" :min="1900" :max="2099" controls-position="right" />
              </el-form-item>
              <el-form-item label="会议级别/分区" required>
                <el-select v-model="meta.divisionTags" multiple filterable allow-create clearable
                  collapse-tags collapse-tags-tooltip placeholder="可多选，也可直接输入自定义值"
                  style="width:400px" @change="onDivisionChange">
                  <el-option value="无分区/未分类" label="无分区/未分类" />
                  <el-option-group label="CCF 分类">
                    <el-option v-for="v in ['CCF-A','CCF-B','CCF-C','非CCF']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                  <el-option-group label="CORE 分类">
                    <el-option v-for="v in ['CORE A*','CORE A','CORE B','CORE C']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                  <el-option-group label="检索收录">
                    <el-option v-for="v in ['EI','SCI','SSCI','CPCI','Scopus']" :key="v" :label="v" :value="v" />
                  </el-option-group>
                </el-select>
              </el-form-item>
            </template>

            <el-form-item label="论文学科" required>
              <el-select v-model="meta.domain" placeholder="选择学科" filterable style="width:300px">
                <el-option v-for="d in DISCIPLINES" :key="d.value" :label="d.label" :value="d.value" />
              </el-select>
            </el-form-item>

            <el-form-item label="DOI（选填）">
              <el-input v-model="meta.doi" placeholder="如：10.1145/3386569.3392506" style="width:360px" />
            </el-form-item>

            <el-form-item v-if="meta.source_language !== 'zh'" label="翻译图片文字">
              <el-switch v-model="meta.translate_images" />
              <span style="margin-left:10px;font-size:0.85rem;color:#909399">
                {{ meta.translate_images ? '自动翻译图表内文字' : '跳过，进入阅读页后可手动翻译' }}
              </span>
            </el-form-item>
          </el-form>
        </template>

        <div style="display:flex;gap:12px;margin-top:20px;justify-content:space-between">
          <el-button @click="prevEntry" :disabled="entryIdx === 0">上一篇</el-button>
          <div style="display:flex;gap:12px">
            <el-button @click="skipEntry">跳过此篇</el-button>
            <el-button type="primary" :disabled="extracting || !isFormValid" @click="confirmEntry">
              {{ entryIdx < entryList.length - 1 ? '确认，下一篇' : '确认，完成录入' }}
            </el-button>
          </div>
        </div>
      </el-card>

      <!-- 重复检测弹窗 -->
      <el-dialog v-model="dupDialog.visible" title="发现相似论文" width="640px" :close-on-click-modal="false">
        <p style="color:#606266;margin-bottom:16px">库中已有以下相似论文，请确认是否仍需继续：</p>
        <div v-for="p in dupDialog.list" :key="p.paper_id"
          style="border:1px solid #ebeef5;border-radius:6px;padding:12px 16px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
            <div style="flex:1;min-width:0">
              <div style="font-weight:500;word-break:break-all">{{ p.title }}</div>
              <div v-if="p.title_zh" style="color:#909399;font-size:0.85rem;margin-top:2px">{{ p.title_zh }}</div>
            </div>
            <el-tag :type="p.similarity >= 0.85 ? 'danger' : 'warning'" size="small">
              相似度 {{ Math.round(p.similarity * 100) }}%
            </el-tag>
          </div>
        </div>
        <template #footer>
          <el-button @click="dupDialog.visible = false">取消（跳过此篇）</el-button>
          <el-button type="primary" @click="dupDialog.visible = false; finishConfirm()">仍然继续</el-button>
        </template>
      </el-dialog>

      <!-- ── 阶段 2：汇总提交 ──────────────────────────────────────────── -->
      <el-card v-if="phase === 2">
        <div class="summary-header">
          已完成 <strong>{{ confirmedList.length }}</strong> 篇信息录入，准备提交
          <span v-if="skippedCount" style="color:#909399;font-size:0.85rem;margin-left:8px">
            （跳过 {{ skippedCount }} 篇）
          </span>
        </div>

        <el-table :data="confirmedList" size="small" style="margin-top:16px">
          <el-table-column type="index" width="48" />
          <el-table-column label="文件名" min-width="180" prop="file.name" />
          <el-table-column label="标题">
            <template #default="{ row }">
              <div>{{ row.meta.title_zh || row.meta.title }}</div>
              <div v-if="row.meta.title_zh && row.meta.title" style="color:#909399;font-size:0.78rem">{{ row.meta.title }}</div>
            </template>
          </el-table-column>
          <el-table-column label="语言" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.meta.source_language === 'zh' ? 'success' : ''">
                {{ row.meta.source_language === 'zh' ? '中文' : '英文' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="期刊/会议" prop="meta.journal" min-width="140" />
          <el-table-column label="年份" prop="meta.year" width="70" />
        </el-table>

        <div v-if="submitProgress.total > 0" style="margin-top:20px">
          <el-progress
            :percentage="Math.round((submitProgress.done / submitProgress.total) * 100)"
            :status="submitProgress.done === submitProgress.total ? 'success' : ''"
          />
          <p style="text-align:center;color:#909399;font-size:0.85rem;margin-top:8px">
            已提交 {{ submitProgress.done }} / {{ submitProgress.total }} 篇
          </p>
        </div>

        <div style="display:flex;justify-content:space-between;margin-top:20px">
          <el-button @click="phase = 1; entryIdx = confirmedList.length > 0 ? confirmedList.length - 1 : 0">
            返回修改
          </el-button>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="confirmedList.length === 0"
            @click="submitAll"
          >
            提交所有论文并开始处理（{{ confirmedList.length }} 篇）
          </el-button>
        </div>
      </el-card>

    </main>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Upload, Document, Loading } from '@element-plus/icons-vue'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import { DISCIPLINES, SUPPORTED_LANGUAGES } from '@/constants/disciplines.js'
import { useDefaultDomain } from '@/composables/useDefaultDomain.js'

const { defaultDomain } = useDefaultDomain()
const batchDefaultType = ref('journal')
const router = useRouter()

// ── 阶段状态 ──────────────────────────────────────────────────────────────────
const phase    = ref(0)
const fileInput = ref(null)

// ── 阶段 0：文件列表 ──────────────────────────────────────────────────────────
let _uid = 0
const fileList = ref([])  // [{ uid, file, include, language }]

const checkedCount = computed(() => fileList.value.filter(f => f.include).length)
const allChecked   = computed(() => fileList.value.length > 0 && fileList.value.every(f => f.include))
const someChecked  = computed(() => fileList.value.some(f => f.include))

function triggerPicker() { fileInput.value?.click() }

function addFiles(rawFiles) {
  for (const f of rawFiles) {
    if (!f.name.toLowerCase().endsWith('.pdf')) continue
    if (fileList.value.some(item => item.file.name === f.name && item.file.size === f.size)) continue
    fileList.value.push({ uid: _uid++, file: f, include: true, language: 'en' })
  }
}

function onFilePick(e) { addFiles(Array.from(e.target.files)); e.target.value = '' }
function onDrop(e)     { addFiles(Array.from(e.dataTransfer.files)) }
function removeFile(row) { fileList.value = fileList.value.filter(f => f.uid !== row.uid) }
function toggleAll(val)  { fileList.value.forEach(f => f.include = val) }

function startMetaEntry() {
  entryList.value = fileList.value.filter(f => f.include)
  entryIdx.value  = 0
  confirmedList.value = []
  skippedCount.value  = 0
  phase.value = 1
  extractForCurrent()
}

// ── 阶段 1：逐篇录入 ──────────────────────────────────────────────────────────
const entryList     = ref([])
const entryIdx      = ref(0)
const confirmedList = ref([])  // [{ file, meta }]
const skippedCount  = ref(0)
const extracting    = ref(false)
const extractedAuto = ref(false)
const dupDialog     = ref({ visible: false, list: [] })

const NO_DIVISION = '无分区/未分类'
const JOURNAL_GROUPS   = [['中科院一区','中科院二区','中科院三区','中科院四区'],['Q1','Q2','Q3','Q4']]
const CONFERENCE_GROUPS= [['CCF-A','CCF-B','CCF-C','非CCF'],['CORE A*','CORE A','CORE B','CORE C']]

function freshMeta(language = 'en') {
  return {
    title: '', title_zh: '', paper_type: batchDefaultType.value, journal: '',
    divisionTags: [], year: new Date().getFullYear(),
    doi: '', source_language: language, domain: defaultDomain.value,
    translate_images: true,
  }
}
const meta = ref(freshMeta())

const isFormValid = computed(() => {
  const m = meta.value
  return !!(m.title.trim() && m.title_zh.trim() && m.journal.trim() && m.year && m.divisionTags.length && m.domain)
})

function onTypeChange() { meta.value.divisionTags = [] }
function onDivisionChange(val) {
  if (!val.length) return
  const last = val[val.length - 1]
  if (last === NO_DIVISION) { meta.value.divisionTags = [NO_DIVISION]; return }
  let tags = val.filter(v => v !== NO_DIVISION)
  const groups = meta.value.paper_type === 'journal' ? JOURNAL_GROUPS : CONFERENCE_GROUPS
  for (const group of groups) {
    const inGroup = tags.filter(t => group.includes(t))
    if (inGroup.length > 1) tags = tags.filter(t => !group.includes(t) || t === last)
  }
  meta.value.divisionTags = tags
}

async function extractForCurrent() {
  const item = entryList.value[entryIdx.value]
  if (!item) return
  meta.value = freshMeta(item.language)
  extractedAuto.value = false
  extracting.value = true
  try {
    const fd = new FormData()
    fd.append('file', item.file)
    if (meta.value.domain) fd.append('domain', meta.value.domain)
    fd.append('paper_type', batchDefaultType.value)
    const res = await api.post('/papers/extract-metadata', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    const d = res.data
    if (d.title)  { meta.value.title = d.title; extractedAuto.value = true }
    if (d.title_zh) meta.value.title_zh = d.title_zh
    if (d.journal)  meta.value.journal  = d.journal
    if (d.year)     meta.value.year     = parseInt(d.year) || meta.value.year
    if (d.doi)      meta.value.doi      = d.doi
    if (d.source_language) meta.value.source_language = d.source_language
    if (Array.isArray(d.division_tags) && d.division_tags.length > 0) {
      meta.value.divisionTags = d.division_tags
    }
  } catch { /* 提取失败静默处理 */ }
  finally { extracting.value = false }
}

async function confirmEntry() {
  // 重复检测
  if (meta.value.title || meta.value.title_zh) {
    try {
      const res = await api.post('/papers/check-duplicate', {
        title: meta.value.title, title_zh: meta.value.title_zh,
      })
      if (res.data.duplicates?.length > 0) {
        dupDialog.value = { visible: true, list: res.data.duplicates }
        return
      }
    } catch { /* 检测失败不阻断 */ }
  }
  finishConfirm()
}

function finishConfirm() {
  const item = entryList.value[entryIdx.value]
  confirmedList.value[entryIdx.value] = { file: item.file, meta: { ...meta.value } }
  advanceOrFinish()
}

function skipEntry() {
  skippedCount.value++
  confirmedList.value[entryIdx.value] = null  // mark as skipped
  advanceOrFinish()
}

function advanceOrFinish() {
  if (entryIdx.value < entryList.value.length - 1) {
    entryIdx.value++
    extractForCurrent()
  } else {
    // 过滤掉跳过的
    confirmedList.value = confirmedList.value.filter(Boolean)
    phase.value = 2
  }
}

function prevEntry() {
  if (entryIdx.value > 0) {
    entryIdx.value--
    // 恢复已保存的 meta（如果有），否则重新提取
    const saved = confirmedList.value[entryIdx.value]
    if (saved) {
      meta.value = { ...saved.meta }
      extractedAuto.value = false
      extracting.value = false
    } else {
      extractForCurrent()
    }
  }
}

// ── 阶段 2：提交 ──────────────────────────────────────────────────────────────
const submitting      = ref(false)
const submitProgress  = ref({ done: 0, total: 0 })

async function submitAll() {
  submitting.value = true
  submitProgress.value = { done: 0, total: confirmedList.value.length }
  let failCount = 0

  for (const item of confirmedList.value) {
    try {
      const fd = new FormData()
      fd.append('file', item.file)
      fd.append('title', item.meta.title)
      if (item.meta.title_zh) fd.append('title_zh', item.meta.title_zh)
      fd.append('paper_type', item.meta.paper_type)
      fd.append('journal', item.meta.journal)
      fd.append('year', item.meta.year)
      fd.append('division', item.meta.divisionTags.join('、'))
      fd.append('source_language', item.meta.source_language)
      if (item.meta.domain)  fd.append('domain', item.meta.domain)
      if (item.meta.doi)     fd.append('doi', item.meta.doi)

      const endpoint = item.meta.source_language === 'zh' ? '/papers/upload-chinese' : '/papers/upload'
      if (item.meta.source_language !== 'zh') {
        fd.append('translate_images', item.meta.translate_images ? 'true' : 'false')
      }
      await api.post(endpoint, fd, { headers: { 'Content-Type': 'multipart/form-data' } })
    } catch {
      failCount++
    }
    submitProgress.value.done++
  }

  submitting.value = false
  if (failCount === 0) {
    ElMessage.success(`全部 ${confirmedList.value.length} 篇已提交，正在处理！`)
  } else {
    ElMessage.warning(`${confirmedList.value.length - failCount} 篇成功，${failCount} 篇失败`)
  }
  router.push('/jobs')
}
</script>

<style scoped>
.page-layout  { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 900px; margin: 0 auto; padding: 24px; }

.drop-zone {
  border: 2px dashed #dcdfe6;
  border-radius: 8px;
  padding: 40px 24px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s;
  margin-bottom: 20px;
}
.drop-zone:hover { border-color: #409eff; }

.batch-defaults {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 6px;
  margin-bottom: 16px;
}
.batch-defaults-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.batch-defaults-label {
  width: 70px;
  text-align: right;
  color: #606266;
  font-size: 0.9rem;
  white-space: nowrap;
  flex-shrink: 0;
}

.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.entry-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e4e7ed;
  flex-wrap: wrap;
}
.entry-progress { font-size: 1rem; color: #303133; white-space: nowrap; }
.entry-filename {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  font-size: 0.88rem;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extracting-tip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 32px;
  justify-content: center;
  color: #909399;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.summary-header {
  font-size: 1rem;
  color: #303133;
  padding-bottom: 4px;
}
</style>
