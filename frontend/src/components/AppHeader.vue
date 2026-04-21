<template>
  <header class="app-header">
    <div class="header-inner">
      <div class="logo" @click="$router.push('/')">z-paper</div>
      <nav class="nav">
        <el-link @click="$router.push('/search')">我的论文库</el-link>
        <el-link @click="uploadModal?.open()">上传论文</el-link>
        <el-link @click="$router.push('/jobs')">处理进度</el-link>
        <el-link @click="$router.push('/glossary')">专有名词</el-link>
        <el-link @click="$router.push('/chat')" style="color:#67c23a;font-weight:600">知识库对话</el-link>
        <el-link @click="$router.push('/settings')" style="color:#909399">⚙ 配置</el-link>
      </nav>
    </div>
  </header>
  <UploadTypeModal ref="uploadModal" />
</template>

<script setup>
import { ref } from 'vue'
import UploadTypeModal from '@/components/UploadTypeModal.vue'
const uploadModal = ref(null)
</script>

<style scoped>
.app-header { background: #fff; border-bottom: 1px solid #e4e7ed; position: sticky; top: 0; z-index: 100; }
.header-inner { max-width: 1400px; margin: 0 auto; padding: 0 24px; height: 60px; display: flex; align-items: center; justify-content: space-between; }
.logo { font-size: 1.4rem; font-weight: 700; color: #409eff; cursor: pointer; }
.nav { display: flex; align-items: center; gap: 24px; }
</style>
