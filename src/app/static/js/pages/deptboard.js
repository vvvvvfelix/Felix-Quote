/* ============================ 组件：部门目录看板 ============================ */
const DeptBoardPage = {
  emits: ['nav'],
  setup(props, { emit }) {
    const groups = ref([]), loading = ref(true), expanded = ref({});

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/org/org-tree');
        groups.value = r.groups || [];
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    /* 点击部门卡片 → 展开/收起该部门的全部模块 */
    function toggle(code) {
      expanded.value[code] = !expanded.value[code];
    }
    function isOpen(code) { return !!expanded.value[code]; }
    function go(key) { emit('nav', key); }

    onMounted(load);
    return { groups, loading, expanded, load, toggle, isOpen, go, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">按传统组织架构分组 · 点击部门卡片查看该部门全部功能</span>
      <span class="grow"></span>
      <button class="btn" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="card"><div class="card-b"><div class="muted">加载中…</div></div></div>

    <div v-else class="dept-grid">
      <div v-for="g in groups" :key="g.code" class="dept-card">
        <div class="dc-h" @click="toggle(g.code)">
          <div class="dc-t">
            <div class="dc-n">{{ g.name }}</div>
            <div class="dc-m">
              <span class="muted small">{{ g.headcount }} 人</span>
              <span class="muted small" v-if="g.leader_name">· 负责人 {{ g.leader_name }}</span>
            </div>
          </div>
          <span class="dc-cnt">{{ g.modules.length }} 个功能</span>
          <i class="ic dc-arrow">{{ isOpen(g.code) ? '▾' : '▸' }}</i>
        </div>

        <div v-if="g.children.length" class="dc-subs">
          <span class="tag gray" v-for="c in g.children" :key="c.id">{{ c.name }} {{ c.headcount }}</span>
        </div>

        <!-- 折叠时只显示前 3 个常用模块，点击展开看全部 -->
        <div class="dc-mods">
          <div v-for="(m, i) in g.modules" :key="m.key" class="dc-mod"
               v-show="isOpen(g.code) || i < 3" @click="go(m.key)">
            <div class="dm-n">{{ m.name }}</div>
            <div class="dm-d">{{ m.desc }}</div>
          </div>
          <div v-if="!isOpen(g.code) && g.modules.length > 3" class="dc-more"
               @click="toggle(g.code)">还有 {{ g.modules.length - 3 }} 个功能，点击展开全部 ▾</div>
        </div>
      </div>
    </div>
  </div>`
};
