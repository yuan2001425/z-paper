<template>
  <div class="page-layout">
    <AppHeader />
    <div class="library-body">

      <!-- ── 左侧文件夹面板 ────────────────────────────────────────────────── -->
      <aside class="folder-panel">
        <div class="folder-panel-header">
          <span class="folder-panel-title">文件夹</span>
          <el-button size="small" type="primary" text @click="openCreateFolder(null)">+ 新建</el-button>
        </div>

        <el-tree
          ref="treeRef"
          :data="treeData"
          node-key="key"
          :current-node-key="currentFolderKey"
          :highlight-current="true"
          :default-expand-all="true"
          :expand-on-click-node="false"
          class="folder-tree"
          @node-click="onNodeClick"
        >
          <template #default="{ data }">
            <div class="tree-node-row">
              <span class="tree-node-label">{{ data.name }}</span>
              <span class="tree-node-count">{{ data.count }}</span>
              <span v-if="data.type === 'folder'" class="tree-node-actions">
                <el-tooltip v-if="data.depth < 3" content="新建子文件夹" placement="top" :show-after="600">
                  <span class="tna-btn" @click.stop="openCreateFolder(data.id)">+</span>
                </el-tooltip>
                <el-tooltip content="重命名" placement="top" :show-after="600">
                  <span class="tna-btn" @click.stop="openRename(data)">✎</span>
                </el-tooltip>
                <el-tooltip content="删除" placement="top" :show-after="600">
                  <span class="tna-btn danger" @click.stop="confirmDeleteFolder(data)">✕</span>
                </el-tooltip>
              </span>
            </div>
          </template>
        </el-tree>
      </aside>

      <!-- ── 右侧论文列表 ─────────────────────────────────────────────────── -->
      <main class="paper-main">
        <div class="search-bar">
          <el-input v-model="query" placeholder="输入标题关键词..." clearable style="flex:1" @keyup.enter="resetAndSearch" />
          <el-input v-model.number="year" placeholder="年份" clearable style="width:100px" @keyup.enter="resetAndSearch" />
          <el-button type="primary" @click="resetAndSearch" :loading="loading">搜索</el-button>
          <el-button :type="batchMode ? 'warning' : ''" @click="toggleBatchMode">
            {{ batchMode ? `已选 ${selectedIds.size} 篇` : '批量管理' }}
          </el-button>
        </div>

        <div v-if="batchMode" class="batch-bar">
          <el-checkbox
            :model-value="selectedIds.size > 0 && selectedIds.size === papers.length"
            :indeterminate="selectedIds.size > 0 && selectedIds.size < papers.length"
            @change="toggleSelectAll"
          >全选</el-checkbox>
          <el-button size="small" type="primary" :disabled="selectedIds.size === 0" @click="openMoveDialog()">
            移动到文件夹（{{ selectedIds.size }}）
          </el-button>
          <el-button size="small" type="info" text @click="batchMode = false; selectedIds.clear()">退出批量</el-button>
        </div>

        <div class="masonry">
          <PaperCard
            v-for="paper in papers"
            :key="paper.id"
            :paper="paper"
            :show-move="true"
            :selected="selectedIds.has(paper.id)"
            :batch-mode="batchMode"
            @toggle-select="toggleSelect(paper.id)"
            @move="openMoveDialog(paper.id)"
          />
        </div>

        <el-empty v-if="!loading && papers.length === 0" description="此文件夹暂无论文" />
        <div ref="sentinel" style="height:1px" />
        <div v-if="loading" style="text-align:center;padding:24px;color:#909399">加载中…</div>
        <div v-else-if="!hasMore && papers.length > 0"
          style="text-align:center;padding:24px;color:#c0c4cc;font-size:0.85rem">
          已加载全部 {{ total }} 篇
        </div>
      </main>
    </div>

    <!-- ── 新建 / 重命名文件夹 ───────────────────────────────────────────── -->
    <el-dialog
      v-model="folderDialog.visible"
      :title="folderDialog.isRename ? '重命名文件夹' : '新建文件夹'"
      width="360px" align-center
    >
      <el-input
        v-model="folderDialog.name"
        placeholder="文件夹名称"
        autofocus
        @keyup.enter="submitFolder"
      />
      <template #footer>
        <el-button @click="folderDialog.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!folderDialog.name.trim()" @click="submitFolder">确定</el-button>
      </template>
    </el-dialog>

    <!-- ── 删除文件夹 ──────────────────────────────────────────────────── -->
    <el-dialog v-model="deleteDialog.visible" title="删除文件夹" width="420px" align-center>
      <p>确认删除文件夹 <strong>「{{ deleteDialog.folder?.name }}」</strong>？</p>
      <el-radio-group
        v-model="deleteDialog.strategy"
        style="display:flex;flex-direction:column;gap:12px;margin-top:16px"
      >
        <el-radio value="unclassified">保留论文，移入「未分类」</el-radio>
        <el-radio value="physical">
          <span style="color:#f56c6c">同时从数据库和磁盘删除文件夹内的论文</span>
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="deleteDialog.visible = false">取消</el-button>
        <el-button type="danger" @click="doDeleteFolder">确认</el-button>
      </template>
    </el-dialog>

    <!-- ── 移动论文 ────────────────────────────────────────────────────── -->
    <el-dialog v-model="moveDialog.visible" title="移动到文件夹" width="380px" align-center>
      <el-tree
        :data="moveTreeData"
        node-key="key"
        :current-node-key="moveDialog.targetKey"
        :highlight-current="true"
        :default-expand-all="true"
        :expand-on-click-node="false"
        class="move-tree"
        @node-click="d => moveDialog.targetKey = d.key"
      >
        <template #default="{ data }">
          <span>{{ data.name }}</span>
          <span class="tree-node-count" style="margin-left:6px">{{ data.count }}</span>
        </template>
      </el-tree>
      <template #footer>
        <el-button @click="moveDialog.visible = false">取消</el-button>
        <el-button type="primary" :disabled="moveDialog.targetKey === null" @click="doMove">移动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/index.js'
