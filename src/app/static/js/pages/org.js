/* 页面：组织与权限 —— 部门、用户、角色、审计日志 */
const OrgPage = {
  setup() {
    const tab = ref('users');
    const users = ref([]), depts = ref([]), roles = ref([]), perms = ref([]), logs = ref([]);
    const me = ref(null), editing = ref(null), pwdModal = ref(false);
    const logFilter = reactive({ module: '', limit: 50 });
    const ROLE_NAME = {};
    const SCOPE = { all: '全部数据', dept: '本部门', self: '仅本人' };
    /* 员工生命周期 —— 后端 EMP_STATUSES 的前端镜像 */
    const EMP_STATUS = {
      probation: '试用期', active: '在职', suspended: '停职',
      left: '离职', retired: '退休',
    };
    const STATUS_CLS = {
      probation: 'blue', active: 'green', suspended: 'orange',
      left: 'gray', retired: 'gray',
    };

    async function load() {
      try {
        // 部门与角色是全局参照（用户表要显示部门名、角色页要下拉），
        // 必须一次性取全，否则首次进页面时下拉框与部门列都是空的。
        const [meR, uR, dR, rR, pR] = await Promise.all([
          api.get('/api/org/me'),
          api.get('/api/org/users').catch(() => []),
          api.get('/api/org/departments').catch(() => []),
          api.get('/api/org/roles').catch(() => []),
          api.get('/api/org/permissions').catch(() => []),
        ]);
        me.value = meR; users.value = uR; depts.value = dR; roles.value = rR;
        perms.value = pR;
        roles.value.forEach(r => { ROLE_NAME[r.code] = r.name; });
        if (tab.value === 'logs') {
          const p = new URLSearchParams({ limit: String(logFilter.limit) });
          if (logFilter.module) p.append('module', logFilter.module);
          logs.value = await api.get('/api/org/audit-logs?' + p.toString()).catch(() => []);
        }
      } catch (e) { notify(e.message, 'err'); }
    }
    function switchTab(t) { tab.value = t; load(); }
    // 进入页面即加载 —— 漏掉 onMounted 会导致表格永远空白
    onMounted(load);
    function createUser() {
      editing.value = { username: '', password: '', full_name: '', role: 'sales',
        dept_id: null, emp_status: 'active', join_date: '', remark: '' };
    }
    function editUser(u) {
      editing.value = { ...u, password: '' };
    }
    /* 生命周期快捷操作 —— 管理员点一下就能办理离职，不用进编辑弹窗 */
    async function setStatus(u, st) {
      if (!confirm(`将 ${u.full_name || u.username} 的状态改为「${EMP_STATUS[st]}」？`)) return;
      try {
        await api.put('/api/org/users/' + u.id, { ...u, emp_status: st });
        notify('已更新为「' + EMP_STATUS[st] + '」', 'ok');
        await load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function save() {
      const e = editing.value;
      try {
        if (e.id) {
          await api.put('/api/org/users/' + e.id, e);
          notify('已保存', 'ok');
        } else {
          await api.post('/api/org/users', e);
          notify('用户已创建', 'ok');
        }
        editing.value = null; await load();
      } catch (err) { notify(err.message, 'err'); }
    }
    async function toggle(u) {
      if (!confirm(`确定${u.is_active ? '停用' : '启用'} ${u.username}？`)) return;
      try { await api.del('/api/org/users/' + u.id); notify('已操作', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function unlock(u) {
      try { await api.post(`/api/org/users/${u.id}/unlock`, {}); notify('已解锁', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function forcePwd(u) {
      if (!confirm(`强制 ${u.username} 下次登录时修改密码？`)) return;
      try { await api.post(`/api/org/users/${u.id}/force-password`, {}); notify('已设置', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    const savePwd = async () => {
      try {
        await api.post('/api/org/me/password', pwdForm);
        notify('密码已修改', 'ok'); pwdModal.value = false;
        pwdForm.old_password = ''; pwdForm.new_password = '';
      } catch (e) { notify(e.message, 'err'); }
    };
    const pwdForm = reactive({ old_password: '', new_password: '' });
    /* ---- WorkBuddy 助手接口的 API Key ---- */
    const aiKey = ref(null);          // { exists, created_at }
    const aiKeyPlain = ref('');       // 明文只在生成/重置时显示一次
    async function loadAiKey() {
      aiKey.value = await api.get('/api/ai/key-status').catch(() => null);
    }
    async function resetAiKey() {
      if (!confirm('重置后旧 Key 立即失效，WorkBuddy 需要重新配置。继续？')) return;
      try {
        const r = await api.post('/api/ai/key-reset', {});
        aiKeyPlain.value = r.api_key;
        notify('已生成新 Key，请立即复制保存', 'ok');
        await loadAiKey();
      } catch (e) { notify(e.message, 'err'); }
    }
    function copyAiKey() {
      const txt = aiKeyPlain.value;
      if (!txt) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt)
          .then(() => notify('已复制到剪贴板', 'ok'), () => notify('复制失败，请手动选择', 'err'));
      } else notify('当前环境不支持自动复制，请手动选择文本', 'err');
    }
    const deptName = id => (depts.value.find(d => d.id === id) || {}).name || '—';
    const permText = n => (n > 900 ? '全部权限' : n + ' 项');
    const isLocked = u => u.locked_until && u.locked_until > new Date().toISOString().slice(0, 19).replace('T', ' ');

    return { tab, users, depts, roles, perms, logs, me, editing, pwdModal, pwdForm,
             logFilter, ROLE_NAME, SCOPE, EMP_STATUS, STATUS_CLS, aiKey, aiKeyPlain,
             load, switchTab, createUser, editUser, setStatus, save, toggle, unlock,
             forcePwd, savePwd, deptName, isLocked, permText,
             loadAiKey, resetAiKey, copyAiKey };
  },
  template: `
  <div>
    <div class="card">
      <div class="card-h">
        <div><h3>组织与权限</h3>
          <div class="d" v-if="me">{{ me.full_name }} · {{ me.role_name }} ·
            数据范围 {{ me.scope_label }}<span v-if="me.dept_name"> · {{ me.dept_name }}</span></div></div>
        <button class="btn ghost" @click="pwdModal=true">修改我的密码</button>
      </div>
      <div class="card-b tight">
        <div class="tabs">
          <div class="tab" :class="{on:tab==='users'}" @click="switchTab('users')">人员</div>
          <div class="tab" :class="{on:tab==='depts'}" @click="switchTab('depts')">部门</div>
          <div class="tab" :class="{on:tab==='roles'}" @click="switchTab('roles')">角色权限</div>
          <div class="tab" :class="{on:tab==='ai'}" @click="switchTab('ai'); loadAiKey()">WorkBuddy 接口</div>
          <div class="tab" :class="{on:tab==='logs'}" @click="switchTab('logs')">操作日志</div>
        </div>

        <!-- 人员 -->
        <div v-if="tab==='users'">
          <div style="padding:10px 14px;display:flex;justify-content:space-between;align-items:center">
            <span class="muted small">工号由系统生成，一经分配不再变更；离职后工号保留不复用。</span>
            <button class="btn" @click="createUser">+ 新建人员</button></div>
          <div class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>工号</th><th>用户名</th><th>姓名</th><th>角色</th><th>部门</th>
              <th>在职状态</th><th>入职</th><th>最后登录</th><th class="c">操作</th></tr></thead>
            <tbody><tr v-for="u in users" :key="u.id">
              <td data-label="工号" class="mono small">{{ u.emp_no || '—' }}</td>
              <td data-label="用户名" class="mono">{{ u.username }}</td>
              <td data-label="姓名">{{ u.full_name || '—' }}</td>
              <td data-label="角色"><span class="tag" :class="u.role==='admin'?'gold':'blue'">{{ ROLE_NAME[u.role] || u.role }}</span></td>
              <td data-label="部门" class="muted">{{ deptName(u.dept_id) }}</td>
              <td data-label="在职状态">
                <span class="tag" :class="STATUS_CLS[u.emp_status] || 'gray'">{{ u.emp_status_label }}</span>
                <span class="tag orange" v-if="u.must_change_pw" style="margin-left:3px">待改密</span>
                <span class="tag red" v-if="isLocked(u)" style="margin-left:3px">已锁定</span>
              </td>
              <td data-label="入职" class="muted small">{{ u.join_date || '—' }}</td>
              <td data-label="最后登录" class="muted small">{{ (u.last_login_at||'').slice(0,16) || '从未' }}</td>
              <td data-label="操作" class="c"><div style="display:flex;gap:4px;flex-wrap:wrap">
                <button class="btn sm ghost" @click="editUser(u)">编辑</button>
                <button class="btn sm ghost" v-if="u.emp_status==='active' && u.role!=='admin'"
                  @click="setStatus(u,'left')">办离职</button>
                <button class="btn sm ghost" v-if="u.emp_status==='left'"
                  @click="setStatus(u,'active')">复职</button>
                <button class="btn sm ghost" v-if="isLocked(u)" @click="unlock(u)">解锁</button>
                <button class="btn sm ghost" @click="forcePwd(u)">重置密码</button>
              </div></td>
            </tr></tbody>
          </table></div>
        </div>

        <!-- 部门 -->
        <div v-else-if="tab==='depts'">
          <div class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>编码</th><th>名称</th><th>类型</th><th>负责人</th><th>状态</th></tr></thead>
            <tbody><tr v-for="d in depts" :key="d.id">
              <td data-label="编码" class="mono">{{ d.code }}</td><td data-label="名称">{{ d.name }}</td>
              <td data-label="类型"><span class="tag" :class="d.kind==='factory'?'orange':d.kind==='branch'?'blue':'gray'">
                {{ d.kind==='factory'?'工厂':d.kind==='branch'?'分公司':'办公室' }}</span></td>
              <td data-label="负责人" class="muted">{{ d.leader_name || '—' }}</td>
              <td data-label="状态"><span class="tag green">启用</span></td>
            </tr></tbody>
          </table></div>
        </div>

        <!-- 角色 -->
        <div v-else-if="tab==='roles'">
          <div class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>角色</th><th>编码</th><th>数据范围</th><th class="r">权限点</th></tr></thead>
            <tbody><tr v-for="r in roles" :key="r.code">
              <td data-label="角色">{{ r.name }}</td><td data-label="编码" class="mono">{{ r.code }}</td>
              <td data-label="数据范围"><span class="tag" :class="r.scope==='all'?'red':r.scope==='dept'?'orange':'blue'">{{ r.scope_label }}</span></td>
              <td data-label="权限点" class="r">{{ permText(r.perm_count) }}</td>
            </tr></tbody>
          </table></div>
          <div style="padding:12px 14px">
            <div class="muted small" style="margin-bottom:6px">权限点说明（按模块）</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <span class="tag gray" v-for="m in perms" :key="m.module">{{ m.module }}:
                <span v-for="a in m.actions" :key="a.key"> {{ a.label }}</span></span>
            </div>
          </div>
        </div>

        <!-- WorkBuddy 助手接口 -->
        <div v-else-if="tab==='ai'">
          <div class="warn-box" style="margin:14px 14px 4px">
            <b>给 WorkBuddy 的只读查询接口。</b>
            员工可以在对话里问「ALPHA 上次报了多少」「我还有几张单没确认交期」，
            助手调用这里的接口取数回答。
            <div style="margin-top:6px">
              接口地址：<code class="mono">GET /api/ai/capabilities</code> ·
              鉴权头：<code class="mono">X-API-Key</code> ·
              请求带 <code class="mono">user</code> 参数（工号或用户名），
              服务端按该员工的权限与数据范围过滤。
            </div>
          </div>
          <div style="padding:12px 14px">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span class="tag" :class="aiKey && aiKey.exists ? 'green' : 'gray'">
                {{ aiKey && aiKey.exists ? '已启用' : '未启用' }}</span>
              <span v-if="aiKey && aiKey.created_at" class="muted small">
                创建于 {{ aiKey.created_at }}</span>
              <span class="grow"></span>
              <button class="btn primary" @click="resetAiKey">
                {{ aiKey && aiKey.exists ? '重置 Key' : '生成 Key' }}</button>
            </div>
            <div v-if="aiKeyPlain" class="warn-box" style="margin-top:12px;background:#FFF8E8">
              <b>这是唯一一次显示明文，请立即保存到 WorkBuddy 配置里：</b>
              <div style="display:flex;align-items:center;gap:9px;margin-top:7px">
                <code class="mono" style="flex:1;word-break:break-all;background:#fff;
                  border:1px solid var(--line);border-radius:5px;padding:7px 9px">{{ aiKeyPlain }}</code>
                <button class="btn sm" @click="copyAiKey">复制</button>
              </div>
              <div class="muted small" style="margin-top:6px">
                系统只保存哈希，无法再次查看。重置后旧 Key 立即失效。</div>
            </div>
            <div class="sec-title" style="margin-top:16px">不开放的能力</div>
            <div class="muted small" style="line-height:1.9">
              密码 / 授权码 / 密钥 —— 任何接口都不返回<br>
              成本 / 毛利 / 利润 —— 商业机密<br>
              审计日志明细 —— 仅统计，不给逐条记录<br>
              写操作 —— 本模块全部只读，改数据走正常界面<br>
              批量导出 —— 单次返回条数有上限
            </div>
          </div>
        </div>

        <!-- 审计 -->
        <div v-else>
          <div class="filters">
            <input class="inp" placeholder="模块，如 order / finance" v-model="logFilter.module" @keyup.enter="load">
            <button class="btn ghost" @click="load">查询</button>
          </div>
          <div class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>时间</th><th>操作人</th><th>模块</th><th>动作</th><th>对象</th><th>IP</th></tr></thead>
            <tbody><tr v-for="l in logs" :key="l.id">
              <td data-label="时间" class="muted small">{{ (l.created_at||'').slice(5,16) }}</td>
              <td data-label="操作人">{{ l.username || '系统' }}</td>
              <td data-label="模块"><span class="tag gray">{{ l.module }}</span></td>
              <td data-label="动作">{{ l.action }}</td>
              <td data-label="对象" class="muted small">{{ l.target_type }} {{ l.target_label || l.target_id }}</td>
              <td data-label="IP" class="muted small">{{ l.ip || '—' }}</td>
            </tr><tr v-if="!logs.length"><td colspan="6" class="muted">暂无记录</td></tr></tbody>
          </table></div>
        </div>
      </div>
    </div>

    <!-- 编辑用户 -->
    <div class="modal" v-if="editing" @click.self="editing=null">
      <div class="modal-h"><div class="ttl">{{ editing.id ? '编辑用户' : '新建用户' }}</div>
        <button class="x-btn" @click="editing=null">×</button></div>
      <div class="modal-b">
        <div class="grid2">
          <div><label>工号（系统生成，不可修改）</label>
            <input class="inp mono" :value="editing.emp_no || (editing.id ? '' : '保存后自动分配')" disabled></div>
          <div><label>用户名</label><input class="inp" v-model="editing.username" :disabled="!!editing.id"></div>
          <div><label>{{ editing.id ? '重置密码（留空不改）' : '初始密码' }}</label>
            <input class="inp" type="password" v-model="editing.password"></div>
          <div><label>姓名</label><input class="inp" v-model="editing.full_name"></div>
          <div><label>邮箱</label><input class="inp" v-model="editing.email"></div>
          <div><label>手机</label><input class="inp" v-model="editing.phone"></div>
          <div><label>角色</label><select class="inp" v-model="editing.role">
            <option v-for="r in roles" :value="r.code" :key="r.code">{{ r.name }}</option></select></div>
          <div><label>部门</label><select class="inp" v-model="editing.dept_id">
            <option :value="null">不指定</option>
            <option v-for="d in depts" :value="d.id" :key="d.id">{{ d.name }}</option></select></div>
          <div><label>在职状态</label><select class="inp" v-model="editing.emp_status">
            <option v-for="(label, key) in EMP_STATUS" :value="key" :key="key">{{ label }}</option></select></div>
          <div><label>入职日期</label><input class="inp" type="date" v-model="editing.join_date"></div>
          <div v-if="editing.emp_status==='left' || editing.emp_status==='retired'">
            <label>离职日期</label><input class="inp" type="date" v-model="editing.leave_date"></div>
          <div style="grid-column:1/-1"><label>备注</label>
            <input class="inp" v-model="editing.remark" placeholder="如：岗位、交接情况"></div>
        </div>
        <div class="muted small" style="margin-top:10px">
          密码需至少 8 位，含大小写字母、数字、符号中的两类；新用户首次登录须修改密码。<br>
          在职状态为「离职 / 退休 / 停职」时账号自动禁止登录，但历史单据的责任归属保留。
        </div>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="editing=null">取消</button>
        <button class="btn" @click="save">保存</button></div>
    </div>

    <!-- 改密 -->
    <div class="modal" v-if="pwdModal" @click.self="pwdModal=false">
      <div class="modal-h"><div class="ttl">修改密码</div><button class="x-btn" @click="pwdModal=false">×</button></div>
      <div class="modal-b">
        <div style="margin-bottom:10px"><label>原密码</label><input class="inp" type="password" v-model="pwdForm.old_password"></div>
        <div><label>新密码</label><input class="inp" type="password" v-model="pwdForm.new_password"></div>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="pwdModal=false">取消</button>
        <button class="btn" @click="savePwd">确认修改</button></div>
    </div>
  </div>`
};
