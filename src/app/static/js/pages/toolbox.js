/* ============================ 组件：岗位工具箱 ============================ */
const ToolboxPage = {
  setup() {
    const positions = ref([]), loading = ref(true), legend = ref({});
    const STATUS = {
      integrated: { t: '已内置', c: '#2e7d32' },
      bridge: { t: '可互通', c: '#1565c0' },
      planned: { t: '规划中', c: '#b26a00' },
    };
    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/toolbox');
        positions.value = r.positions || [];
        legend.value = r.legend || {};
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    onMounted(load);
    return { positions, loading, legend, STATUS, load, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">每个岗位在生产过程中常用的外部软件/系统，以及本系统的对应关系</span>
      <span class="grow"></span>
      <button class="btn" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="card"><div class="card-b"><div class="muted">加载中…</div></div></div>

    <div v-else class="tb-grid">
      <div v-for="p in positions" :key="p.role" class="tb-card">
        <div class="tb-h">
          <div class="tb-role">{{ p.role }}</div>
          <div class="tb-scope">{{ p.scope }}</div>
        </div>
        <div class="tb-tools">
          <div v-for="(t,i) in p.tools" :key="i" class="tb-tool">
            <div class="tb-tool-main">
              <span class="tb-name">{{ t.name }}</span>
              <span class="tb-cat">{{ t.cat }}</span>
            </div>
            <span class="tb-badge" :style="{background: (STATUS[t.status]||{}).c}">
              {{ (STATUS[t.status]||{t:t.status}).t }}
            </span>
            <div class="tb-note">{{ t.note }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-b muted small">
        集成状态：<b style="color:#2e7d32">已内置</b> = 本系统已实现；
        <b style="color:#1565c0">可互通</b> = 通过导入/导出或对接与本系统交换数据；
        <b style="color:#b26a00">规划中</b> = 已列入路线图，待对接实现。
      </div>
    </div>
  </div>
  `,
};
