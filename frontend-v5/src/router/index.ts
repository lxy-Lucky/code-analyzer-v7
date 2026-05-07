import { createRouter, createWebHashHistory } from 'vue-router'
import RepoManager    from '@/pages/RepoManager.vue'
import CodeSearch     from '@/pages/CodeSearch.vue'
import ChangeAnalysis from '@/pages/ChangeAnalysis.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',         redirect: '/repo' },
    { path: '/repo',     component: RepoManager },
    { path: '/search',   component: CodeSearch },
    { path: '/analysis', component: ChangeAnalysis },
  ],
})
