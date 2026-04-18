<template>
  <div class="home-layout">
    <AppHeader />
    <main class="main-content">
      <div class="hero">
        <img :src="logoUrl" alt="z-paper" class="hero-logo" />
        <p>个人学术论文管理工具 · AI 驱动翻译 · 术语一致</p>
        <div class="hero-actions">
          <el-button type="primary" size="large" @click="uploadModal?.open()">
            上传论文
          </el-button>
          <el-button size="large" @click="$router.push('/search')">
            我的论文库
          </el-button>
          <el-button size="large" @click="aboutVisible = true">
            关于项目
          </el-button>
        </div>
      </div>

      <UploadTypeModal ref="uploadModal" />

      <!-- 关于项目弹框 -->
      <el-dialog
        v-model="aboutVisible"
        title="关于项目"
        width="780px"
        :close-on-click-modal="true"
        :modal="true"
        class="about-dialog"
        align-center
      >
        <div class="about-content" v-html="readmeHtml" />
      </el-dialog>

      <section class="paper-list">
        <h2>最近收录</h2>
        <el-row :gutter="16">
          <el-col v-for="paper in papers" :key="paper.id" :span="8">
            <PaperCard :paper="paper" />
          </el-col>
        </el-row>
        <el-empty v-if="papers.length === 0" description="还没有论文，上传第一篇开始吧" />
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import readmeMd from 'virtual:readme'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import PaperCard from '@/components/PaperCard.vue'
import UploadTypeModal from '@/components/UploadTypeModal.vue'
import logoSrc from '@/assets/logo.svg'

const logoUrl = logoSrc

const papers = ref([])
const uploadModal = ref(null)
const aboutVisible = ref(false)

const readmeHtml = computed(() => marked.parse(readmeMd))

onMounted(async () => {
  try {
    const res = await api.get('/papers/search', { params: { page_size: 9 } })
    papers.value = res.data.items
  } catch {}
})
</script>

<style scoped>
.home-layout { min-height: 100vh; background: #f5f7fa; }
.main-content { max-width: 1200px; margin: 0 auto; padding: 24px; }
.hero { text-align: center; padding: 32px 0 24px; }
.hero-logo { height: 96px; width: auto; margin: 0 auto 16px; display: block; }
.hero h1 { font-size: 3rem; font-weight: 700; color: #303133; margin-bottom: 12px; }
.hero p { font-size: 1.2rem; color: #606266; margin-bottom: 32px; }
.hero-actions { display: flex; justify-content: center; gap: 16px; }
.paper-list { margin-top: 40px; }
.paper-list h2 { font-size: 1.4rem; color: #303133; margin-bottom: 20px; }
</style>

<style>
/* 弹框：固定高度悬浮在视口中央，内容区独立滚动，页面不出滚动条 */
.about-dialog.el-dialog {
  max-height: 82vh;
  display: flex;
  flex-direction: column;
}
.about-dialog .el-dialog__header {
  flex-shrink: 0;
}
.about-dialog .el-dialog__body {
  flex: 1;
  min-height: 0;
  padding: 8px 24px 24px;
  overflow-y: auto;
}
.about-content {
  font-size: 0.92rem;
  color: #303133;
  line-height: 1.75;
}
.about-content h1 { font-size: 1.6rem; font-weight: 700; margin: 0 0 8px; }
.about-content h2 { font-size: 1.2rem; font-weight: 700; margin: 24px 0 10px; border-bottom: 1px solid #e4e7ed; padding-bottom: 6px; }
.about-content h3 { font-size: 1rem; font-weight: 700; margin: 18px 0 8px; color: #409eff; }
.about-content p { margin: 0 0 10px; }
.about-content ul, .about-content ol { padding-left: 20px; margin: 0 0 10px; }
.about-content li { margin-bottom: 4px; }
.about-content code {
  background: #f0f2f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 0.85em;
  color: #c0392b;
}
.about-content pre {
  background: #f6f8fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px 16px;
  overflow-x: auto;
  margin: 0 0 12px;
}
.about-content pre code {
  background: none;
  padding: 0;
  color: #303133;
  font-size: 0.82rem;
}
.about-content table {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 12px;
  font-size: 0.88rem;
}
.about-content th, .about-content td {
  border: 1px solid #e4e7ed;
  padding: 6px 12px;
  text-align: left;
}
.about-content th { background: #f5f7fa; font-weight: 600; }
.about-content blockquote {
  border-left: 3px solid #409eff;
  padding-left: 12px;
  color: #606266;
  margin: 0 0 12px;
}
.about-content hr { border: none; border-top: 1px solid #e4e7ed; margin: 20px 0; }
.about-content a { color: #409eff; text-decoration: none; }
.about-content a:hover { text-decoration: underline; }
</style>
