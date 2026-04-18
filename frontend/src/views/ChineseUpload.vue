<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <h2>上传中文论文</h2>

      <el-steps :active="step" finish-status="success" style="margin-bottom:32px">
        <el-step title="选择文件" />
        <el-step title="填写论文信息" />
        <el-step title="提交" />
      </el-steps>

      <!-- 步骤1：选择文件 -->
      <el-card v-if="step === 0">
        <el-upload
          drag
          :auto-upload="false"
          :on-change="onFileChange"
          accept=".pdf"
          :show-file-list="false"
        >
          <el-icon style="font-size:48px;color:#c0c4cc"><Upload /></el-icon>
          <div style="color:#606266;margin-top:8px">将 PDF 拖到此处，或 <em style="color:#409eff">点击上传</em></div>
          <template #tip>
            <div style="color:#909399;font-size:0.85rem;margin-top:8px">仅支持 PDF</div>
          </template>
        </el-upload>

        <div v-if="file" style="margin-top:16px">
          <div class="file-info">
            <el-icon><Document /></el-icon>
            <span>{{ file.name }} &nbsp; ({{ (file.size / 1024 / 1024).toFixed(2) }} MB)</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:12px;margin-top:16px">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <span style="color:#606266;width:70px;text-align:right;white-space:nowrap">论文类型：</span>
              <el-radio-group v-model="meta.paper_type">
                <el-radio value="journal">期刊论文</el-radio>
                <el-radio value="conference">会议论文</el-radio>
              </el-radio-group>
            </div>
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <span style="color:#606266;width:70px;text-align:right;white-space:nowrap">论文学科：</span>
              <el-select v-model="meta.domain" placeholder="选择学科（可搜索）" filterable style="width:300px">
                <el-option v-for="d in DISCIPLINES" :key="d.value" :label="d.label" :value="d.value" />
              </el-select>
            </div>
            <div style="display:flex;justify-content:flex-end;margin-top:4px">
              <el-button type="primary" @click="goToStep2" :loading="extracting" :disabled="!meta.domain">
                {{ extracting ? '正在识别论文信息...' : '下一步' }}
              </el-button>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 步骤2：填写元数据 -->
      <el-card v-if="step === 1">
        <el-alert
          v-if="extractedAuto"
          type="success"
          title="已自动识别论文信息，请核对并补充"
          :closable="false"
          style="margin-bottom:16px"
        />

        <el-form :model="meta" label-width="140px" label-position="right">

          <el-form-item label="中文标题" required>
            <el-input v-model="meta.title" placeholder="论文的中文标题" />
          </el-form-item>

          <el-form-item label="论文类型" required>
            <el-radio-group v-model="meta.paper_type" @change="onTypeChange">
              <el-radio value="journal">期刊论文</el-radio>
              <el-radio value="conference">会议论文</el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- 期刊专属字段 -->
          <template v-if="meta.paper_type === 'journal'">
            <el-form-item label="期刊名称" required>
              <el-input v-model="meta.journal" placeholder="如：中国科学 / 计算机学报" />
            </el-form-item>
            <el-form-item label="发表年份" required>
              <el-input-number v-model="meta.year" :min="1900" :max="2099" controls-position="right" />
            </el-form-item>
            <el-form-item label="期刊分区/收录" required>
              <el-select
                v-model="meta.divisionTags"
                multiple filterable allow-create clearable
                collapse-tags collapse-tags-tooltip
                placeholder="可多选，也可直接输入自定义值"
                style="width:400px"
                @change="onDivisionChange"
              >
                <el-option value="无分区/未分类" label="无分区/未分类" />
                <el-option-group label="中科院分区">
                  <el-option label="中科院一区" value="中科院一区" />
                  <el-option label="中科院二区" value="中科院二区" />
                  <el-option label="中科院三区" value="中科院三区" />
                  <el-option label="中科院四区" value="中科院四区" />
                </el-option-group>
                <el-option-group label="JCR 分区">
                  <el-option label="Q1" value="Q1" />
                  <el-option label="Q2" value="Q2" />
                  <el-option label="Q3" value="Q3" />
                  <el-option label="Q4" value="Q4" />
                </el-option-group>
                <el-option-group label="检索收录">
                  <el-option label="SCI" value="SCI" />
                  <el-option label="SSCI" value="SSCI" />
                  <el-option label="EI" value="EI" />
                  <el-option label="Scopus" value="Scopus" />
                  <el-option label="CSCD" value="CSCD" />
                  <el-option label="北大核心" value="北大核心" />
                  <el-option label="CSSCI" value="CSSCI" />
                </el-option-group>
              </el-select>
            </el-form-item>
          </template>

          <!-- 会议专属字段 -->
          <template v-if="meta.paper_type === 'conference'">
            <el-form-item label="会议名称" required>
              <el-input v-model="meta.journal" placeholder="如：CNKI 2024" />
            </el-form-item>
            <el-form-item label="举办年份" required>
              <el-input-number v-model="meta.year" :min="1900" :max="2099" controls-position="right" />
            </el-form-item>
            <el-form-item label="会议级别/分区" required>
              <el-select
                v-model="meta.divisionTags"
                multiple filterable allow-create clearable
                collapse-tags collapse-tags-tooltip
                placeholder="可多选，也可直接输入自定义值"
                style="width:400px"
                @change="onDivisionChange"
              >
                <el-option value="无分区/未分类" label="无分区/未分类" />
                <el-option-group label="CCF 分类">
                  <el-option label="CCF-A" value="CCF-A" />
                  <el-option label="CCF-B" value="CCF-B" />
                  <el-option label="CCF-C" value="CCF-C" />
                  <el-option label="非CCF" value="非CCF" />
                </el-option-group>
                <el-option-group label="检索收录">
                  <el-option label="EI" value="EI" />
                  <el-option label="Scopus" value="Scopus" />
                </el-option-group>
              </el-select>
            </el-form-item>
          </template>

          <el-form-item label="DOI（选填）">
            <el-input v-model="meta.doi" placeholder="如：10.1000/xxx" style="width:360px" />
          </el-form-item>

        </el-form>

        <div style="display:flex;gap:12px;margin-top:16px">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" @click="submit" :loading="uploading" :disabled="!isFormValid">
            提交并开始处理
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
import { Upload, Document } from '@element-plus/icons-vue'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import { DISCIPLINES } from '@/constants/disciplines.js'
import { useDefaultDomain } from '@/composables/useDefaultDomain.js'

