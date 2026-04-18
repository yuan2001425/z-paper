<template>
  <div class="translation-viewer" @click.capture="onCiteClick">
    <template v-for="(item, idx) in visibleItems" :key="idx">

      <!-- 图片块：中文模式单图，翻译模式双图 -->
      <div v-if="item['图片地址']" class="figure-block" :data-block-id="`img-${idx}`" :data-block-idx="item.__originalIdx">
        <!-- 中文论文：单图居中 -->
        <div v-if="chineseMode" class="figure-single">
          <img
            :src="item['图片地址']"
            alt="图片"
            class="figure-img"
            @click="openLightbox(item['图片地址'], '图片', idx, 'original')"
          />
        </div>
        <!-- 翻译论文：左右双图 -->
        <div v-else class="figure-pair">
          <div class="figure-side">
            <div class="figure-label">原图</div>
            <img
              :src="item['图片地址']"
              alt="原图"
              class="figure-img"
              @load="onOriginalLoad(idx, $event)"
              @click="openLightbox(item['图片地址'], '原图', idx, 'original')"
            />
            <button
              v-if="resultId && !translatedUrls[idx] && item['中文图片地址'] === item['图片地址']"
              class="translate-img-btn"
              :disabled="translatingBlocks[idx]"
              @click="translateImage(idx, item['图片地址'])"
              :title="`翻译图片内文字`"
            >
              <svg v-if="translatingBlocks[idx]" class="spin-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
              </svg>
              {{ translatingBlocks[idx] ? '翻译中...' : '翻译此图' }}
            </button>
          </div>
          <div class="figure-side">
            <div class="figure-label">中文图</div>
            <img
              :src="translatedUrls[idx] || item['中文图片地址'] || item['图片地址']"
              alt="中文图"
              class="figure-img"
              :style="originalSizes[idx]
                ? { maxHeight: originalSizes[idx].h + 'px', objectFit: 'contain' }
                : {}"
              @click="openLightbox(translatedUrls[idx] || item['中文图片地址'] || item['图片地址'], '中文图', idx, 'translated')"
            />
          </div>
        </div>
      </div>

      <!-- 一级标题 -->
      <div v-else-if="item['标题等级'] === 1" class="heading-block heading-1" :data-block-id="`h-${idx}`" :data-block-idx="item.__originalIdx">
        <h3 :class="chineseMode ? 'heading-zh' : 'heading-en'" v-html="renderKatex(item['文本'])" />
        <h3 v-if="!chineseMode && item['中文文本']" class="heading-zh" v-html="renderKatex(item['中文文本'])" />
      </div>

      <!-- 二级标题 -->
      <div v-else-if="item['标题等级'] === 2" class="heading-block heading-2" :data-block-id="`h-${idx}`" :data-block-idx="item.__originalIdx">
        <h4 :class="chineseMode ? 'heading-zh' : 'heading-en'" v-html="renderKatex(item['文本'])" />
        <h4 v-if="!chineseMode && item['中文文本']" class="heading-zh" v-html="renderKatex(item['中文文本'])" />
      </div>

      <!-- 三级标题 -->
      <div v-else-if="item['标题等级'] === 3" class="heading-block heading-3" :data-block-id="`h-${idx}`" :data-block-idx="item.__originalIdx">
        <h5 :class="chineseMode ? 'heading-zh' : 'heading-en'" v-html="renderKatex(item['文本'])" />
        <h5 v-if="!chineseMode && item['中文文本']" class="heading-zh" v-html="renderKatex(item['中文文本'])" />
      </div>

      <!-- 普通段落 -->
      <div
        v-else-if="item['文本']"
        class="paragraph-block"
        :class="chineseMode ? 'para-chinese' : layout === 'double' ? 'para-double' : 'para-single'"
        :data-block-id="`p-${idx}`"
        :data-block-idx="item.__originalIdx"
      >
        <!-- 中文模式：单列直接显示文本 -->
        <template v-if="chineseMode">
          <div class="zh-text" v-html="renderKatex(item['文本'])" />
        </template>
        <!-- 翻译模式：双列或单列 -->
        <template v-else>
          <div class="en-text" v-html="renderKatex(item['文本'])" />
          <div class="zh-text" v-html="renderKatex(item['中文文本'] || '')" />
        </template>
        <button class="annotate-btn" @click="onAnnotateClick(`p-${idx}`)" title="添加批注">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </button>
      </div>

    </template>
  </div>

  <!-- Lightbox -->
  <Teleport to="body">
    <div v-if="lightbox.visible" class="lb-overlay" @click.self="closeLightbox">
      <div class="lb-toolbar">
        <span class="lb-title">{{ lightbox.title }}</span>
        <div class="lb-actions">
          <button @click="zoom(0.25)" title="放大">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              <line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>
            </svg>
          </button>
          <button @click="zoom(-0.25)" title="缩小">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              <line x1="8" y1="11" x2="14" y2="11"/>
            </svg>
          </button>
          <button @click="lightbox.scale = lightbox.baseScale" title="适应屏幕">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
            </svg>
          </button>
          <button @click="downloadImage" title="下载">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
          </button>
          <button @click="closeLightbox" title="关闭">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="lb-stage" @wheel.prevent="onWheel">
        <img
          :src="lightbox.src"
          :style="{ transform: `scale(${lightbox.scale})` }"
          class="lb-img"
          draggable="false"
          @load="onLightboxLoad"
        />
      </div>
      <div class="lb-scale-hint">{{ Math.round(lightbox.scale * 100) }}%</div>
    </div>
  </Teleport>
