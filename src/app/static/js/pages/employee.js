/* ============================ 组件：员工档案（M003） ============================
   第三轴（人员轴）的人事属性层：花名册 + 档案详情（证件/合同/社保/银行卡/紧急联系人）
   + 全生命周期异动流水（入职/转正/调岗/晋升/离职/退休）。
   所有中文枚举一律从后端 /api/employee/types 取，前端不硬编码；
   身份证 / 银行卡属敏感字段，仅在 `employee.sensitive.view` 权限下可见、可编辑。
============================================================================ */
const EmployeePage = {
  setup() {
    const T = reactive({});                    // 枚举 value -> 中文名
    const types = ref(null);
    const loading = ref(true);
    const busy = ref(false);
    const canEdit = ref(false);
    const canSensitive = ref(false);

    const rows = ref([]);                       // 花名册
    const filter = reactive({ keyword: '', status: '', org_unit_id: '' });
    const orgs = ref([]);                       // 组织下拉（取 org_units 扁平列表）
    const positions = ref([]);                  // 异动目标岗位下拉

    const selectedId = ref(null);               // 选中的 user_id
    const detail = ref(null);                   // 档案详情（已按权限脱敏）
    const profileId = ref(null);                // 当前档案 id（有则编辑，无则新建）
    const events = ref([]);                     // 异动流水
    const editing = ref(false);                 // 是否处于档案编辑态

    /* 权限：无编辑/敏感权限时按钮不出现，而不是点了才报错 */
    async function loadMe() {
      try {
        const me = await api.get('/api/org/me');
        const ps = me.permissions || [];
        canEdit.value = ps.includes('*') || ps.includes('employee.edit') ||
          ps.includes('employee');
        canSensitive.value = ps.includes('*') ||
          ps.includes('employee.sensitive.view');
      } catch (e) { canEdit.value = false; canSensitive.value = false; }
    }

    async function loadRefs() {
      const [t, o, p] = await Promise.all([
        api.get('/api/employee/types'),
        api.get('/api/structure/units?active=-1').catch(() => []),
        api.get('/api/position/positions?active=1').catch(() => []),
      ]);
      types.value = t;
      (t.event_types || []).forEach(x => { T[x.value] = x.label; });
      (t.statuses || []).forEach(x => { T[x.value] = x.label; });
      orgs.value = o;
      positions.value = p;
    }

    function query() {
      const p = new URLSearchParams();
      if (filter.keyword) p.set('keyword', filter.keyword);
      if (filter.status) p.set('status', filter.status);
      if (filter.org_unit_id) p.set('org_unit_id', filter.org_unit_id);
      return p.toString();
    }

    async function load() {
      loading.value = true;
      try {
        await loadRefs();
        rows.value = await api.get('/api/employee/list?' + query());
        // 默认选中第一行，方便首次进入即有详情可看
        if (selectedId.value == null && rows.value.length) {
          await select(rows.value[0]);
        } else if (selectedId.value != null) {
          await select({ id: selectedId.value });
        }
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    async function reload() { await load(); }

    const orgName = id => (orgs.value.find(o => o.id === id) || {}).name || '—';
    const posName = id => (positions.value.find(p => p.id === id) || {}).name || '—';

    /* ---- 选中某人 → 拉详情 + 异动 ---- */
    async function select(row) {
      selectedId.value = row.id;
      editing.value = false;
      try {
        const [prof, evs] = await Promise.all([
          api.get('/api/employee/profiles?user_id=' + row.id).catch(() => null),
          api.get('/api/employee/events?user_id=' + row.id).catch(() => []),
        ]);
        detail.value = prof;
        profileId.value = prof ? prof.id : null;
        events.value = evs;
      } catch (e) { notify(e.message, 'err'); }
    }

    /* ---- 档案编辑 ---- */
    function blankProfile() {
      return {
        user_id: selectedId.value, id_card: '', contract_no: '', contract_start: '',
        contract_end: '', social_no: '', bank_name: '', bank_account: '',
        emergency_contact: '', emergency_phone: '', hire_date: '', id_scan_path: '',
        is_active: 1,
      };
    }
    function editProfile() {
      editing.value = true;
      detail.value = JSON.parse(JSON.stringify(detail.value || blankProfile()));
      detail.value.user_id = selectedId.value;
    }
    function newProfile() {
      editing.value = true;
      detail.value = blankProfile();
      profileId.value = null;
    }
    async function saveProfile() {
      const d = detail.value;
      if (!d.user_id) { notify('请先选择一名员工', 'err'); return; }
      busy.value = true;
      try {
        if (profileId.value) {
          await api.put('/api/employee/profiles/' + profileId.value, d);
        } else {
          const r = await api.post('/api/employee/profiles', d);
          profileId.value = r.id;
        }
        notify('档案已保存', 'ok');
        editing.value = false;
        await select({ id: selectedId.value });
        await reload();
      } catch (e) { notify(e.message, 'err'); }
      finally { busy.value = false; }
    }
    async function removeProfile() {
      if (!profileId.value) return;
      if (!confirm('注销该员工档案？历史记录保留，仅不再参与新业务。')) return;
      try {
        await api.del('/api/employee/profiles/' + profileId.value);
        notify('档案已注销', 'ok');
        await select({ id: selectedId.value });
        await reload();
      } catch (e) { notify(e.message, 'err'); }
    }

    /* ---- 异动登记 ---- */
    const eventing = ref(null);
    function openEvent() {
      eventing.value = {
        user_id: selectedId.value, event_type: 'transfer',
        from_position_id: detail.value ? detail.value.position_id : null,
        to_position_id: null, event_date: '', remark: '',
      };
    }
    async function saveEvent() {
      const e = eventing.value;
      if (!e.user_id) { notify('请先选择一名员工', 'err'); return; }
      if ((e.event_type === 'onboard' || e.event_type === 'transfer' ||
           e.event_type === 'promote') && !e.to_position_id) {
        notify('入职 / 调岗 / 晋升 需指定异动后岗位', 'err'); return;
      }
      busy.value = true;
      try {
        await api.post('/api/employee/events', e);
        notify('异动已登记', 'ok');
        eventing.value = null;
        await select({ id: selectedId.value });
        await reload();
      } catch (err) { notify(err.message, 'err'); }
      finally { busy.value = false; }
    }

    onMounted(async () => { await loadMe(); await load(); });
    return {
      T, types, loading, busy, canEdit, canSensitive, rows, filter, orgs,
      positions, selectedId, detail, profileId, events, editing, eventing,
      orgName, posName, load, reload, select, editProfile, newProfile,
      saveProfile, removeProfile, openEvent, saveEvent, notify
    };
  },
  template: `
  <div>
    <div style="display:grid;grid-template-columns:1.5fr 1fr;gap:14px;align-items:start">
      <!-- ===== 花名册 ===== -->
      <div class="card">
        <div class="card-h">
          <div><h3>员工花名册</h3>
            <div class="d">人员 · 档案摘要 · 当前主岗 · 所属组织（敏感字段不在此列表下发）</div></div>
        </div>
        <div class="card-b">
          <div class="toolbar" style="margin-bottom:10px">
            <input class="inp" style="max-width:170px" v-model="filter.keyword" @keyup.enter="reload" placeholder="姓名 / 用户名 / 工号">
            <select class="inp" style="max-width:130px" v-model="filter.status" @change="reload">
              <option value="">全部状态</option>
              <option v-for="s in (types?types.statuses:[])" :key="s.value" :value="s.value">{{ s.label }}</option>
            </select>
            <select class="inp" style="max-width:170px" v-model="filter.org_unit_id" @change="reload">
              <option value="">全部组织</option>
              <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}</option>
            </select>
            <span class="grow"></span>
            <button class="btn sm" @click="load">刷新</button>
          </div>
          <div class="tbl-wrap" style="max-height:560px;overflow:auto">
            <table class="tbl responsive">
              <thead><tr>
                <th>姓名</th><th style="width:96px">工号</th><th style="width:80px">状态</th>
                <th>当前主岗</th><th>所属组织</th><th class="c" style="width:70px">档案</th>
              </tr></thead>
              <tbody>
                <tr v-if="!rows.length" style="height:120px">
                  <td colspan="6" class="muted c" data-label="">没有匹配的员工</td></tr>
                <tr v-for="r in rows" :key="r.id" @click="select(r)"
                    :style="{background: selectedId===r.id?'#FFF8E8':'', cursor:'pointer'}">
                  <td data-label="姓名"><b>{{ r.full_name || r.username }}</b>
                    <div class="small muted">{{ r.username }}</div></td>
                  <td class="mono small" data-label="工号">{{ r.emp_no || '—' }}</td>
                  <td data-label="状态"><span class="tag" :class="r.emp_status==='active'?'ok':(r.emp_status==='left'||r.emp_status==='retired'?'err':'warn')">{{ r.emp_status_label }}</span></td>
                  <td data-label="当前主岗">{{ r.position_name || '—' }}</td>
                  <td data-label="所属组织">{{ r.org_unit_name || '—' }}</td>
                  <td class="c" data-label="档案"><span v-if="r.has_profile" class="tag blue">有</span>
                    <span v-else class="muted small">无</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- ===== 档案详情 ===== -->
      <div class="card">
        <div class="card-h">
          <div><h3>档案详情</h3>
            <div class="d">{{ detail ? (detail.full_name||detail.username) + ' 的人事档案' : '点击左侧员工查看档案与异动' }}</div></div>
          <div v-if="selectedId!=null && !editing">
            <button class="btn sm" v-if="canEdit && !profileId" @click="newProfile">＋ 建档案</button>
            <button class="btn sm" v-if="canEdit && profileId" @click="editProfile">编辑</button>
          </div>
        </div>
        <div class="card-b">
          <div v-if="selectedId==null" class="muted small">未选择员工。</div>
          <template v-else-if="editing">
            <div class="field"><label>合同编号</label>
              <input class="inp" v-model="detail.contract_no"></div>
            <div class="grid2">
              <div class="field"><label>合同起始</label><input class="inp" type="date" v-model="detail.contract_start"></div>
              <div class="field"><label>合同截止</label><input class="inp" type="date" v-model="detail.contract_end"></div>
            </div>
            <div class="field" v-if="canSensitive"><label>社保账号</label><input class="inp" v-model="detail.social_no"></div>
            <div class="field"><label>紧急联系人</label><input class="inp" v-model="detail.emergency_contact"></div>
            <div class="field" v-if="canSensitive"><label>紧急联系电话</label><input class="inp" v-model="detail.emergency_phone"></div>
            <div class="field"><label>入职日期</label><input class="inp" type="date" v-model="detail.hire_date"></div>
            <div class="field" v-if="canSensitive"><label>证件扫描路径</label><input class="inp" v-model="detail.id_scan_path" placeholder="如 scans/WV-EMP-0001.jpg"></div>
            <!-- 敏感字段：仅 sensitive 权限可见、可编辑 -->
            <div class="grid2" v-if="canSensitive">
              <div class="field"><label>身份证号</label><input class="inp" v-model="detail.id_card"></div>
              <div class="field"><label>银行卡号</label><input class="inp" v-model="detail.bank_account"></div>
            </div>
            <div class="field" v-if="canSensitive"><label>开户行</label><input class="inp" v-model="detail.bank_name"></div>
            <div v-else class="muted small" style="padding:6px 0">
              （身份证号 / 银行卡号 / 社保号 / 紧急联系电话 / 证件扫描件 属敏感字段，需「员工档案·敏感」权限方可查看与编辑）</div>
            <div style="display:flex;gap:8px;margin-top:8px">
              <button class="btn primary sm" :disabled="busy" @click="saveProfile">{{ busy?'保存中…':'保存' }}</button>
              <button class="btn sm" @click="editing=false">取消</button>
              <button class="btn sm danger" v-if="profileId" @click="removeProfile">注销档案</button>
            </div>
          </template>
          <template v-else>
            <table style="width:100%;font-size:12.5px;border-collapse:collapse" v-if="detail">
              <tr><td class="muted" style="width:90px;padding:5px 0">姓名</td><td>{{ detail.full_name || detail.username }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">工号</td><td class="mono">{{ detail.emp_no || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">状态</td><td>{{ detail.emp_status_label || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">当前主岗</td><td>{{ detail.position_name || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">所属组织</td><td>{{ detail.org_unit_name || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">合同编号</td><td>{{ detail.contract_no || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">合同期</td>
                <td>{{ detail.contract_start || '—' }} ~ {{ detail.contract_end || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">社保账号</td><td>{{ detail.social_no || '—' }}</td></tr>
              <tr><td class="muted" style="padding:5px 0">紧急联系人</td>
                <td>{{ detail.emergency_contact || '—' }} <span class="muted small">{{ detail.emergency_phone || '' }}</span></td></tr>
              <tr><td class="muted" style="padding:5px 0">入职日期</td><td>{{ detail.hire_date || '—' }}</td></tr>
              <tr v-if="canSensitive"><td class="muted" style="padding:5px 0">身份证号</td><td class="mono">{{ detail.id_card || '—' }}</td></tr>
              <tr v-if="canSensitive"><td class="muted" style="padding:5px 0">银行卡号</td><td class="mono">{{ detail.bank_account || '—' }}</td></tr>
              <tr v-if="canSensitive"><td class="muted" style="padding:5px 0">开户行</td><td>{{ detail.bank_name || '—' }}</td></tr>
            </table>
            <div v-else class="muted small">该员工暂无人事档案，点击右上角「＋ 建档案」创建（需员工档案编辑权限）。</div>

            <!-- 异动流水时间线 -->
            <div style="margin-top:14px;font-weight:700;font-size:13px">
              异动流水（{{ events.length }}）
              <button class="btn sm" style="float:right" v-if="canEdit" @click="openEvent">＋ 登记异动</button>
            </div>
            <div v-if="!events.length" class="muted small" style="margin-top:6px">暂无异动记录。</div>
            <ul class="timeline" v-else style="margin-top:8px">
              <li v-for="e in events" :key="e.id">
                <div style="display:flex;justify-content:space-between">
                  <b>{{ T[e.event_type] || e.event_type }}</b>
                  <span class="muted small">{{ e.event_date }}</span>
                </div>
                <div class="small muted" v-if="e.to_position_name || e.from_position_name">
                  <template v-if="e.from_position_name">由 {{ e.from_position_name }}</template>
                  <template v-if="e.to_position_name"> → {{ e.to_position_name }}</template>
                </div>
                <div class="small" v-if="e.remark">{{ e.remark }}</div>
                <div class="small muted">操作人：{{ e.operator }}</div>
              </li>
            </ul>
          </template>
        </div>
      </div>
    </div>

    <!-- ===== 异动登记弹窗 ===== -->
    <div class="mask" v-if="eventing" @click.self="eventing=null">
      <div class="modal" style="max-width:520px">
        <div class="modal-h"><h3>登记异动</h3><span class="x-btn" @click="eventing=null">×</span></div>
        <div class="modal-b">
          <div class="field"><label>异动类型</label>
            <select class="inp" v-model="eventing.event_type">
              <option v-for="t in (types?types.event_types:[])" :key="t.value" :value="t.value">{{ t.label }}</option>
            </select></div>
          <div class="field"><label>异动后岗位</label>
            <select class="inp" v-model="eventing.to_position_id">
              <option :value="null">（不变 / 离职退休无需填）</option>
              <option v-for="p in positions" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select></div>
          <div class="field"><label>异动日期</label><input class="inp" type="date" v-model="eventing.event_date"></div>
          <div class="field"><label>备注</label><input class="inp" v-model="eventing.remark"></div>
          <div class="muted small">入职 / 调岗 / 晋升 会同步更新该员工的任职关系（旧岗自动结束，新开主岗）；离职 / 退休 结束所有在职任职；转正仅留痕。</div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="eventing=null">取消</button>
          <button class="btn primary" :disabled="busy" @click="saveEvent">{{ busy?'提交中…':'保存' }}</button>
        </div>
      </div>
    </div>
  </div>
  `,
};