const { defaultDomain } = useDefaultDomain()
const router = useRouter()
const step = ref(0)
const file = ref(null)
const extracting = ref(false)
const uploading = ref(false)
const extractedAuto = ref(false)

const meta = ref({
  title: '',
  paper_type: 'journal',
  journal: '',
  divisionTags: [],
  year: new Date().getFullYear(),
  doi: '',
  domain: defaultDomain.value,
})

const NO_DIVISION = '无分区/未分类'
const JOURNAL_GROUPS = [
  ['中科院一区', '中科院二区', '中科院三区', '中科院四区'],
  ['Q1', 'Q2', 'Q3', 'Q4'],
]
const CONFERENCE_GROUPS = [
  ['CCF-A', 'CCF-B', 'CCF-C', '非CCF'],
]

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

function onTypeChange() { meta.value.divisionTags = [] }

watch(() => meta.value.domain, (val) => { defaultDomain.value = val })

const isFormValid = computed(() => {
  const m = meta.value
  if (!m.title.trim() || !m.journal.trim()) return false
  if (!m.year) return false
  if (m.divisionTags.length === 0) return false
  if (!m.domain) return false
  return true
})

function onFileChange(uploadFile) {
  file.value = uploadFile.raw
}

async function goToStep2() {
  extracting.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.value)
    if (meta.value.domain) formData.append('domain', meta.value.domain)
    formData.append('paper_type', meta.value.paper_type)
    const res = await api.post('/papers/extract-metadata', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const data = res.data
    if (data.title) {
      // 中文论文：优先用 title_zh，其次用 title
      meta.value.title = data.title_zh || data.title || meta.value.title
      meta.value.journal = data.journal || meta.value.journal
      meta.value.year = parseInt(data.year) || meta.value.year
      meta.value.doi = data.doi || meta.value.doi
      if (data.division_tags?.length) meta.value.divisionTags = data.division_tags
      extractedAuto.value = true
    }
    step.value = 1
  } catch (err) {
    ElMessage.warning('元数据识别失败，请手动填写')
    step.value = 1
  } finally {
    extracting.value = false
  }
}

async function submit() {
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.value)
    formData.append('title', meta.value.title.trim())
    formData.append('paper_type', meta.value.paper_type)
    formData.append('journal', meta.value.journal)
    formData.append('year', meta.value.year)
    formData.append('doi', meta.value.doi)
    formData.append('division', meta.value.divisionTags.join('、'))
    if (meta.value.domain) formData.append('domain', meta.value.domain)

    const res = await api.post('/papers/upload-chinese', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success('已提交，正在处理...')
    router.push('/jobs')
  } catch (err) {
    ElMessage.error('提交失败：' + (err.response?.data?.detail || err.message))
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 720px; margin: 0 auto; padding: 24px; }
.file-info {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; background: #f5f7fa;
  border-radius: 6px; color: #606266; font-size: 0.9rem;
}
</style>
