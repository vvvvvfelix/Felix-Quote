/* ============================ 组件：审批中心（M005） ============================
   通用审批引擎的前端入口：审批中心（待办 / 已办 / 我发起的 / 流程监控）+ 请假申请示例。
   所有「申请→审批」类单据未来都复用此引擎，本页只做 HTTP 编排与展示，流转逻辑在后端。
   权限：workflow.view 可见入口；workflow.approve 可处理待办；workflow.manage 可改流程定义。
============================================================================ */
const WorkflowPage = {
  setup() {
    const me = ref(null);
    const tab = ref('todo');                 // todo / done / started / monitor

    const todo = ref([]);                     // 待我审批
    const done = ref([]);                     // 我已处理
    const started = ref([]);                  // 我发起的
    const monitor = ref([]);                  // 流程监控
    const monitorStatus = ref('');            // 监控筛选状态
    const users = ref([]);                    // 转办/加签目标候选
    const detail = ref(null);                 // 实例详情（任务 + 流水）
    const detailId = ref(null);

    const loading = ref(false);
    const showLeave = ref(false);              // 请假申请表单开关
    const leaveTypes = ref([]);
    const leaveForm = reactive({ leave_type: 'personal', days: 1, reason: '', start_date: '' });
    const leaveBusy = ref(false);

    /* 操作弹窗：approve / reject / transfer / addsign 共用一个 ModalShell */
    const action = reactive({ open: false, type: '', taskId: null, userId: null, comment: '' });

    const canManage = computed(() => {
      const ps = (me.value && me.value.permissions) || [];
      return ps.includes('*') || ps.includes('workflow.manage');
    });

    /* 枚举映射 */
    const INST_STATUS = {
      running: '审批中', approved: '已通过', rejected: '已驳回', canceled: '已撤回'
    };
    const TASK_STATUS = {
      pending: '待审批', approved: '已批准', rejected: '已驳回',
      transferred: '已转办', cancelled: '已取消'
    };
    const MODE_LABEL = { or: '或签', and: '会签' };
    const LEAVE_LABEL = {
      sick: '病假', annual: '年假', personal: '事假',
      marriage: '婚假', maternity: '产假', bereavement: '丧假'
    };
    const instLabel = s => INST_STATUS[s] || s;
    const taskLabel = s => TASK_STATUS[s] || s;
    const modeLabel = m => MODE_LABEL[m] || m;
    const leaveLabel = t => LEAVE_LABEL[t] || t;
    const userName = id => {
      const u = (users.value || []).find(x => x.id === id);
      return u ? (u.full_name || u.username) : ('#' + id);
    };

    async function loadMe() {
      try { me.value = await api.get('/api/org/me'); } catch (e) { me.value = null; }
    }

    async function loadTab() {
      loading.value = true;
      try {
        if (tab.value === 'todo') todo.value = await api.get('/api/workflow/my-tasks').catch(() => []);
        else if (tab.value === 'done') done.value = await api.get('/api/workflow/my-done').catch(() => []);
        else if (tab.value === 'started') started.value = await api.get('/api/workflow/my-started').catch(() => []);
        else if (tab.value === 'monitor') {
          const q = monitorStatus.value ? ('?status=' + monitorStatus.value) : '';
          monitor.value = await api.get('/api/workflow/instances' + q).catch(() => []);
        }
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    function switchTab(t) { tab.value = t; detail.value = null; loadTab(); }

    async function openDetail(iid) {
      try { detail.value = await api.get('/api/workflow/instances/' + iid); detailId.value = iid; }
      catch (e) { notify(e.message, 'err'); }
    }
    function closeDetail() { detail.value = null; detailId.value = null; }

    /* 操作：批准 / 驳回 / 转办 / 加签 */
    function openAction(t, type) {
      action.open = true; action.type = type; action.taskId = t.id; action.userId = null; action.comment = '';
      if (type === 'transfer' || type === 'addsign') {
        if (!users.value.length) loadUsers();
      }
    }
    function closeAction() { action.open = false; }

    async function loadUsers() {
      try { users.value = await api.get('/api/org/users').catch(() => []); } catch (e) { users.value = []; }
    }

    async function confirmAction() {
      const map = {
        approve: id => api.post('/api/workflow/tasks/' + id + '/approve', { comment: action.comment }),
        reject: id => api.post('/api/workflow/tasks/' + id + '/reject', { comment: action.comment }),
        transfer: id => api.post('/api/workflow/tasks/' + id + '/transfer', { target_user_id: action.userId, comment: action.comment }),
        addsign: id => api.post('/api/workflow/tasks/' + id + '/add-sign', { target_user_id: action.userId, comment: action.comment }),
      };
      if ((action.type === 'transfer' || action.type === 'addsign') && !action.userId) {
        notify('请选择目标人员', 'warn'); return;
      }
      try {
        await map[action.type](action.taskId);
        notify('操作成功', 'ok');
        closeAction();
        await loadTab();
        if (detailId.value) await openDetail(detailId.value);
      } catch (e) { notify(e.message, 'err'); }
    }

    async function cancelInst(inst) {
      if (!confirm('确认撤回该流程？')) return;
      try {
        await api.post('/api/workflow/instances/' + inst.id + '/cancel', { comment: '' });
        notify('已撤回', 'ok');
        await loadTab();
        if (detailId.value) await openDetail(detailId.value);
      } catch (e) { notify(e.message, 'err'); }
    }

    /* 请假申请 */
    async function loadLeaveTypes() {
      try { leaveTypes.value = await api.get('/api/workflow/leave/types').catch(() => []); } catch (e) { leaveTypes.value = []; }
    }
    function openLeave() { showLeave.value = true; loadLeaveTypes(); }
    function closeLeave() { showLeave.value = false; }
    async function submitLeave() {
      leaveBusy.value = true;
      try {
        await api.post('/api/workflow/leave', { ...leaveForm });
        notify('请假申请已提交，已进入审批流程', 'ok');
        showLeave.value = false;
        switchTab('started');
      } catch (e) { notify(e.message, 'err'); }
      finally { leaveBusy.value = false; }
    }

    async function loadMine() {
      // 进入页面默认拉「待办」，并预热其他列表用的用户字典
      await loadUsers();
      await loadTab();
    }

    onMounted(async () => {
      await loadMe();
      await loadMine();
    });

    return {
      me, tab, todo, done, started, monitor, monitorStatus, users, detail,
      loading, showLeave, leaveTypes, leaveForm, leaveBusy, action, canManage,
      instLabel, taskLabel, modeLabel, leaveLabel, userName,
      switchTab, openDetail, closeDetail, openAction, closeAction, confirmAction,
      cancelInst, openLeave, closeLeave, submitLeave
    };
  },
  template: `
  <div class="wf">
    <div class="wf-head">
      <div class="wf-tabs">
        <span :class="{on:tab==='todo'}" @click="switchTab('todo')">待我审批 <i class="bd" v-if="todo.length">{{ todo.length }}</i></span>
        <span :class="{on:tab==='done'}" @click="switchTab('done')">我已处理</span>
        <span :class="{on:tab==='started'}" @click="switchTab('started')">我发起的</span>
        <span :class="{on:tab==='monitor'}" @click="switchTab('monitor')">流程监控</span>
      </div>
      <button class="btn primary sm" @click="openLeave">＋ 发起请假</button>
    </div>

    <div v-if="loading" class="wf-loading">载入中…</div>

    <!-- 待办 -->
    <div v-else-if="tab==='todo'">
      <div v-if="!todo.length" class="wf-empty">暂无待办，喝杯茶 ☕</div>
      <div v-for="t in todo" :key="t.id" class="wf-card">
        <div class="wf-card-h">
          <b>{{ t.inst_title || t.inst_code }}</b>
          <span class="tag">{{ modeLabel(t.mode) }}</span>
          <span class="tag blue">{{ t.node_name }}</span>
        </div>
        <div class="wf-card-m">单号 {{ t.inst_code }} · 发起人 {{ t.applicant_name||'—' }} · {{ t.biz_type||'' }}</div>
        <div class="wf-card-f">
          <button class="btn sm ok" @click="openAction(t,'approve')">批准</button>
          <button class="btn sm warn" @click="openAction(t,'reject')">驳回</button>
          <button class="btn sm" @click="openAction(t,'transfer')">转办</button>
          <button class="btn sm" @click="openAction(t,'addsign')">加签</button>
          <button class="btn sm ghost" @click="openDetail(t.instance_id)">查看</button>
        </div>
      </div>
    </div>

    <!-- 已办 -->
    <div v-else-if="tab==='done'">
      <div v-if="!done.length" class="wf-empty">还没有处理过的审批</div>
      <div v-for="t in done" :key="t.id" class="wf-card">
        <div class="wf-card-h">
          <b>{{ t.inst_title || t.inst_code }}</b>
          <span class="tag" :class="t.status==='approved'?'ok':(t.status==='rejected'?'err':'blue')">{{ taskLabel(t.status) }}</span>
        </div>
        <div class="wf-card-m">单号 {{ t.inst_code }} · {{ t.node_name }} · 发起人 {{ t.applicant_name||'—' }}</div>
        <div class="wf-card-f"><button class="btn sm ghost" @click="openDetail(t.instance_id)">查看</button></div>
      </div>
    </div>

    <!-- 我发起的 -->
    <div v-else-if="tab==='started'">
      <div v-if="!started.length" class="wf-empty">还没有发起过流程，点右上角「发起请假」试试</div>
      <div v-for="i in started" :key="i.id" class="wf-card">
        <div class="wf-card-h">
          <b>{{ i.title }}</b>
          <span class="tag" :class="i.status==='approved'?'ok':(i.status==='rejected'?'err':(i.status==='running'?'blue':'gray'))">{{ instLabel(i.status) }}</span>
        </div>
        <div class="wf-card-m">单号 {{ i.code }} · 流程 {{ i.def_name }} · {{ i.started_at }}</div>
        <div class="wf-card-f"><button class="btn sm ghost" @click="openDetail(i.id)">查看进度</button></div>
      </div>
    </div>

    <!-- 流程监控 -->
    <div v-else-if="tab==='monitor'">
      <div class="wf-filter">
        状态：
        <select class="inp sm" v-model="monitorStatus" @change="loadTab()">
          <option value="">全部</option>
          <option value="running">审批中</option>
          <option value="approved">已通过</option>
          <option value="rejected">已驳回</option>
          <option value="canceled">已撤回</option>
        </select>
      </div>
      <div v-if="!monitor.length" class="wf-empty">暂无流程实例</div>
      <div v-for="i in monitor" :key="i.id" class="wf-card">
        <div class="wf-card-h">
          <b>{{ i.title }}</b>
          <span class="tag" :class="i.status==='approved'?'ok':(i.status==='rejected'?'err':(i.status==='running'?'blue':'gray'))">{{ instLabel(i.status) }}</span>
        </div>
        <div class="wf-card-m">单号 {{ i.code }} · {{ i.def_name }} · 发起人 {{ i.applicant_name||'—' }} · {{ i.started_at }}</div>
        <div class="wf-card-f"><button class="btn sm ghost" @click="openDetail(i.id)">查看</button></div>
      </div>
    </div>

    <!-- 实例详情（任务 + 流水） -->
    <div v-if="detail" class="wf-detail">
      <div class="wf-detail-h">
        <b>流程详情 · {{ detail.code }}</b>
        <span class="tag" :class="detail.status==='approved'?'ok':(detail.status==='rejected'?'err':(detail.status==='running'?'blue':'gray'))">{{ instLabel(detail.status) }}</span>
        <a style="margin-left:auto;color:#8B90A0;cursor:pointer" @click="closeDetail">收起 ×</a>
      </div>
      <div class="wf-detail-body">
        <div><b>标题：</b>{{ detail.title }}　<b>流程：</b>{{ detail.def_name }}　<b>发起人：</b>{{ detail.applicant_name||'—' }}</div>
        <div class="wf-sub">审批任务</div>
        <table class="wf-tasks">
          <thead><tr><th>节点</th><th>方式</th><th>审批人</th><th>状态</th><th>意见</th></tr></thead>
          <tbody>
            <tr v-for="t in detail.tasks" :key="t.id">
              <td>{{ t.node_name }}</td>
              <td>{{ modeLabel(t.mode) }}</td>
              <td>{{ userName(t.assignee_id) }}<template v-if="t.transfer_to"> → {{ userName(t.transfer_to) }}</template></td>
              <td><span class="tag" :class="t.status==='approved'?'ok':(t.status==='rejected'?'err':(t.status==='transferred'?'blue':'gray'))">{{ taskLabel(t.status) }}</span></td>
              <td>{{ t.comment||'—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="wf-sub">流程流水</div>
        <div class="wf-log">
          <div v-for="l in detail.logs" :key="l.id" class="wf-log-item">
            <span class="wf-log-act">{{ l.action }}</span>
            <span class="wf-log-who">{{ l.actor_name||'系统' }}</span>
            <span class="wf-log-c">（{{ l.created_at }}）</span>
            <span v-if="l.comment">— {{ l.comment }}</span>
          </div>
        </div>
        <div v-if="detail.status==='running'" style="margin-top:10px">
          <button class="btn sm warn" @click="cancelInst(detail)">撤回流程</button>
        </div>
      </div>
    </div>

    <!-- 请假申请表单 -->
    <div v-if="showLeave" class="mask" @click.self="closeLeave">
      <div class="modal">
        <div class="modal-h"><h3>发起请假申请</h3><span class="x-btn" @click="closeLeave">×</span></div>
        <div class="modal-b">
          <div class="grid2">
            <div class="field"><label>请假类型</label>
              <select class="inp" v-model="leaveForm.leave_type">
                <option v-for="x in leaveTypes" :key="x.value" :value="x.value">{{ x.label }}</option>
              </select></div>
            <div class="field"><label>天数</label><input class="inp" type="number" step="0.5" v-model="leaveForm.days"></div>
          </div>
          <div class="field"><label>开始日期</label><input class="inp" type="date" v-model="leaveForm.start_date"></div>
          <div class="field"><label>事由</label><textarea class="inp" rows="3" v-model="leaveForm.reason" placeholder="可选"></textarea></div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="closeLeave">取消</button>
          <button class="btn primary" :disabled="leaveBusy" @click="submitLeave">{{ leaveBusy?'提交中…':'提交申请' }}</button>
        </div>
      </div>
    </div>

    <!-- 操作弹窗 -->
    <div v-if="action.open" class="mask" @click.self="closeAction">
      <div class="modal">
        <div class="modal-h"><h3>{{ {approve:'批准',reject:'驳回',transfer:'转办',addsign:'加签'}[action.type] }}</h3><span class="x-btn" @click="closeAction">×</span></div>
        <div class="modal-b">
          <div class="field" v-if="action.type==='transfer'||action.type==='addsign'"><label>目标人员</label>
            <select class="inp" v-model="action.userId">
              <option value="">请选择</option>
              <option v-for="u in users" :key="u.id" :value="u.id">{{ u.full_name||u.username }}（{{ u.role }}）</option>
            </select></div>
          <div class="field"><label>审批意见</label><textarea class="inp" rows="3" v-model="action.comment" placeholder="可选"></textarea></div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="closeAction">取消</button>
          <button class="btn primary" @click="confirmAction">确定</button>
        </div>
      </div>
    </div>
  </div>
  `
};
