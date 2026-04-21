<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <h2>我的论文库</h2>
      <el-row :gutter="16" class="search-bar">
        <el-col :span="16">
          <el-input v-model="query" placeholder="输入标题关键词..." clearable @keyup.enter="resetAndSearch" />
        </el-col>
        <el-col :span="4">
          <el-input v-model.number="year" placeholder="年份" clearable @keyup.enter="resetAndSearch" />
        </el-col>
        <el-col :span="4">
          <el-button type="primary" @click="resetAndSearch" :loading="loading" style="width:100%">搜索</el-button>
        </el-col>
      </el-row>

      <div class="masonry">
        <PaperCard v-for="paper in papers" :key="paper.id" :paper="paper" />
      </div>

      <el-empty v-if="!loading && papers.length === 0" description="未找到相关论文" />

      <!-- 懒加载触发锚点 -->
      <div ref="sentinel" style="height:1px" />

      <div v-if="loading" style="text-align:center;padding:24px;color:#909399">
        加载中...
      </div>
      <div v-else-if="!hasMore && papers.length > 0" style="text-align:center;padding:24px;color:#c0c4cc;font-size:0.85rem">
        已加载全部 {{ total }} 篇
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import PaperCard from '@/components/PaperCard.vue'

const PAGE_SIZE = 10

const query = ref('')
const year = ref(null)
const papers = ref([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const hasMore = ref(true)
const sentinel = ref(null)

function sentinelVisible() {
  if (!sentinel.value) return false
  return sentinel.value.getBoundingClientRect().top <= window.innerHeight + 300
}

async function loadPage() {
  if (loading.value || !hasMore.value) return
  loading.value = true
  try {
    const res = await api.get('/papers/search', {
      params: { q: query.value, year: year.value || undefined, page: page.value, page_size: PAGE_SIZE }
    })
    papers.value.push(...res.data.items)
    total.value = res.data.total
    hasMore.value = papers.value.length < total.value
    page.value++
  } finally {
    loading.value = false
    // 加载完后 sentinel 仍在视口内则继续加载（内容不足一屏的情况）
    await nextTick()
    if (hasMore.value && sentinelVisible()) loadPage()
  }
}

function resetAndSearch() {
  papers.value = []
  page.value = 1
  hasMore.value = true
  loadPage()
}

let observer = null
onMounted(() => {
  observer = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) loadPage()
  }, { rootMargin: '300px' })
  if (sentinel.value) observer.observe(sentinel.value)
  loadPage()
})

onUnmounted(() => observer?.disconnect())
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 1200px; margin: 0 auto; padding: 24px; }
.search-bar { margin-bottom: 24px; }
.masonry { columns: 3; column-gap: 16px; }
.masonry > * { break-inside: avoid; margin-bottom: 16px; }
</style>