import AppHeader from '@/components/AppHeader.vue'
import PaperCard from '@/components/PaperCard.vue'

const PAGE_SIZE = 12

// ── 搜索 / 懒加载 ─────────────────────────────────────────────────────────────
const query    = ref('')
const year     = ref(null)
const papers   = ref([])
const loading  = ref(false)
const total    = ref(0)
const page     = ref(1)
const hasMore  = ref(true)
const sentinel = ref(null)

// ── 文件夹状态 ────────────────────────────────────────────────────────────────
const folders     = ref([])
const folderStats = ref({ total_papers: 0, unclassified_count: 0 })
const currentFolderKey = ref('__all__')
const treeRef = ref(null)

// ── 批量管理 ──────────────────────────────────────────────────────────────────
const batchMode = ref(false)
let selectedIds = reactive(new Set())

// ── 文件夹对话框 ──────────────────────────────────────────────────────────────
const folderDialog = reactive({
  visible: false, name: '', isRename: false,
  targetParentId: null, renameId: null,
})
const deleteDialog = reactive({
  visible: false, folder: null, strategy: 'unclassified',
})
const moveDialog = reactive({
  visible: false, paperIds: [], targetKey: null,
})

// ── 树数据 ────────────────────────────────────────────────────────────────────
function buildFolderTree(flat) {
  const map = {}
  flat.forEach(f => {
    map[f.id] = { key: f.id, id: f.id, name: f.name, parent_id: f.parent_id,
                  count: f.paper_count, type: 'folder', depth: 1, children: [] }
  })
  const roots = []
  flat.forEach(f => {
    if (f.parent_id && map[f.parent_id]) {
      map[f.id].depth = map[f.parent_id].depth + 1
      map[f.parent_id].children.push(map[f.id])
    } else if (!f.parent_id) roots.push(map[f.id])
  })
  // 清理空 children 让 el-tree 不显示展开箭头
  function prune(nodes) {
    nodes.forEach(n => { if (!n.children.length) delete n.children; else prune(n.children) })
  }
  prune(roots)
  return roots
}