</template>

<script setup>
import { reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import katex from 'katex'
import api from '@/api/index.js'

const props = defineProps({
  items: { type: Array, default: () => [] },
  layout: { type: String, default: 'double' },
  resultId: { type: String, default: '' },
  references: { type: Array, default: () => [] },
  chineseMode: { type: Boolean, default: false },
})
const emit = defineEmits(['add-annotation', 'jump-to-ref'])

// ── 参考文献标题过滤 ───────────────────────────────────────────────────────────
// 正文中的「参考文献 / References」标题与右侧面板重复，隐藏之
const REF_HEADING_RE = /^(参考文献|references|bibliography)\s*$/i
const visibleItems = computed(() =>
  props.items
    .map((item, idx) => ({ ...item, __originalIdx: idx }))
    .filter(item => {
      if (item['标题等级'] == null) return true
      const text = (item['文本'] || item['中文文本'] || '').trim()
      return !REF_HEADING_RE.test(text)
    })
)

// ── 图片尺寸同步 ──────────────────────────────────────────────────────────────

const originalSizes = reactive({})        // idx → { w, h }（原图实际渲染尺寸，供中文缩略图对齐）
const originalNaturalW = reactive({})     // idx → naturalWidth（原图自然像素宽，供 lightbox 初始 scale 计算）

function onOriginalLoad(idx, e) {
  const img = e.target
  originalSizes[idx] = { w: img.offsetWidth, h: img.offsetHeight }
  originalNaturalW[idx] = img.naturalWidth
}

// ── 图片翻译（开发用） ─────────────────────────────────────────────────────────

const translatingBlocks = reactive({})   // idx → true/false
const translatedUrls = reactive({})      // idx → 翻译后图片 URL

// 翻译完成后，若 lightbox 当前正在展示同一块的中文图，自动刷新为新 URL
watch(translatedUrls, (urls) => {
  if (!lightbox.visible || lightbox.side !== 'translated' || lightbox.blockIdx === null) return
  const newUrl = urls[lightbox.blockIdx]
  if (newUrl && newUrl !== lightbox.src) {
    lightbox.src = newUrl
  }
}, { deep: true })

async function translateImage(idx, imageUrl) {
  if (!props.resultId) return
  translatingBlocks[idx] = true
  try {
    const res = await api.post(
      `/results/${props.resultId}/translate-image`,
      { image_url: imageUrl, block_index: idx },
      { timeout: 180000 },   // 图片翻译可能需要 1-3 分钟
    )
    translatedUrls[idx] = res.data.translated_url
    if (res.data.changed) {
      ElMessage.success('图片翻译完成')
    } else {
      ElMessage.info('图片无需翻译（未识别到可翻译文字）')
    }
  } catch {
    // 错误信息已由 api 拦截器统一弹出
  } finally {
    translatingBlocks[idx] = false
  }
}

// ── Lightbox ──────────────────────────────────────────────────────────────────

const lightbox = reactive({
  visible: false,
  src: '',
  title: '',
  scale: 1,
  baseScale: 1,   // "100%" 基准：原图=1，中文图=原图宽/中文图宽（使两者等效大小一致）
  blockIdx: null,
  side: '',       // 'original' | 'translated'
  originNatW: 0,  // 原图自然像素宽，供中文图 lightbox 计算 baseScale
})

function openLightbox(src, title, blockIdx = null, side = '') {
  lightbox.src = src
  lightbox.title = title
  lightbox.scale = 1
  lightbox.baseScale = 1
  lightbox.visible = true
  lightbox.blockIdx = blockIdx
  lightbox.side = side
  lightbox.originNatW = (side === 'translated' && blockIdx !== null)
    ? (originalNaturalW[blockIdx] || 0)
    : 0
  document.body.style.overflow = 'hidden'
}

// lightbox 图片加载后，中文图按原图自然宽推算 baseScale，使"100%"等效原图大小
function onLightboxLoad(e) {
  if (lightbox.originNatW && e.target.naturalWidth) {
    lightbox.baseScale = lightbox.originNatW / e.target.naturalWidth
    lightbox.scale = lightbox.baseScale
  }
}


function closeLightbox() {
  lightbox.visible = false
  document.body.style.overflow = ''
}

function zoom(delta) {
  lightbox.scale = Math.min(4, Math.max(0.25, lightbox.scale + delta))
}

function onWheel(e) {
  zoom(e.deltaY < 0 ? 0.15 : -0.15)
}

function downloadImage() {
  const a = document.createElement('a')
  a.href = lightbox.src
  a.download = lightbox.title + '_' + lightbox.src.split('/').pop()
  a.click()
}

function onKeydown(e) {
  if (!lightbox.visible) return
  if (e.key === 'Escape') closeLightbox()
  if (e.key === '+' || e.key === '=') zoom(0.25)
  if (e.key === '-') zoom(-0.25)
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))

