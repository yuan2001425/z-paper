<template>
  <el-card
    class="paper-card"
    :class="{ 'is-selected': selected }"
    shadow="hover"
    @click="handleClick"
  >
    <!-- 批量模式勾选框 -->
    <div v-if="batchMode" class="card-checkbox" @click.stop="$emit('toggle-select')">
      <el-checkbox :model-value="selected" />
    </div>

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

    <!-- 移动按钮（非批量模式，鼠标悬停显示） -->
    <div v-if="showMove && !batchMode" class="card-move-btn" @click.stop="$emit('move')">
      <el-icon><Rank /></el-icon>
      移动
    </div>
  </el-card>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { Rank } from '@element-plus/icons-vue'

const props = defineProps({
  paper:      Object,
  showMove:   { type: Boolean, default: false },
  batchMode:  { type: Boolean, default: false },
  selected:   { type: Boolean, default: false },
})
const emit = defineEmits(['move', 'toggle-select'])

const router = useRouter()

function handleClick() {
  if (props.batchMode) {
    emit('toggle-select')
  } else {
    router.push(`/results/by-paper/${props.paper.id}`)
  }
}
</script>

<style scoped>
.paper-card {
  cursor: pointer;
  margin-bottom: 16px;
  position: relative;
  transition: outline 0.15s;
}
.paper-card.is-selected {
  outline: 2px solid #409eff;
  outline-offset: 1px;
}

.card-checkbox {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 1;
}

.paper-title    { font-weight: 600; font-size: 0.95rem; color: #303133; line-height: 1.4; }
.paper-title-zh { font-size: 0.95rem; color: #409eff; font-weight: 600; margin-top: 4px; line-height: 1.4; }
.paper-meta     { display: flex; gap: 10px; color: #909399; font-size: 0.82rem; margin-top: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.year           { font-weight: 600; }
.journal        { font-style: italic; }
.paper-abstract { color: #606266; font-size: 0.85rem; line-height: 1.5; margin-bottom: 10px; }
.paper-tags     { display: flex; gap: 6px; flex-wrap: wrap; }

.card-move-btn {
  display: none;
  position: absolute;
  bottom: 8px;
  right: 8px;
  align-items: center;
  gap: 3px;
  font-size: 0.75rem;
  color: #fff;
  background: #409eff;
  border-radius: 4px;
  padding: 3px 8px;
  cursor: pointer;
  z-index: 1;
}
.paper-card:hover .card-move-btn { display: flex; }
</style>