const treeData = computed(() => [
  { key: '__all__',          name: '全部论文', count: folderStats.value.total_papers,      type: 'all' },
  { key: '__unclassified__', name: '未分类',   count: folderStats.value.unclassified_count, type: 'unclassified' },
  ...buildFolderTree(folders.value),
])

const moveTreeData = computed(() => [
  { key: '__unclassified__', name: '未分类', count: folderStats.value.unclassified_count, type: 'unclassified' },
  ...buildFolderTree(folders.value),
])

// el-tree 的 highlight-current 需要在数据变化后手动刷新当前 key
watch(treeData, async () => {
  await nextTick()
  treeRef.value?.setCurrentKey(currentFolderKey.value)
})

// ── 文件夹加载 ────────────────────────────────────────────────────────────────
async function loadFolders() {
  try {
    const res = await api.get('/folders')
    folders.value = res.data.folders
    folderStats.value = {
      total_papers:       res.data.total_papers,
      unclassified_count: res.data.unclassified_count,
    }
  } catch { /* ignore */ }
}

// ── 选中文件夹 ────────────────────────────────────────────────────────────────
function onNodeClick(data) {
  currentFolderKey.value = data.key
  resetAndSearch()
}

function selectFolder(key) {
  currentFolderKey.value = key
  treeRef.value?.setCurrentKey(key)
  resetAndSearch()
}

// ── 文件夹 CRUD ───────────────────────────────────────────────────────────────
function openCreateFolder(parentId) {
  folderDialog.name = ''
  folderDialog.isRename = false
  folderDialog.targetParentId = parentId
  folderDialog.visible = true
}

function openRename(data) {
  folderDialog.name = data.name
  folderDialog.isRename = true
  folderDialog.renameId = data.id
  folderDialog.visible = true
}

