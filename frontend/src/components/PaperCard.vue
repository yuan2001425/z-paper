<template>
  <el-card class="paper-card" shadow="hover" @click="navigate">
    <div class="paper-title">{{ paper.title || '（无标题）' }}</div>
    <div v-if="paper.title_zh" class="paper-title-zh">{{ paper.title_zh }}</div>
    <div class="paper-meta">
      <span v-if="paper.year" class="year">{{ paper.year }}</span>
      <span v-if="paper.journal" class="journal">{{ paper.journal }}</span>
    </div>
    <div class="paper-abstract" v-if="paper.abstract">
      {{ paper.abstract.slice(0, 100) }}{{ paper.abstract.length > 100 ? '...' : '' }}
    </div>
    <div class="paper-tags">
      <el-tag v-for="kw in (paper.keywords || []).slice(0, 3)" :key="kw" size="small" type="info">{{ kw }}</el-tag>
    </div>
  </el-card>
</template>

<script setup>
import { useRouter } from 'vue-router'
const props = defineProps({ paper: Object })
const router = useRouter()
function navigate() {
  router.push(`/results/by-paper/${props.paper.id}`)
}
</script>

<style scoped>
.paper-card { cursor: pointer; margin-bottom: 16px; }
.paper-title { font-weight: 600; font-size: 0.95rem; color: #303133; line-height: 1.4; }
.paper-title-zh { font-size: 0.95rem; color: #409eff; font-weight: 600; margin-top: 4px; line-height: 1.4; }
.paper-meta { display: flex; gap: 10px; color: #909399; font-size: 0.82rem; margin-top: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.year { font-weight: 600; }
.journal { font-style: italic; }
.paper-abstract { color: #606266; font-size: 0.85rem; line-height: 1.5; margin-bottom: 10px; }
.paper-tags { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