// ── KaTeX ─────────────────────────────────────────────────────────────────────

function renderKatex(text) {
  if (!text) return ''
  let result = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, formula) => {
    try { return katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false }) }
    catch { return `$$${formula}$$` }
  })
  result = result.replace(/\$([^$\n]+?)\$/g, (_, formula) => {
    try { return katex.renderToString(formula, { displayMode: false, throwOnError: false }) }
    catch { return `$${formula}$` }
  })
  // 将 [1] [1,2] 等括号引用标记转为可点击的 tag
  result = result.replace(/\[(\d+(?:,\s*\d+)*)\]/g, (_, inner) => {
    return inner.split(',').map(n => {
      const num = n.trim()
      return `<span class="cite-link" data-ref="${num}">[${num}]</span>`
    }).join('')
  })
  // 去掉连续引用 tag 之间的逗号：[1],[2] → [1][2]
  result = result.replace(/(<\/span>)\s*,\s*(<span class="cite-link")/g, '$1$2')
  return result
}

function onCiteClick(e) {
  const el = e.target.closest('.cite-link')
  if (!el) return
  e.stopPropagation()
  const num = parseInt(el.dataset.ref)
  if (num) emit('jump-to-ref', num)
}

function onAnnotateClick(blockId) {
  const selection = window.getSelection()
  const selectedText = selection && !selection.isCollapsed ? selection.toString() : ''
  emit('add-annotation', { blockId, selectedText })
}
</script>

<style scoped>
.translation-viewer { font-family: 'Times New Roman', serif; line-height: 1.8; }

