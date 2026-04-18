<template>
  <div class="uploader">
    <el-upload
      drag
      :auto-upload="false"
      :on-change="onFileChange"
      accept=".pdf"
      :show-file-list="false"
    >
      <el-icon class="upload-icon"><Upload /></el-icon>
      <div class="upload-text">将 PDF 拖到此处，或 <em>点击上传</em></div>
      <template #tip>
        <div class="upload-tip">仅支持 PDF 格式，首页需包含标题</div>
      </template>
    </el-upload>

    <div v-if="file" class="file-info">
      <el-icon><Document /></el-icon>
      <span>{{ file.name }} ({{ (file.size / 1024 / 1024).toFixed(2) }} MB)</span>
    </div>

    <el-button
      v-if="file"
      type="primary"
      :loading="uploading"
      @click="upload"
      style="margin-top:16px;width:100%"
    >
      开始翻译
    </el-button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/index.js'

const emit = defineEmits(['uploaded'])
const file = ref(null)
const uploading = ref(false)

function onFileChange(uploadFile) {
  file.value = uploadFile.raw
}

async function upload() {
  if (!file.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.value)
    const res = await api.post('/papers/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    ElMessage.success('上传成功，翻译任务已创建！')
    emit('uploaded', { paperId: res.data.paper_id, jobId: res.data.job_id })
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '上传失败，请重试')
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.uploader { padding: 8px; }
.upload-icon { font-size: 48px; color: #c0c4cc; margin-bottom: 8px; }
.upload-text { color: #606266; }
.upload-text em { color: #409eff; font-style: normal; }
.upload-tip { color: #909399; font-size: 0.85rem; margin-top: 8px; }
.file-info { display: flex; align-items: center; gap: 8px; margin-top: 16px; padding: 12px; background: #f5f7fa; border-radius: 4px; color: #606266; }
</style>
