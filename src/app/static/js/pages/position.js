/* ============================ 组件：岗位职级（M002） ============================
   三轴模型的第二轴：组织 × 岗位 × 职级 × 编制 × 汇报上级。
   三个签页：编制（含 编制 vs 在岗看板）/ 岗位 / 职级。
   所有中文枚举一律从后端 /api/position/types 取，前端不硬编码。
============================================================================ */
const PositionPage = {
  setup() {
    const T = reactive({});                    // 枚举 value -> 中文名
    const types = ref(null);
    const tab = ref('pos');                    // pos / title / grade
    const loading = ref(true);
    const busy = ref(false);
    const canEdit = ref(false);

    const stats = ref(null);
    const orgs = ref([]);                      // 组织下拉
    const titles = ref([]);
    const grades = ref([]);
    const rows = ref([]);                      // 当前签页的表格数据

    const filter = reactive({
      org_unit_id: '', job_title_id: '', job_family: '', status: '',
      gap_status: '', keyword: '', show_inactive: false, subtree: false,
    });
    const board = ref(null);
    const boardDim = ref('org');
    const boardScope = ref('self');

    const selectedId = ref(null);
    const detail = ref(null);
    const editing = ref(null);                 // 编制/岗位/职级 编辑对象
    const editKind = ref('pos');
    const assigning = ref(null);               // 任职弹窗
    const candidates = ref([]);

    /* 权限：无编辑权限时按钮不出现，而不是点了才报错 */
    async function loadMe() {
      try {
        const me = await api.get('/api/org/me');
        const ps = me.permissions || [];
        canEdit.value = ps.includes('*') || ps.includes('position.edit') ||
          ps.includes('position');
      } catch (e) { canEdit.value = false; }
    }

    async function loadRefs() {
      const [t, o, ti, g] = await Promise.all([
        api.get('/api/position/types'),
        api.get('/api/position/org-options?active=-1'),
        api.get('/api/position/titles?active=-1'),
        api.get('/api/position/grades?active=-1'),
      ]);
      types.value = t;
      ['families', 'categories', 'statuses', 'assign_modes', 'gap_statuses', 'board_dims']
        .forEach(k => { (t[k] || []).forEach(x => { T[x.value] = x.label; }); });
      orgs.value = o; titles.value = ti; grades.value = g;
    }

    function query() {
      const p = new URLSearchParams();
      p.set('active', filter.show_inactive ? '-1' : '1');
      if (filter.org_unit_id) { p.set('org_unit_id', filter.org_unit_id); p.set('subtree', filter.subtree ? '1' : '0'); }
      if (filter.job_title_id) p.set('job_title_id', filter.job_title_id);
      if (filter.job_family) p.set('job_family', filter.job_family);
      if (filter.status) p.set('status', filter.status);
      if (filter.keyword) p.set('keyword', filter.keyword);
      return p.toString();
    }

    async function loadRows() {
      if (tab.value === 'pos') {
        rows.value = await api.get('/api/position/positions?' + query());
        if (filter.gap_status) rows.value = rows.value.filter(r => r.gap_status === filter.gap_status);
      } else if (tab.value === 'title') {
        const p = new URLSearchParams();
        p.set('active', filter.show_inactive ? '-1' : '1');
        if (filter.job_family) p.set('family', filter.job_family);
        if (filter.keyword) p.set('keyword', filter.keyword);
        rows.value = await api.get('/api/position/titles?' + p.toString());
      } else {
        const p = new URLSearchParams();
        p.set('active', filter.show_inactive ? '-1' : '1');
        if (filter.job_family) p.set('family', filter.job_family);
        rows.value = await api.get('/api/position/grades?' + p.toString());
      }
    }

    async function loadBoard() {
      const p = new URLSearchParams();
      p.set('dim', boardDim.value);
      p.set('scope', boardScope.value);
      if (filter.org_unit_id) p.set('org_unit_id', filter.org_unit_id);
      if (filter.job_family) p.set('job_family', filter.job_family);
      if (filter.status) p.set('status', filter.status);
      board.value = await api.get('/api/position/board?' + p.toString());
    }

    async function load() {
      loading.value = true;
      try {
        await loadRefs();
        stats.value = await api.get('/api/position/stats').catch(() => null);
        await loadRows();
        await loadBoard();
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function reload() { await loadRows(); await loadBoard(); }

    const orgName = id => (orgs.value.find(o => o.id === id) || {}).name || '—';

    /* ---- 详情 ---- */
    async function select(row) {
      if (tab.value !== 'pos') return;
      selectedId.value = row.id;
      try { detail.value = await api.get('/api/position/positions/' + row.id); }
      catch (e) { notify(e.message, 'err'); }
    }

    /* ---- 编辑 ---- */
    function blankPos() {
      return { code: '', name: '', org_unit_id: filter.org_unit_id || null,
        job_title_id: null, job_grade_id: null, headcount: 1, parent_position_id: null,
        status: 'active', is_key: 0, work_place: '', duty: '', sort_order: 0,
        remark: '', is_active: 1, requires: [] };
    }
    function blankTitle() {
      return { code: '', name: '', name_en: '', family: 'P', category: 'admin',
        level_hint: 0, duty: '', sort_order: 0, remark: '', is_active: 1, requires: [] };
    }
    function blankGrade() {
      return { code: '', family: 'P', level: 1, name: '', name_en: '',
        salary_min: null, salary_max: null, currency: 'CNY', duty: '',
        sort_order: 0, remark: '', is_active: 1 };
    }
    function addNew() {
      editKind.value = tab.value;
      editing.value = tab.value === 'pos' ? blankPos()
        : (tab.value === 'title' ? blankTitle() : blankGrade());
    }
    function editRow(r) {
      editKind.value = tab.value;
      editing.value = JSON.parse(JSON.stringify(r));
    }
    function base(kind) {
      return kind === 'pos' ? '/api/position/positions'
        : (kind === 'title' ? '/api/position/titles' : '/api/position/grades');
    }
    async function save() {
      const e = editing.value, k = editKind.value;
      if (k === 'pos' && !e.org_unit_id) { notify('必须选择所属组织', 'err'); return; }
      if (!e.code && k !== 'pos') { notify('编码必填', 'err'); return; }
      if (!e.name) { notify('名称必填', 'err'); return; }
      busy.value = true;
      try {
        if (e.id) { await api.put(base(k) + '/' + e.id, e); notify('已保存', 'ok'); }
        else { await api.post(base(k), e); notify('已创建', 'ok'); }
        editing.value = null;
        await load();
        if (selectedId.value && k === 'pos') {
          try { detail.value = await api.get('/api/position/positions/' + selectedId.value); } catch (err) {}
        }
      } catch (err) { notify(err.message, 'err'); }
      finally { busy.value = false; }
    }
    async function remove(r) {
      const nm = r.name || r.code;
      if (!confirm(`停用「${nm}」？历史数据保留，仅不再参与新业务。`)) return;
      try { await api.del(base(tab.value) + '/' + r.id); notify('已停用', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }

    /* ---- 任职 ---- */
    async function openAssign(pos) {
      try {
        candidates.value = await api.get('/api/position/candidates');
        assigning.value = { position_id: pos.id, position_name: pos.name,
          user_id: null, is_primary: 1, ratio: 1.0, start_date: '', remark: '' };
      } catch (e) { notify(e.message, 'err'); }
    }
    async function saveAssign() {
      const a = assigning.value;
      if (!a.user_id) { notify('请选择人员', 'err'); return; }
      busy.value = true;
      try {
        await api.post('/api/position/assignments', a);
        notify('任职已登记', 'ok');
        assigning.value = null;
        await load();
        if (selectedId.value) detail.value = await api.get('/api/position/positions/' + selectedId.value);
      } catch (e) { notify(e.message, 'err'); }
      finally { busy.value = false; }
    }
    async function endAssign(row) {
      if (!confirm(`结束「${row.full_name || row.username}」在该编制上的任职？将保留历史记录。`)) return;
      try {
        await api.del('/api/position/assignments/' + row.id);
        notify('任职已结束', 'ok');
        await load();
        if (selectedId.value) detail.value = await api.get('/api/position/positions/' + selectedId.value);
      } catch (e) { notify(e.message, 'err'); }
    }

    const gapClass = s => (s === 'over' ? 'err' : (s === 'full' ? 'ok' : 'warn'));
    const titleName = id => (titles.value.find(t => t.id === id) || {}).name || '—';
    const gradeCode = id => (grades.value.find(g => g.id === id) || {}).code || '—';
    /* 汇报上级下拉必须排除自己 —— 同元素上 v-if 先于 v-for 求值，取不到 r，
       故用 computed 预先过滤，避免「把自己设成自己的上级」成环 */
    const parentOptions = computed(() => {
      const id = editing.value ? editing.value.id : null;
      return rows.value.filter(r => !id || r.id !== id);
    });

    onMounted(async () => { await loadMe(); await load(); });
    return {
      T, types, tab, loading, busy, canEdit, stats, orgs, titles, grades, rows,
      filter, board, boardDim, boardScope, selectedId, detail, editing, editKind,
      assigning, candidates, orgName, titleName, gradeCode, gapClass, parentOptions,
      load, reload, loadBoard, loadRows, select, addNew, editRow, save, remove,
      openAssign, saveAssign, endAssign, notify
    };
  },
  template: `
  <div>
    <div v-if="stats" class="stats">
      <!-- 统计卡中的 缺编 / 满编 / 超编 是前端按 gap = 总编制 − 在岗 计算得到的派生展示标签，
           并非后端枚举（后端只下发 gap_status: short/full/over 及其 label），此处为模块内部派生文案，
           维护时请勿误当作对外枚举去改 types 接口。 -->
      <div class="stat"><div class="lb">总编制</div><div class="vl">{{ stats.headcount }}</div>
        <div class="sf">{{ stats.positions }} 个编制岗位</div></div>
      <div class="stat b"><div class="lb">在岗任职</div><div class="vl">{{ stats.occupied }}</div>
        <div class="sf">在职 {{ stats.members }} 人 · 已落编 {{ stats.assigned }}</div></div>
      <div class="stat k"><div class="lb">缺编</div><div class="vl">{{ stats.short }}</div>
        <div class="sf">{{ stats.short_positions }} 个岗位缺人</div></div>
      <div class="stat g"><div class="lb">超编</div><div class="vl">{{ stats.over }}</div>
        <div class="sf">{{ stats.over_positions }} 个岗位超编 · 满编 {{ stats.full }} 个</div></div>
      <div class="stat"><div class="lb">未落编人员</div><div class="vl">{{ stats.unassigned }}</div>
        <div class="sf">在职但无编制归属</div></div>
    </div>

    <div class="toolbar">
      <div class="tabs" style="margin:0">
        <button :class="{on:tab==='pos'}" @click="tab='pos';reload()">编制</button>
        <button :class="{on:tab==='title'}" @click="tab='title';reload()">岗位</button>
        <button :class="{on:tab==='grade'}" @click="tab='grade';reload()">职级</button>
      </div>
      <select class="inp" style="max-width:170px" v-model="filter.org_unit_id" @change="reload">
        <option value="">全部组织</option>
        <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}</option>
      </select>
      <select class="inp" style="max-width:150px" v-model="filter.job_title_id" @change="reload" v-if="tab==='pos'">
        <option value="">全部岗位</option>
        <option v-for="t in titles" :key="t.id" :value="t.id">{{ t.name }}</option>
      </select>
      <select class="inp" style="max-width:140px" v-model="filter.job_family" @change="reload">
        <option value="">全部序列</option>
        <option v-for="f in (types ? types.families : [])" :key="f.value" :value="f.value">{{ f.label }}</option>
      </select>
      <select class="inp" style="max-width:130px" v-model="filter.status" @change="reload" v-if="tab==='pos'">
        <option value="">全部状态</option>
        <option v-for="s in (types ? types.statuses : [])" :key="s.value" :value="s.value">{{ s.label }}</option>
      </select>
      <select class="inp" style="max-width:110px" v-model="filter.gap_status" @change="loadRows" v-if="tab==='pos'">
        <option value="">全部差额</option>
        <option v-for="g in (types ? types.gap_statuses : [])" :key="g.value" :value="g.value">{{ g.label }}</option>
      </select>
      <input class="inp" style="max-width:160px" v-model="filter.keyword" @keyup.enter="reload" placeholder="搜索名称 / 编码">
      <label class="small" style="display:flex;align-items:center;gap:4px;color:#5A6070" v-if="tab==='pos'">
        <input type="checkbox" v-model="filter.subtree" @change="reload"> 含下级组织</label>
      <label class="small" style="display:flex;align-items:center;gap:4px;color:#5A6070">
        <input type="checkbox" v-model="filter.show_inactive" @change="reload"> 含停用</label>
      <span class="grow"></span>
      <button class="btn sm" @click="load">刷新</button>
      <button class="btn primary sm" v-if="canEdit" @click="addNew">＋ 新增{{ tab==='pos'?'编制':(tab==='title'?'岗位':'职级') }}</button>
    </div>

    <!-- ===== 编制 vs 在岗看板 ===== -->
    <div class="card" v-if="tab==='pos' && board">
      <div class="card-h">
        <div><h3>编制 vs 在岗看板</h3>
          <div class="d">{{ board.note }}</div></div>
        <div style="display:flex;gap:8px;align-items:center">
          <select class="inp" style="max-width:120px" v-model="boardDim" @change="loadBoard">
            <option v-for="d in (types ? types.board_dims : [])" :key="d.value" :value="d.value">{{ d.label }}</option>
          </select>
          <select class="inp" style="max-width:120px" v-model="boardScope" @change="loadBoard"
                  v-if="boardDim==='org'">
            <option value="self">仅本级</option><option value="subtree">含下级</option>
          </select>
        </div>
      </div>
      <div class="card-b tight">
        <div class="tbl-wrap">
          <table class="tbl responsive">
            <thead><tr>
              <th>{{ boardDim==='title'?'岗位':(boardDim==='family'?'职级序列':'组织 / 部门') }}</th>
              <th style="width:100px">编码</th>
              <th class="c" style="width:80px">编制数</th>
              <th class="c" style="width:80px">在岗</th>
              <th class="c" style="width:80px">差额</th>
              <th class="c" style="width:90px">状态</th>
              <th class="c" style="width:110px" v-if="boardDim==='org'||boardDim==='dept'">组织在岗</th>
            </tr></thead>
            <tbody>
              <tr v-if="!board.rows.length"><td colspan="7" class="muted c" data-label="">没有匹配的编制数据</td></tr>
              <tr v-for="b in board.rows" :key="b.key">
                <td data-label="组织 / 部门"><b>{{ b.name }}</b></td>
                <td class="mono muted" data-label="编码">{{ b.code }}</td>
                <td class="c" data-label="编制数">{{ b.headcount }}</td>
                <td class="c" data-label="在岗">{{ b.occupied }}</td>
                <td class="c" data-label="差额" :style="{color: b.gap<0?'#D3453B':(b.gap>0?'#C98A00':'#2E7D32')}">
                  {{ b.gap > 0 ? '-' + b.gap : (b.gap < 0 ? '+' + (-b.gap) : '0') }}</td>
                <td class="c" data-label="状态"><span class="tag" :class="gapClass(b.gap_status)">{{ b.gap_status_label }}</span></td>
                <td class="c muted" data-label="组织在岗" v-if="boardDim==='org'||boardDim==='dept'">{{ b.members }}</td>
              </tr>
            </tbody>
            <tfoot v-if="board.rows.length">
              <tr style="background:#FAFAFC;font-weight:700">
                <td data-label="">合计</td><td data-label=""></td>
                <td class="c" data-label="编制数">{{ board.total.headcount }}</td>
                <td class="c" data-label="在岗">{{ board.total.occupied }}</td>
                <td class="c" data-label="差额">{{ board.total.gap }}</td>
                <td data-label=""></td><td data-label="" v-if="boardDim==='org'||boardDim==='dept'"></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1.6fr 1fr;gap:14px;align-items:start;margin-top:14px">
      <div class="card">
        <div class="card-h">
          <div><h3>{{ tab==='pos'?'编制清单':(tab==='title'?'岗位库':'职级库') }}</h3>
            <div class="d">{{ tab==='pos'?'组织 × 岗位 × 职级 × 编制数 × 汇报上级':'全公司统一口径，前端不硬编码中文' }}</div></div>
        </div>
        <div class="card-b tight">
          <div class="tbl-wrap">
            <table class="tbl responsive">
              <thead v-if="tab==='pos'"><tr>
                <th>编制</th><th style="width:130px">组织</th><th style="width:110px">岗位</th>
                <th style="width:70px">职级</th><th class="c" style="width:64px">编制</th>
                <th class="c" style="width:64px">在岗</th><th class="c" style="width:80px">差额</th>
                <th class="c" style="width:150px">操作</th>
              </tr></thead>
              <thead v-else-if="tab==='title'"><tr>
                <th style="width:150px">编码</th><th>岗位名称</th><th style="width:120px">序列</th>
                <th style="width:90px">类别</th><th class="c" style="width:80px">生效编制</th>
                <th class="c" style="width:80px">在职</th><th class="c" style="width:140px">操作</th>
              </tr></thead>
              <thead v-else><tr>
                <th style="width:80px">职级</th><th>名称</th><th style="width:110px">序列</th>
                <th style="width:150px">薪资带宽</th><th class="c" style="width:80px">生效编制</th>
                <th class="c" style="width:80px">在职</th><th class="c" style="width:140px">操作</th>
              </tr></thead>
              <tbody>
                <tr v-if="!rows.length"><td colspan="8" class="muted c" data-label="">没有匹配的记录</td></tr>

                <template v-if="tab==='pos'">
                  <tr v-for="r in rows" :key="r.id" @click="select(r)"
                      :style="{background: selectedId===r.id?'#FFF8E8':'', cursor:'pointer'}">
                    <td data-label="编制"><b>{{ r.name }}</b>
                      <span class="mono muted" style="margin-left:6px">{{ r.code }}</span>
                      <span v-if="r.is_key" class="tag gold" style="margin-left:5px">关键岗</span>
                      <span v-if="!r.is_active" class="tag err" style="margin-left:5px">停用</span>
                      <div class="small muted" v-if="r.parent_position_name">汇报：{{ r.parent_position_name }}</div>
                    </td>
                    <td data-label="组织">{{ r.org_name || '—' }}</td>
                    <td data-label="岗位">{{ r.job_title_name || '—' }}</td>
                    <td class="mono" data-label="职级">{{ r.grade_code || '—' }}</td>
                    <td class="c" data-label="编制">{{ r.headcount }}</td>
                    <td class="c" data-label="在岗">{{ r.occupied }}</td>
                    <td class="c" data-label="差额"><span class="tag" :class="gapClass(r.gap_status)">{{ r.gap_status_label }}</span></td>
                    <td class="c" data-label="操作">
                      <template v-if="canEdit">
                        <button class="btn sm" @click.stop="editRow(r)">编辑</button>
                        <button class="btn sm" @click.stop="openAssign(r)">任职</button>
                        <button class="btn sm danger" v-if="r.is_active" @click.stop="remove(r)">停用</button>
                      </template>
                      <span v-else class="muted small">只读</span>
                    </td>
                  </tr>
                </template>

                <template v-else-if="tab==='title'">
                  <tr v-for="r in rows" :key="r.id">
                    <td class="mono" data-label="编码">{{ r.code }}</td>
                    <td data-label="岗位名称"><b>{{ r.name }}</b><span class="muted small" v-if="r.name_en" style="margin-left:6px">{{ r.name_en }}</span>
                      <span v-if="!r.is_active" class="tag err" style="margin-left:5px">停用</span></td>
                    <td data-label="序列"><span class="tag blue">{{ r.family_label }}</span></td>
                    <td data-label="类别">{{ r.category_label }}</td>
                    <td class="c" data-label="生效编制">{{ r.pos_count }}</td>
                    <td class="c" data-label="在职">{{ r.holder_count }}</td>
                    <td class="c" data-label="操作">
                      <template v-if="canEdit">
                        <button class="btn sm" @click="editRow(r)">编辑</button>
                        <button class="btn sm danger" v-if="r.is_active" @click="remove(r)">停用</button>
                      </template>
                      <span v-else class="muted small">只读</span>
                    </td>
                  </tr>
                </template>

                <template v-else>
                  <tr v-for="r in rows" :key="r.id">
                    <td class="mono" data-label="职级"><b>{{ r.code }}</b></td>
                    <td data-label="名称">{{ r.name }}<span class="muted small" v-if="r.name_en" style="margin-left:6px">{{ r.name_en }}</span>
                      <span v-if="!r.is_active" class="tag err" style="margin-left:5px">停用</span></td>
                    <td data-label="序列"><span class="tag blue">{{ r.family_label }}</span></td>
                    <td class="mono small" data-label="薪资带宽" v-if="r.salary_min || r.salary_max">
                      {{ r.salary_min || 0 }} – {{ r.salary_max || 0 }} {{ r.currency }}</td>
                    <td class="mono small muted" data-label="薪资带宽" v-else>—</td>
                    <td class="c" data-label="生效编制">{{ r.pos_count }}</td>
                    <td class="c" data-label="在职">{{ r.holder_count }}</td>
                    <td class="c" data-label="操作">
                      <template v-if="canEdit">
                        <button class="btn sm" @click="editRow(r)">编辑</button>
                        <button class="btn sm danger" v-if="r.is_active" @click="remove(r)">停用</button>
                      </template>
                      <span v-else class="muted small">只读</span>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- ===== 编制详情 ===== -->
      <div class="card">
        <div class="card-h"><div><h3>编制详情</h3>
          <div class="d">{{ detail ? '任职人员与汇报关系' : '点击左侧编制查看详情' }}</div></div></div>
        <div class="card-b">
          <div v-if="!detail" class="muted small">未选择编制。</div>
          <template v-else>
            <div style="font-size:15px;font-weight:700">{{ detail.name }}</div>
            <div class="small muted" style="margin:3px 0 10px">
              <span v-for="(c,i) in detail.org_chain" :key="c.id"><span v-if="i"> / </span>{{ c.name }}</span>
            </div>
            <table style="width:100%;font-size:12.5px;border-collapse:collapse">
              <tr><td class="muted" style="width:82px;padding:5px 0">编码</td><td class="mono">{{ detail.code }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">组织</td><td>{{ detail.org_name }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">岗位</td><td>{{ detail.job_title_name || '—' }}<span class="muted small"> · {{ detail.job_category_label }}</span></td></tr>
              <tr><td class="muted" style="padding:5px 0">职级</td><td>{{ detail.grade_code || '—' }}<span class="muted small"> · {{ detail.job_family_label }}</span></td></tr>
              <tr><td class="muted" style="padding:5px 0">编制 / 在岗</td>
                <td>{{ detail.headcount }} / {{ detail.occupied }}
                  <span class="tag" :class="gapClass(detail.gap_status)">{{ detail.gap_status_label }}</span></td></tr>
              <tr><td class="muted" style="padding:5px 0">状态</td><td>{{ detail.status_label }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">汇报上级</td><td>{{ detail.parent_position_name || '（顶层）' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">工作地点</td><td>{{ detail.work_place || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">岗位职责</td><td>{{ detail.duty || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">备注</td><td>{{ detail.remark || '—' }}</td></tr>
            </table>

            <div style="margin-top:12px;font-weight:700;font-size:13px">
              任职人员（{{ (detail.incumbents||[]).length }}）
              <button class="btn sm" style="float:right" v-if="canEdit" @click="openAssign(detail)">＋ 任职</button>
            </div>
            <table class="tbl responsive" style="margin-top:6px">
              <thead><tr><th>姓名</th><th style="width:80px">方式</th><th style="width:70px">工号</th><th class="c" style="width:60px">操作</th></tr></thead>
              <tbody>
                <tr v-if="!(detail.incumbents||[]).length"><td colspan="4" class="muted c" data-label="">暂无人任职</td></tr>
                <tr v-for="p in detail.incumbents" :key="p.id">
                  <td data-label="姓名">{{ p.full_name || p.username }}<div class="small muted">{{ p.org_name || '—' }}</div></td>
                  <td data-label="方式"><span class="tag" :class="p.is_primary?'blue':''">{{ p.is_primary_label }}</span></td>
                  <td class="mono small" data-label="工号">{{ p.emp_no || '—' }}</td>
                  <td class="c" data-label="操作"><button class="btn sm danger" v-if="canEdit" @click="endAssign(p)">结束</button></td>
                </tr>
              </tbody>
            </table>

            <div style="margin-top:12px;font-weight:700;font-size:13px">下级汇报岗位（{{ (detail.children||[]).length }}）</div>
            <div class="small muted" style="margin-top:4px" v-if="!(detail.children||[]).length">无</div>
            <div v-for="c in detail.children" :key="c.id" class="small"
                 style="padding:3px 0;border-bottom:1px dashed #EEE">
              {{ c.name }} <span class="muted">（{{ c.headcount }} 编 / {{ c.occupied }} 在岗）</span>
            </div>

            <div style="margin-top:12px;font-weight:700;font-size:13px">汇报链</div>
            <div class="small muted" style="margin-top:4px" v-if="!(detail.report_chain||[]).length">已是顶层编制</div>
            <div v-for="(c,i) in detail.report_chain" :key="c.id" class="small" style="padding:2px 0">
              {{ i+1 }}. {{ c.name }}<span v-if="c.holder_name"> — {{ c.holder_name }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-b muted small">
        <b>为什么编制要独立成表：</b>组织树只回答「部门在哪一层」，回答不了「这个部门该设几个岗、
        每岗几人、现在缺多少人、谁向谁汇报」。一条编制 = 组织 × 岗位 × 职级 × 编制人数 × 汇报上级；
        人通过<b>任职关系</b>挂到编制上（支持主岗 / 兼岗），于是「编制 vs 在岗」就是一个简单的差。
        兼岗者在按编制汇总时会重复计一次，看板同时给出按人去重的<b>组织在岗</b>数供交叉核对。
      </div>
    </div>

    <!-- ===== 编辑弹窗 ===== -->
    <div class="mask" v-if="editing" @click.self="editing=null">
      <div class="modal" style="max-width:660px">
        <div class="modal-h">
          <h3>{{ editing.id ? '编辑' : '新增' }}{{ editKind==='pos'?'编制':(editKind==='title'?'岗位':'职级') }}</h3>
          <span class="x-btn" @click="editing=null">×</span>
        </div>
        <div class="modal-b">
          <template v-if="editKind==='pos'">
            <div class="grid2">
              <div class="field"><label>所属组织 <span class="req">*</span></label>
                <select class="inp" v-model="editing.org_unit_id">
                  <option :value="null">请选择</option>
                  <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}</option>
                </select></div>
              <div class="field"><label>岗位</label>
                <select class="inp" v-model="editing.job_title_id">
                  <option :value="null">（未指定）</option>
                  <option v-for="t in titles" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select></div>
            </div>
            <div class="grid2">
              <div class="field"><label>职级</label>
                <select class="inp" v-model="editing.job_grade_id">
                  <option :value="null">（未定级）</option>
                  <option v-for="g in grades" :key="g.id" :value="g.id">{{ g.code }} {{ g.name }}</option>
                </select></div>
              <div class="field"><label>编制人数 <span class="req">*</span></label>
                <input class="inp" type="number" step="0.5" min="0.5" v-model="editing.headcount"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>汇报上级编制</label>
                <select class="inp" v-model="editing.parent_position_id">
                  <option :value="null">（顶层 / 无）</option>
                  <option v-for="r in parentOptions" :key="r.id" :value="r.id">{{ r.name }}</option>
                </select></div>
              <div class="field"><label>状态</label>
                <select class="inp" v-model="editing.status">
                  <option v-for="s in (types?types.statuses:[])" :key="s.value" :value="s.value">{{ s.label }}</option>
                </select></div>
            </div>
            <div class="grid2">
              <div class="field"><label>编制编码</label>
                <input class="inp" v-model="editing.code" placeholder="留空自动生成"></div>
              <div class="field"><label>工作地点</label>
                <input class="inp" v-model="editing.work_place" placeholder="如 白云生产基地"></div>
            </div>
            <div class="field"><label>编制名称</label>
              <input class="inp" v-model="editing.name" placeholder="留空自动生成：组织 · 岗位 · 职级"></div>
            <div class="field"><label>岗位职责</label>
              <input class="inp" v-model="editing.duty"></div>
            <div class="grid2">
              <div class="field"><label>关键岗位</label>
                <select class="inp" v-model="editing.is_key">
                  <option :value="1">是</option><option :value="0">否</option></select></div>
              <div class="field"><label>状态</label>
                <select class="inp" v-model="editing.is_active">
                  <option :value="1">启用</option><option :value="0">停用</option></select></div>
            </div>
            <div class="field"><label>备注</label><input class="inp" v-model="editing.remark"></div>
          </template>

          <template v-else-if="editKind==='title'">
            <div class="grid2">
              <div class="field"><label>编码 <span class="req">*</span></label>
                <input class="inp" v-model="editing.code" placeholder="如 JT-RND-ENG"></div>
              <div class="field"><label>岗位名称 <span class="req">*</span></label>
                <input class="inp" v-model="editing.name" placeholder="如 研发工程师"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>英文名</label><input class="inp" v-model="editing.name_en"></div>
              <div class="field"><label>默认职级序列</label>
                <select class="inp" v-model="editing.family">
                  <option v-for="f in (types?types.families:[])" :key="f.value" :value="f.value">{{ f.label }}</option>
                </select></div>
            </div>
            <div class="grid2">
              <div class="field"><label>岗位类别</label>
                <select class="inp" v-model="editing.category">
                  <option v-for="c in (types?types.categories:[])" :key="c.value" :value="c.value">{{ c.label }}</option>
                </select></div>
              <div class="field"><label>建议层级</label>
                <input class="inp" type="number" min="0" v-model="editing.level_hint"></div>
            </div>
            <div class="field"><label>岗位职责</label><input class="inp" v-model="editing.duty"></div>
            <div class="grid2">
              <div class="field"><label>排序</label><input class="inp" type="number" v-model="editing.sort_order"></div>
              <div class="field"><label>状态</label>
                <select class="inp" v-model="editing.is_active">
                  <option :value="1">启用</option><option :value="0">停用</option></select></div>
            </div>
            <div class="field"><label>备注</label><input class="inp" v-model="editing.remark"></div>
          </template>

          <template v-else>
            <div class="grid2">
              <div class="field"><label>职级编码 <span class="req">*</span></label>
                <input class="inp" v-model="editing.code" placeholder="如 T4"></div>
              <div class="field"><label>职级名称 <span class="req">*</span></label>
                <input class="inp" v-model="editing.name" placeholder="如 高级工程师级"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>序列</label>
                <select class="inp" v-model="editing.family">
                  <option v-for="f in (types?types.families:[])" :key="f.value" :value="f.value">{{ f.label }}</option>
                </select></div>
              <div class="field"><label>层级</label>
                <input class="inp" type="number" min="1" v-model="editing.level"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>英文名</label><input class="inp" v-model="editing.name_en"></div>
              <div class="field"><label>币种</label><input class="inp" v-model="editing.currency"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>薪资带宽下限</label>
                <input class="inp" type="number" v-model="editing.salary_min"></div>
              <div class="field"><label>薪资带宽上限</label>
                <input class="inp" type="number" v-model="editing.salary_max"></div>
            </div>
            <div class="field"><label>能力要求</label><input class="inp" v-model="editing.duty"></div>
            <div class="grid2">
              <div class="field"><label>排序</label><input class="inp" type="number" v-model="editing.sort_order"></div>
              <div class="field"><label>状态</label>
                <select class="inp" v-model="editing.is_active">
                  <option :value="1">启用</option><option :value="0">停用</option></select></div>
            </div>
            <div class="field"><label>备注</label><input class="inp" v-model="editing.remark"></div>
          </template>
        </div>
        <div class="modal-f">
          <button class="btn" @click="editing=null">取消</button>
          <button class="btn primary" :disabled="busy" @click="save">{{ busy?'保存中…':'保存' }}</button>
        </div>
      </div>
    </div>

    <!-- ===== 任职弹窗 ===== -->
    <div class="mask" v-if="assigning" @click.self="assigning=null">
      <div class="modal" style="max-width:520px">
        <div class="modal-h">
          <h3>任职登记</h3><span class="x-btn" @click="assigning=null">×</span>
        </div>
        <div class="modal-b">
          <div class="field"><label>编制</label>
            <input class="inp" :value="assigning.position_name" disabled></div>
          <div class="field"><label>人员 <span class="req">*</span></label>
            <select class="inp" v-model="assigning.user_id">
              <option :value="null">请选择在职人员</option>
              <option v-for="c in candidates" :key="c.id" :value="c.id">
                {{ c.full_name || c.username }}（{{ c.emp_no || '无工号' }} · {{ c.org_name || '未归属' }}）
              </option>
            </select></div>
          <div class="grid2">
            <div class="field"><label>任职方式</label>
              <!-- 主岗/兼岗 属后端枚举 ASSIGN_MODES（见 /api/position/types.assign_modes），前端从 types 取，不写死；
                   :value 转 Number 以匹配后端 INTEGER 列与 blankAssign 中的数值初值 -->
              <select class="inp" v-model="assigning.is_primary">
                <option v-for="m in (types?types.assign_modes:[])" :key="m.value"
                        :value="Number(m.value)">{{ m.label }}</option></select></div>
            <div class="field"><label>投入占比</label>
              <input class="inp" type="number" step="0.1" min="0" max="1" v-model="assigning.ratio"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>起始日期</label>
              <input class="inp" type="date" v-model="assigning.start_date"></div>
            <div class="field"><label>备注</label><input class="inp" v-model="assigning.remark"></div>
          </div>
          <div class="muted small">一个人只能有一个主岗：设为新主岗时，其原主岗自动降为兼岗。</div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="assigning=null">取消</button>
          <button class="btn primary" :disabled="busy" @click="saveAssign">{{ busy?'提交中…':'保存' }}</button>
        </div>
      </div>
    </div>
  </div>
  `,
};
