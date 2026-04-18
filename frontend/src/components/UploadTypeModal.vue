<template>
  <el-dialog
    v-model="visible"
    title="上传论文"
    width="420px"
    :close-on-click-modal="true"
    align-center
  >
    <div class="choices">
      <div class="choice-card" @click="choose('foreign')">
        <div class="choice-icon">🌐</div>
        <div class="choice-title">上传外文论文</div>
        <div class="choice-desc">PDF 解析 → 专业术语审查 → AI 翻译为中文</div>
      </div>
      <div class="choice-card" @click="choose('chinese')">
        <div class="choice-icon">📄</div>
        <div class="choice-title">上传中文论文</div>
        <div class="choice-desc">PDF 解析 → 文本整理 → 参考文献识别，无需翻译</div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const visible = ref(false)

function open() { visible.value = true }

function choose(type) {
  visible.value = false
  router.push(type === 'chinese' ? '/upload-chinese' : '/translate')
}

defineExpose({ open })
</script>

<style scoped>
.choices { display: flex; gap: 16px; padding: 8px 0 4px; }
.choice-card {
  flex: 1;
  border: 2px solid #e4e7ed;
  border-radius: 10px;
  padding: 20px 16px;
  cursor: pointer;
  text-align: center;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.choice-card:hover {
  border-color: #409eff;
  box-shadow: 0 2px 12px rgba(64,158,255,0.15);
}
.choice-icon { font-size: 2rem; margin-bottom: 10px; }
.choice-title { font-size: 1rem; font-weight: 600; color: #303133; margin-bottom: 8px; }
.choice-desc { font-size: 0.8rem; color: #909399; line-height: 1.5; }
</style>