async function submitFolder() {
  const name = folderDialog.name.trim()
  if (!name) return
  try {
    if (folderDialog.isRename) {
      await api.put(`/folders/${folderDialog.renameId}`, { name })
    } else {
      await api.post('/folders', { name, parent_id: folderDialog.targetParentId })
    }
    folderDialog.visible = false
    await loadFolders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

function confirmDeleteFolder(data) {
  deleteDialog.folder   = data
  deleteDialog.strategy = 'unclassified'
  deleteDialog.visible  = true
}

async function doDeleteFolder() {
  try {
    await api.delete(`/folders/${deleteDialog.folder.id}?strategy=${deleteDialog.strategy}`)
    deleteDialog.visible = false
    if (currentFolderKey.value === deleteDialog.folder.id) selectFolder('__all__')
    await loadFolders()
    resetAndSearch()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

// ── 批量管理 ──────────────────────────────────────────────────────────────────
function toggleBatchMode() {
  batchMode.value = !batchMode.value
  selectedIds.clear()
}

function toggleSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id)
  else selectedIds.add(id)
}

function toggleSelectAll(checked) {
  selectedIds.clear()
  if (checked) papers.value.forEach(p => selectedIds.add(p.id))
}

// ── 移动论文 ──────────────────────────────────────────────────────────────────
function openMoveDialog(singleId) {
  moveDialog.paperIds = singleId ? [singleId] : [...selectedIds]
  moveDialog.targetKey = null
  moveDialog.visible = true
}

async function doMove() {
  const key = moveDialog.targetKey
  const folderId = key === '__unclassified__' ? null : key
  try {
    await api.put('/folders/papers/move', { paper_ids: moveDialog.paperIds, folder_id: folderId })
    moveDialog.visible = false
    selectedIds = reactive(new Set())
    batchMode.value = false
    await loadFolders()
    resetAndSearch()
    ElMessage.success('已移动')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '移动失败')
  }
}

// ── 论文搜索 / 懒加载 ─────────────────────────────────────────────────────────
function buildSearchParams() {
  const params = { q: query.value, year: year.value || undefined, page: page.value, page_size: PAGE_SIZE }
  if (currentFolderKey.value === '__unclassified__') params.unclassified = true
  else if (currentFolderKey.value !== '__all__') params.folder_id = currentFolderKey.value
  return params
}

function sentinelVisible() {
  if (!sentinel.value) return false
  return sentinel.value.getBoundingClientRect().top <= window.innerHeight + 300
}

async function loadPage() {
  if (loading.value || !hasMore.value) return
  loading.value = true
  try {
    const res = await api.get('/papers/search', { params: buildSearchParams() })
    papers.value.push(...res.data.items)
    total.value = res.data.total
    hasMore.value = papers.value.length < total.value
    page.value++
  } finally {
    loading.value = false
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
onMounted(async () => {
  await loadFolders()
  observer = new IntersectionObserver(
    entries => { if (entries[0].isIntersecting) loadPage() },
    { rootMargin: '300px' }
  )
  if (sentinel.value) observer.observe(sentinel.value)
  loadPage()
})
onUnmounted(() => observer?.disconnect())
</script>

<style scoped>
.page-layout  { min-height: 100vh; background: #f5f7fa; display: flex; flex-direction: column; }

.library-body {
  flex: 1;
  display: flex;
  max-width: 1260px;
  width: 100%;
  margin: 0 auto;
  padding: 16px 24px 24px;
  gap: 0;
  align-items: flex-start;
}

/* ── 左侧文件夹面板 ──────────────────────────────────────────────── */
.folder-panel {
  width: 220px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 88px);
  display: flex;
  flex-direction: column;
}

.folder-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}
.folder-panel-title { font-size: 0.88rem; font-weight: 600; color: #303133; }

/* el-tree 覆盖样式 */
.folder-tree {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  font-size: 0.85rem;
}
:deep(.el-tree-node__content) {
  height: 32px;
  padding-right: 4px;
}
:deep(.el-tree-node__content:hover) { background: #f5f7fa; }
:deep(.el-tree-node.is-current > .el-tree-node__content) { background: #ecf5ff; }

.tree-node-row {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 4px;
  overflow: hidden;
}
.tree-node-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #303133;
}
:deep(.el-tree-node.is-current) .tree-node-label { color: #409eff; font-weight: 600; }
.tree-node-count {
  font-size: 0.72rem;
  color: #c0c4cc;
  background: #f5f7fa;
  border-radius: 8px;
  padding: 0 5px;
  flex-shrink: 0;
}
:deep(.el-tree-node.is-current) .tree-node-count { background: #d9ecff; color: #409eff; }

/* 操作按钮：默认隐藏，hover 节点时显示 */
.tree-node-actions {
  display: none;
  gap: 2px;
  flex-shrink: 0;
}
:deep(.el-tree-node__content:hover) .tree-node-actions { display: flex; }

.tna-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 3px;
  font-size: 11px;
  cursor: pointer;
  color: #909399;
}
.tna-btn:hover { background: #e4e7ed; color: #303133; }
.tna-btn.danger:hover { background: #fef0f0; color: #f56c6c; }

/* ── 右侧论文区 ──────────────────────────────────────────────────── */
.paper-main { flex: 1; min-width: 0; padding-left: 20px; }

.search-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #fef9ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  font-size: 0.85rem;
}

.masonry { columns: 3; column-gap: 16px; }
.masonry > * { break-inside: avoid; margin-bottom: 16px; }

/* ── 移动树 ───────────────────────────────────────────────────────── */
.move-tree {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  max-height: 320px;
  overflow-y: auto;
}
</style>
