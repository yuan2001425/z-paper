<template>
  <div class="reader-layout">
    <AppHeader />
    <main class="reader-main">
      <!-- 工具栏 -->
      <div class="toolbar">
        <el-radio-group v-if="!chineseMode" v-model="layout" size="small">
          <el-radio-button value="double">双栏</el-radio-button>
          <el-radio-button value="single">单栏</el-radio-button>
        </el-radio-group>
        <span v-else style="font-size:0.85rem;color:#909399">中文论文 · 单列阅读</span>
        <el-button
          v-if="result?.pdf_url"
          type="primary"
          size="small"
          :icon="Document"
          @click="openPdf"
        >
          查看原文 PDF
        </el-button>
      </div>

      <div class="reader-body">
        <!-- 正文内容 -->
        <div class="content-area" ref="contentRef">
          <div v-if="result" class="paper-content">
            <div v-if="paperMeta" class="paper-header">
              <h1 class="paper-title-en">{{ paperMeta.title }}</h1>
              <h2 v-if="paperMeta.title_zh" class="paper-title-zh">{{ paperMeta.title_zh }}</h2>
              <div v-if="paperMeta.journal || paperMeta.year || paperMeta.doi || paperMeta.tags?.length" class="paper-source">
                <span v-if="paperMeta.journal">{{ paperMeta.journal }}</span>
                <span v-if="paperMeta.year">（{{ paperMeta.year }}）</span>
                <span v-if="paperMeta.doi" class="paper-doi">DOI: {{ paperMeta.doi }}</span>
                <el-tag v-for="tag in (paperMeta.tags || [])" :key="tag" size="small" type="info" style="margin-left:4px;vertical-align:middle">{{ tag }}</el-tag>
              </div>
            </div>
            <TranslationViewer
              :items="zhengwen"
              :layout="chineseMode ? 'single' : layout"
              :chinese-mode="chineseMode"
              :result-id="result?.id"
              :references="references"
              @add-annotation="onAddAnnotation"
              @jump-to-ref="scrollToRef"
            />
          </div>
          <el-skeleton v-else :rows="20" animated />
        </div>

        <!-- 右侧固定列 -->
        <div class="right-panel">
          <!-- 批注侧边栏 -->
          <div class="annotation-area">
            <AnnotationSidebar
              v-if="result"
              ref="sidebarRef"
              :result-id="result?.id"
              :content-ref="contentRef"
            />
          </div>

          <!-- 参考文献 -->
          <div class="refs-area" v-if="references.length">
            <p class="refs-tip">参考文献识别结果仅供参考</p>
            <div class="refs-header" @click="refsExpanded = !refsExpanded">
              <span class="refs-title">参考文献（{{ references.length }}）</span>
              <el-icon class="refs-arrow" :class="{ 'is-expanded': refsExpanded }"><ArrowRight /></el-icon>
            </div>
            <transition name="refs-slide">
              <ol v-if="refsExpanded" class="refs-list" ref="refsListEl">
                <li v-for="(ref, i) in references" :key="i" class="refs-item" :data-ref-idx="i">{{ ref }}</li>
              </ol>
            </transition>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Document } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import TranslationViewer from '@/components/TranslationViewer.vue'
import AnnotationSidebar from '@/components/AnnotationSidebar.vue'

const route = useRoute()
const router = useRouter()
const resultPath = route.params.jobId
  ? `by-job/${route.params.jobId}`
  : route.params.paperId
    ? `by-paper/${route.params.paperId}`
    : route.params.resultId
const result = ref(null)
const contentRef = ref(null)
const sidebarRef = ref(null)
const refsExpanded = ref(true)
const refsListEl = ref(null)

const chineseMode = computed(() => result.value?.structure_json?.paper_type === 'chinese')
const layout = ref('double')

const references = computed(() => result.value?.structure_json?.['参考文献'] || [])
const paperMeta = computed(() => {
  const s = result.value?.structure_json
  if (!s) return null
  return {
    title:    s['标题']            || '',
    title_zh: s['标题中文']        || '',
    journal:  s['所属期刊/会议']   || '',
    year:     s['年份']            || '',
    doi:      s['DOI']             || '',
    tags:     s['期刊/会议分类标签'] || [],
  }
})
const zhengwen = computed(() => result.value?.structure_json?.['正文'] || [])