/* ── 标题 ── */
.heading-block { margin: 20px 0 8px; }
.heading-en { color: #606266; margin: 0 0 2px; }
.heading-zh { color: #303133; margin: 0; }

.heading-1 .heading-en,
.heading-1 .heading-zh { font-size: 1.05rem; font-weight: 700; }
.heading-2 .heading-en,
.heading-2 .heading-zh { font-size: 0.98rem; font-weight: 600; }
.heading-3 .heading-en,
.heading-3 .heading-zh { font-size: 0.93rem; font-weight: 600; }

/* ── 段落 ── */
.paragraph-block { position: relative; }
.annotate-btn {
  position: absolute; top: 4px; right: -28px;
  width: 22px; height: 22px;
  border: 1px solid #dcdfe6; border-radius: 4px;
  background: #fff; cursor: pointer; color: #909399;
  display: flex; align-items: center; justify-content: center;
  padding: 0; transition: color 0.2s, border-color 0.2s;
}
.annotate-btn:hover { color: #409eff; border-color: #409eff; }

.paragraph-block.para-double {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 16px; margin-bottom: 20px;
}
.para-double .en-text { color: #606266; font-size: 0.9rem; padding-right: 12px; border-right: 1px solid #e4e7ed; text-align: justify; }
.para-double .zh-text { color: #303133; font-size: 0.95rem; text-align: justify; line-height: 2; }

.paragraph-block.para-single { display: block; margin-bottom: 32px; }
.para-single .en-text { color: #606266; font-size: 0.9rem; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px dashed #e4e7ed; text-align: justify; }
.para-single .zh-text { color: #303133; font-size: 0.95rem; text-align: justify; line-height: 2; }

.paragraph-block.para-chinese { display: block; margin-bottom: 20px; }
.para-chinese .zh-text { color: #303133; font-size: 0.95rem; text-align: justify; line-height: 1.9; }

/* ── 图片块 ── */
/* 侧边栏 300px + gap 24px + content-area padding-right 32px = 356px 非内容占用
   双栏：每张图再除以 2，减去列间距 12px 的一半 = 6px
   单栏：同样限制为半幅，与双栏保持一致 */
.figure-block { margin: 24px 0; --img-max-w: calc(((100vw - 356px) / 2 - 6px) * 0.9); }

.figure-single { text-align: center; }
.figure-single .figure-img { max-width: var(--img-max-w); max-height: 400px; object-fit: contain; border-radius: 4px; cursor: zoom-in; }
.figure-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: start;
}
.figure-side { display: flex; flex-direction: column; align-items: center; gap: 6px; min-width: 0; }
.figure-label {
  font-size: 0.78rem; color: #909399;
  background: #f5f7fa; border: 1px solid #e4e7ed;
  border-radius: 3px; padding: 1px 8px;
  align-self: flex-start;
}
.figure-img {
  max-width: var(--img-max-w);
  max-height: 400px;
  object-fit: contain;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  cursor: zoom-in;
  transition: box-shadow 0.2s;
}
.figure-img:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.15); }

.translate-img-btn {
  margin-top: 6px;
  align-self: flex-start;
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; font-size: 0.75rem;
  border: 1px dashed #c0c4cc; border-radius: 3px;
  background: #f5f7fa; color: #606266; cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.translate-img-btn:hover:not(:disabled) { color: #409eff; border-color: #409eff; background: #ecf5ff; }
.translate-img-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.spin-icon { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* ── Lightbox ── */
.lb-overlay {
  position: fixed; inset: 0; z-index: 9999;
  background: rgba(0, 0, 0, 0.85);
  display: flex; flex-direction: column;
}
.lb-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px;
  background: rgba(0,0,0,0.6);
  color: #fff; flex-shrink: 0;
}
.lb-title { font-size: 0.9rem; color: #ccc; }
.lb-actions { display: flex; gap: 8px; }
.lb-actions button {
  background: transparent; border: 1px solid rgba(255,255,255,0.25);
  border-radius: 4px; color: #fff; cursor: pointer;
  padding: 5px; display: flex; align-items: center; justify-content: center;
  transition: background 0.15s;
}
.lb-actions button:hover { background: rgba(255,255,255,0.15); }

.lb-stage {
  flex: 1; overflow: auto;
  display: flex; align-items: center; justify-content: center;
  cursor: grab;
}
.lb-stage:active { cursor: grabbing; }
.lb-img {
  max-width: none;
  transform-origin: center center;
  transition: transform 0.15s ease;
  user-select: none;
}
.lb-scale-hint {
  text-align: center; padding: 6px;
  color: rgba(255,255,255,0.5); font-size: 0.78rem;
  flex-shrink: 0;
}

/* ── 引用标记（el-tag 风格，用 :deep 穿透 scoped） ── */
:deep(.cite-link) {
  display: inline-block;
  vertical-align: super;
  line-height: 1;
  padding: 0 4px;
  font-size: 0.7em;
  font-family: inherit;
  color: #409eff;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 3px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
:deep(.cite-link:hover) {
  color: #fff;
  background: #409eff;
  border-color: #409eff;
}
</style>
