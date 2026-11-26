/* ============================ 组件：组织树（M001） ============================
   集团多组织主干：集团 / 法人公司 / 工厂 / 事业部 / 部门 / 车间 / 班组。
   左侧树 + 右侧详情，管理员可增删改与调整归属。
   类型中文一律从后端 /types 取，前端不硬编码。
============================================================================ */
const StructurePage = {
  setup() {
    const types = ref([]);
    const TYPE = reactive({});                 // value -> 中文名
    const tree = ref([]);
    const allUnits = ref([]);                  // 扁平全量（上级下拉用）
    const users = ref([]);
    const stats = ref(null);
    const loading = ref(true);
    const filter = reactive({ org_type: '', keyword: '', show_inactive: false });
    const expanded = reactive({});             // id -> 是否展开
    const selectedId = ref(null);
    const detail = ref(null);
    const editing = ref(null);
    const busy = ref(false);
    const canEdit = ref(false);

    /* 权限：页面级按钮显隐 —— 无编辑权限时只读，按钮不出现而不是点了报错 */
    async function loadMe() {
      try {
        const me = await api.get('/api/org/me');
        const ps = me.permissions || [];
        canEdit.value = ps.includes('*') || ps.includes('structure.edit') ||
          ps.includes('structure');
      } catch (e) { canEdit.value = false; }
    }

    async function load() {
      loading.value = true;
      try {
        const [t, d, u, s] = await Promise.all([
          api.get('/api/structure/types'),
          api.get('/api/structure/units?active=-1'),
          api.get('/api/org/users').catch(() => []),
          api.get('/api/structure/stats').catch(() => null),
        ]);
        types.value = t;
        t.forEach(x => { TYPE[x.value] = x.label; });
        allUnits.value = d;
        users.value = u;
        stats.value = s;
        await loadTree();
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function loadTree() {
      const p = filter.show_inactive ? 'active=-1' : 'active=1';
      const r = await api.get('/api/structure/tree?' + p);
      tree.value = r.tree || [];
      // 默认展开前两层：集团 → 公司/事业部，进来就能看到骨架
      tree.value.forEach(n => { if (expanded[n.id] === undefined) expanded[n.id] = true; });
    }
    async function reload() { await load(); }

    /* ---- 树 → 扁平行（缩进渲染，避免递归组件） ---- */
    const flat = computed(() => {
      const out = [];
      const kw = (filter.keyword || '').trim();
      const walk = (nodes, depth) => {
        nodes.forEach(n => {
          const hitKw = !kw || n.name.includes(kw) || n.code.includes(kw) ||
            (n.cost_center || '').includes(kw);
          const hitType = !filter.org_type || n.org_type === filter.org_type;
          // 命中筛选的节点：即便父级不匹配也要显示（否则筛「车间」什么也看不到）
          if (hitKw && hitType) out.push({ ...n, _depth: depth });
          if (n.children && n.children.length && expanded[n.id]) walk(n.children, depth + 1);
        });
      };
      walk(tree.value, 0);
      return out;
    });

    function toggle(id) { expanded[id] = !expanded[id]; }
    function expandAll() {
      const walk = ns => ns.forEach(n => { expanded[n.id] = true; walk(n.children || []); });
      walk(tree.value);
    }
    function collapseAll() {
      Object.keys(expanded).forEach(k => { expanded[k] = false; });
    }

    /* ---- 详情 ---- */
    async function select(row) {
      selectedId.value = row.id;
      try { detail.value = await api.get('/api/structure/units/' + row.id); }
      catch (e) { notify(e.message, 'err'); }
    }
    const nameOf = id => (allUnits.value.find(u => u.id === id) || {}).name || '—';
    const userName = id => (users.value.find(u => u.id === id) || {}).full_name ||
      (users.value.find(u => u.id === id) || {}).username || '—';

    /* ---- 编辑 ---- */
    function blank() {
      return { code: '', name: '', name_en: '', org_type: 'dept', parent_id: null,
        leader_id: null, cost_center: '', address: '', phone: '', tax_no: '',
        sort_order: 0, remark: '', is_active: 1 };
    }
    function addRoot() { editing.value = blank(); }
    function addChild(row) { editing.value = { ...blank(), parent_id: row.id }; }
    async function edit(row) {
      try { editing.value = await api.get('/api/structure/units/' + row.id); }
      catch (e) { notify(e.message, 'err'); }
    }
    /* 上级下拉必须排除自己及其后代 —— 否则成环，所有递归查询都会死循环 */
    function descendantsOf(id) {
      const ids = [];
      const walk = ns => ns.forEach(n => {
        if (n.id === id) { const c = []; const g = x => (x.children || []).forEach(y => { c.push(y.id); g(y); }); g(n); ids.push(...c); return; }
        walk(n.children || []);
      });
      walk(tree.value);
      return ids;
    }
    const parentOptions = computed(() => {
      if (!editing.value) return [];
      const banned = new Set(descendantsOf(editing.value.id).concat(editing.value.id ? [editing.value.id] : []));
      return allUnits.value.filter(u => !banned.has(u.id));
    });
    async function save() {
      const e = editing.value;
      if (!e.code || !e.name) { notify('编码与名称必填', 'err'); return; }
      busy.value = true;
      try {
        if (e.id) { await api.put('/api/structure/units/' + e.id, e); notify('已保存', 'ok'); }
        else { await api.post('/api/structure/units', e); notify('组织已创建', 'ok'); }
        editing.value = null;
        await load();
        if (selectedId.value) {
          try { detail.value = await api.get('/api/structure/units/' + selectedId.value); } catch (err) {}
        }
      } catch (err) { notify(err.message, 'err'); }
      finally { busy.value = false; }
    }
    async function remove(row) {
      if (!confirm(`停用组织「${row.name}」？历史数据保留，仅不再参与新业务。`)) return;
      try { await api.del('/api/structure/units/' + row.id); notify('已停用', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }

    onMounted(async () => { await loadMe(); await load(); });
    return {
      types, TYPE, tree, allUnits, users, stats, loading, filter, expanded,
      selectedId, detail, editing, busy, canEdit, flat,
      load, reload, loadTree, toggle, expandAll, collapseAll, select,
      nameOf, userName, addRoot, addChild, edit, save, remove, parentOptions, notify
    };
  },
  template: `
  <div>
    <div v-if="stats" class="stats">
      <div class="stat"><div class="lb">法人公司</div><div class="vl">{{ (stats.by_type.find(t=>t.value==='company')||{}).count || 0 }}</div><div class="sf">独立账套主体</div></div>
      <div class="stat b"><div class="lb">工厂 / 车间</div><div class="vl">{{ ((stats.by_type.find(t=>t.value==='factory')||{}).count||0) + ((stats.by_type.find(t=>t.value==='workshop')||{}).count||0) }}</div><div class="sf">生产基地</div></div>
      <div class="stat k"><div class="lb">事业部</div><div class="vl">{{ (stats.by_type.find(t=>t.value==='bu')||{}).count || 0 }}</div><div class="sf">经营口径</div></div>
      <div class="stat g"><div class="lb">在岗人数</div><div class="vl">{{ stats.headcount }}</div><div class="sf">未分配组织 {{ stats.unassigned }} 人</div></div>
    </div>

    <div class="toolbar">
      <select class="inp" style="max-width:150px" v-model="filter.org_type">
        <option value="">全部类型</option>
        <option v-for="t in types" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <input class="inp" style="max-width:190px" v-model="filter.keyword" placeholder="搜索名称 / 编码 / 成本中心">
      <label class="small" style="display:flex;align-items:center;gap:5px;color:#5A6070">
        <input type="checkbox" v-model="filter.show_inactive" @change="loadTree"> 含已停用
      </label>
      <button class="btn sm hide-mobile" @click="expandAll">展开全部</button>
      <button class="btn sm hide-mobile" @click="collapseAll">折叠全部</button>
      <span class="grow"></span>
      <button class="btn sm" @click="reload">刷新</button>
      <button class="btn primary sm" v-if="canEdit" @click="addRoot">＋ 新增组织</button>
    </div>

    <div class="split-org">
      <div class="card">
        <div class="card-h">
          <div><h3>组织架构</h3><div class="d">集团 → 法人公司 → 工厂 / 事业部 → 部门 → 车间 → 班组</div></div>
        </div>
        <div class="card-b tight">
          <div class="tbl-wrap">
            <table class="tbl responsive">
              <thead>
                <tr>
                  <th>组织名称</th><th style="width:88px">类型</th>
                  <th style="width:110px">负责人</th><th style="width:96px">成本中心</th>
                  <th class="c" style="width:74px">在岗</th><th class="c" style="width:74px">含下级</th>
                  <th class="c" style="width:150px">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!flat.length"><td colspan="7" class="muted c">没有匹配的记录</td></tr>
                <tr v-for="r in flat" :key="r.id" @click="select(r)"
                    :style="{background: selectedId===r.id ? '#FFF8E8':'', cursor:'pointer'}">
                  <td data-label="组织名称">
                    <span :style="{paddingLeft:(r._depth*16)+'px', display:'inline-block'}">
                      <span v-if="r.child_count" @click.stop="toggle(r.id)"
                            style="cursor:pointer;color:#8B90A0;display:inline-block;width:14px">
                        {{ expanded[r.id] ? '▾' : '▸' }}
                      </span>
                      <span v-else style="display:inline-block;width:14px"></span>
                      <b>{{ r.name }}</b>
                      <span class="mono muted" style="margin-left:6px">{{ r.code }}</span>
                      <span v-if="!r.is_active" class="tag err" style="margin-left:5px">停用</span>
                    </span>
                  </td>
                  <td data-label="类型"><span class="tag blue">{{ r.org_type_label }}</span></td>
                  <td data-label="负责人">{{ r.leader_name || '—' }}</td>
                  <td data-label="成本中心" class="mono">{{ r.cost_center || '—' }}</td>
                  <td data-label="在岗" class="c">{{ r.member_count }}</td>
                  <td data-label="含下级" class="c">{{ r.total_count }}</td>
                  <td data-label="操作" class="c">
                    <template v-if="canEdit">
                      <button class="btn sm" v-if="r.can_have_child" @click.stop="addChild(r)">+子级</button>
                      <button class="btn sm" @click.stop="edit(r)">编辑</button>
                      <button class="btn sm danger" v-if="r.is_active" @click.stop="remove(r)">停用</button>
                    </template>
                    <span v-else class="muted small">只读</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-h"><div><h3>组织详情</h3>
          <div class="d">{{ detail ? '归属三轴与编制信息' : '点击左侧组织查看详情' }}</div></div></div>
        <div class="card-b">
          <div v-if="!detail" class="muted small">未选择组织。</div>
          <template v-else>
            <div style="font-size:15px;font-weight:700">{{ detail.name }}</div>
            <div class="small muted" style="margin:3px 0 10px">
              <span v-for="(c,i) in detail.chain" :key="c.id">
                <span v-if="i"> / </span>{{ c.name }}
              </span>
            </div>
            <table style="width:100%;font-size:12.5px;border-collapse:collapse">
              <tr><td class="muted" style="width:88px;padding:5px 0">编码</td><td class="mono">{{ detail.code }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">类型</td><td>{{ detail.org_type_label }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">上级</td><td>{{ detail.parent_name || '（顶层）' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">负责人</td><td>{{ detail.leader_name || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">成本中心</td><td class="mono">{{ detail.cost_center || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">所属公司</td><td>{{ nameOf(detail.company_id) }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">所属工厂</td><td>{{ nameOf(detail.factory_id) }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">所属事业部</td><td>{{ nameOf(detail.bu_id) }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">在岗人数</td><td>{{ detail.member_count }} 人<span class="muted"> （下级组织 {{ (detail.subtree_ids||[]).length }} 个）</span></td></tr>
              <tr><td class="muted" style="padding:5px 0">地址</td><td>{{ detail.address || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">电话</td><td>{{ detail.phone || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">税号</td><td class="mono">{{ detail.tax_no || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">备注</td><td>{{ detail.remark || '—' }}</td></tr>
            </table>
          </template>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-b muted small">
        <b>为什么分三轴：</b>一个「装配车间」同时属于 一厂（生产轴）、国内事业部（经营轴）、
        广州菲力克斯机械（法人轴）。组织树只管层级，三条归属线（公司 / 工厂 / 事业部）由系统
        自顶向下自动继承，未来「一厂厂长只看一厂数据」「国际事业部看不到国内客户底价」
        就靠这三个字段过滤 —— 不用在业务代码里写特判。
      </div>
    </div>

    <div class="mask" v-if="editing" @click.self="editing=null">
      <div class="modal" style="max-width:640px">
        <div class="modal-h">
          <h3>{{ editing.id ? '编辑组织' : '新增组织' }}</h3>
          <span class="x-btn" @click="editing=null">×</span>
        </div>
        <div class="modal-b">
          <div class="grid2">
            <div class="field"><label>编码 <span class="req">*</span></label>
              <input class="inp" v-model="editing.code" placeholder="如 FA03 / DPQA"></div>
            <div class="field"><label>名称 <span class="req">*</span></label>
              <input class="inp" v-model="editing.name" placeholder="如 三厂（清远生产基地）"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>组织类型</label>
              <select class="inp" v-model="editing.org_type">
                <option v-for="t in types" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select></div>
            <div class="field"><label>上级组织</label>
              <select class="inp" v-model="editing.parent_id">
                <option :value="null">（顶层组织）</option>
                <option v-for="u in parentOptions" :key="u.id" :value="u.id">{{ u.name }}</option>
              </select></div>
          </div>
          <div class="grid2">
            <div class="field"><label>负责人</label>
              <select class="inp" v-model="editing.leader_id">
                <option :value="null">（未指定）</option>
                <option v-for="u in users" :key="u.id" :value="u.id">{{ u.full_name || u.username }}</option>
              </select></div>
            <div class="field"><label>成本中心</label>
              <input class="inp" v-model="editing.cost_center" placeholder="CC-140"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>地址</label>
              <input class="inp" v-model="editing.address"></div>
            <div class="field"><label>电话</label>
              <input class="inp" v-model="editing.phone"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>税号（仅法人公司）</label>
              <input class="inp" v-model="editing.tax_no"></div>
            <div class="field"><label>排序</label>
              <input class="inp" type="number" v-model="editing.sort_order"></div>
          </div>
          <div class="field"><label>备注</label>
            <input class="inp" v-model="editing.remark"></div>
          <div class="field"><label>状态</label>
            <select class="inp" v-model="editing.is_active">
              <option :value="1">启用</option><option :value="0">停用</option>
            </select></div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="editing=null">取消</button>
          <button class="btn primary" :disabled="busy" @click="save">{{ busy?'保存中…':'保存' }}</button>
        </div>
      </div>
    </div>
  </div>
  `,
};