onMounted(async () => {
  try {
    const res = await api.get(`/results/${resultPath}`)
    result.value = res.data
    await nextTick()
    if (route.query.block != null) {
      scrollToBlockIdx(Number(route.query.block))
    } else if (route.query.bid) {
      scrollToBlockId(route.query.bid)
    }
  } catch (err) {
    if (err.response?.status === 404) {
      ElMessage.warning('译文尚未生成，请等待翻译完成')
      router.push('/jobs')
    }
  }
})

function scrollToBlockIdx(blockIdx) {
  // 段落/标题：用原始数组下标（data-block-idx）
  _highlightEl(contentRef.value?.querySelector(`[data-block-idx="${blockIdx}"]`))
}

function scrollToBlockId(blockId) {
  // 批注：用 "p-N" 格式的 data-block-id
  _highlightEl(contentRef.value?.querySelector(`[data-block-id="${blockId}"]`))
}

function _highlightEl(el) {
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('block--highlight')
  setTimeout(() => el.classList.remove('block--highlight'), 2500)
}

function scrollToRef(num) {
  refsExpanded.value = true
  nextTick(() => {
    const item = refsListEl.value?.querySelector(`[data-ref-idx="${num - 1}"]`)
    if (!item) return
    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    item.classList.add('refs-item--highlight')
    setTimeout(() => item.classList.remove('refs-item--highlight'), 2000)
  })
}

function onAddAnnotation(info) {
  sidebarRef.value?.openInlineForm(info)
}

function openPdf() {
  if (result.value?.pdf_url) window.open(result.value.pdf_url, '_blank')
}
</script>

<style scoped>
.reader-layout { min-height: 100vh; background: #fff; }
.reader-main { max-width: 1400px; margin: 0 auto; padding: 16px 24px; }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.reader-body { display: grid; grid-template-columns: 1fr 300px; gap: 24px; align-items: start; }
.content-area { padding-right: 32px; min-width: 0; overflow-x: hidden; }

.paper-header {
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid #e4e7ed;
}
.paper-title-en { font-size: 1.15rem; font-weight: 700; color: #303133; margin: 0 0 4px; line-height: 1.4; font-family: 'Times New Roman', serif; }
.paper-title-zh { font-size: 1rem; font-weight: 600; color: #409eff; margin: 0 0 6px; line-height: 1.4; }
.paper-source { font-size: 0.8rem; color: #909399; }
.paper-source span { margin-right: 8px; }
.paper-doi { font-style: italic; }

/* 右侧固定列 */
.right-panel {
  position: sticky;
  top: 108px;
  height: calc(100vh - 124px);
  display: flex;
  flex-direction: column;
  border-left: 1px solid #e4e7ed;
  padding-left: 16px;
  overflow: hidden;
}

/* 批注区：占满上半，内部 sidebar 自行滚动 */
.annotation-area {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 参考文献区：最多占下半，独立滚动 */
.refs-area {
  flex: 0 1 50%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-top: 1px solid #e4e7ed;
  padding-top: 12px;
  margin-top: 12px;
}
.refs-tip { font-size: 0.75rem; color: #909399; margin: 0 0 8px; line-height: 1.5; flex-shrink: 0; }
.refs-header {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; user-select: none; padding: 4px 0; flex-shrink: 0;
}
.refs-header:hover .refs-title { color: #409eff; }
.refs-title { font-size: 0.88rem; font-weight: 600; color: #606266; }
.refs-arrow { font-size: 12px; color: #909399; transition: transform 0.2s; }
.refs-arrow.is-expanded { transform: rotate(90deg); }
.refs-list { margin: 8px 0 0; padding-left: 20px; overflow-y: auto; flex: 1; min-height: 0; }
.refs-item {
  font-size: 0.78rem; color: #606266; line-height: 1.5;
  margin-bottom: 6px; word-break: break-all;
  transition: background 0.3s; border-radius: 3px; padding: 2px 4px;
}
.refs-item--highlight { background: #ecf5ff; color: #303133; }
.refs-slide-enter-active,
.refs-slide-leave-active { transition: opacity 0.2s; overflow: hidden; }
.refs-slide-enter-from,
.refs-slide-leave-to { opacity: 0; }
.refs-slide-enter-to,
.refs-slide-leave-from { opacity: 1; }

/* 引用跳转高亮 */
:global(.block--highlight) {
  animation: block-highlight-fade 2.5s ease forwards;
}
@keyframes block-highlight-fade {
  0%   { background-color: #ecf5ff; outline: 2px solid #409eff; border-radius: 4px; }
  60%  { background-color: #ecf5ff; outline: 2px solid #409eff; border-radius: 4px; }
  100% { background-color: transparent; outline: 2px solid transparent; }
}
</style>
