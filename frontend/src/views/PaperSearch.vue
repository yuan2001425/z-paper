<template>
  <div class="page-layout">
    <AppHeader />
    <main class="main-content">
      <h2>我的论文库</h2>
      <el-row :gutter="16" class="search-bar">
        <el-col :span="16">
          <el-input v-model="query" placeholder="输入标题关键词..." clearable @keyup.enter="search" />
        </el-col>
        <el-col :span="4">
          <el-input v-model.number="year" placeholder="年份" clearable />
        </el-col>
        <el-col :span="4">
          <el-button type="primary" @click="search" :loading="loading" style="width:100%">搜索</el-button>
        </el-col>
      </el-row>

      <div class="results">
        <el-row :gutter="16">
          <el-col v-for="paper in papers" :key="paper.id" :span="8">
            <PaperCard :paper="paper" />
          </el-col>
        </el-row>
        <el-empty v-if="!loading && papers.length === 0" description="未找到相关论文" />
        <el-pagination
          v-if="total > pageSize"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="(p) => { page = p; search() }"
          layout="prev, pager, next"
          style="margin-top:24px;text-align:center"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import PaperCard from '@/components/PaperCard.vue'

const query = ref('')
const year = ref(null)
const papers = ref([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 18

async function search() {
  loading.value = true
  try {
    const res = await api.get('/papers/search', {
      params: { q: query.value, year: year.value || undefined, page: page.value, page_size: pageSize }
    })
    papers.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

onMounted(search)
</script>

<style scoped>
.page-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 1200px; margin: 0 auto; padding: 24px; }
.search-bar { margin-bottom: 24px; }
</style>
