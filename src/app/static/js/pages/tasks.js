/* 页面：待办中心 —— 跨部门协作枢纽。
   VPN 场景下用 30 秒轮询，不用 WebSocket（10-30 人、分钟级变化足够）。 */
const TaskPage = {
  setup(props, { emit }) {
    const list = ref([]), board = ref(null), loading = ref(false);
    const tab = ref('mine'), done = ref(null), result = ref('');
    let timer = null;

    async function load() {
      loading.value = true;
      try {
        if (tab.value === 'mine') {
          list.value = await api.get('/api/tasks?status=open&mine=1');
        } else if (tab.value === 'done') {
          list.value = await api.get('/api/tasks?status=done&mine=1');
        } else {
          board.value = await api.get('/api/tasks/board');
        }
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    function switchTab(t) { tab.value = t; load(); }
    function openDone(t) { done.value = t; result.value = ''; }
    async function finish() {
      try {
        await api.post(`/api/tasks/${done.value.id}/done`, { result: result.value });
        notify('已完成', 'ok'); done.value = null; await load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function cancel(t) {
      if (!confirm('取消这条待办？')) return;
      try { await api.post(`/api/tasks/${t.id}/cancel`, {}); notify('已取消', 'ok'); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    onMounted(() => {
      load();
      timer = setInterval(load, 60000);   // 1 分钟轮询，多人协作时及时看到新任务
    });
    onUnmounted(() => { if (timer) clearInterval(timer); });

    const badge = t => (t.is_overdue ? 'red' : t.priority === 'urgent' ? 'orange'
      : t.is_today ? 'gold' : 'gray');
    const badgeText = t => (t.is_overdue ? '已超期' : t.priority === 'urgent' ? '紧急'
      : t.is_today ? '今天' : '');
    const fmtDate = v => (v || '').slice(0, 16);

    return {
      list, board, loading, tab, done, result,
      load, switchTab, openDone, finish, cancel, badge, badgeText, fmtDate,
    };
  },
  template: `
  <div>
    <div class="card">
      <div class="card-h"><div><h3>待办中心</h3>
        <div class="d">跨部门派活都在这里</div></div>
        <button class="btn ghost" @click="load">刷新</button></div>
      <div class="card-b tight">
        <div class="tabs">
          <div class="tab" :class="{on:tab==='mine'}" @click="switchTab('mine')">我的待办</div>
          <div class="tab" :class="{on:tab==='board'}" @click="switchTab('board')">部门看板</div>
          <div class="tab" :class="{on:tab==='done'}" @click="switchTab('done')">已完成</div>
        </div>

        <!-- 我的待办 -->
        <div v-if="tab!=='board'">
          <div v-if="!list.length" class="empty"><div class="ic">✓</div>
            <div class="t">{{ tab==='done' ? '还没有已完成的待办' : '没有待办，休息一下' }}</div></div>
          <div v-else class="tbl-wrap"><table class="tbl responsive">
            <thead><tr><th>事项</th><th>类型</th><th>部门</th><th>截止</th><th>状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="t in list" :key="t.id">
              <td data-label="事项"><div>{{ t.title }}</div>
                <div class="muted small" v-if="t.detail">{{ t.detail }}</div></td>
              <td data-label="类型"><span class="tag gray">{{ t.task_label }}</span></td>
              <td class="muted" data-label="部门">{{ t.dept_name || '—' }}</td>
              <td class="muted small" data-label="截止">{{ fmtDate(t.due_at) || '不限' }}</td>
              <td data-label="状态"><span v-if="badgeText(t)" class="tag" :class="badge(t)">{{ badgeText(t) }}</span>
                <span v-else class="muted small">—</span></td>
              <td data-label="操作"><div style="display:flex;gap:5px" v-if="tab==='mine'">
                <button class="btn sm" @click="openDone(t)">完成</button>
                <button class="btn sm ghost" @click="cancel(t)">取消</button>
              </div><span v-else class="muted small">{{ fmtDate(t.done_at) }}</span></td>
            </tr></tbody>
          </table></div>
        </div>

        <!-- 部门看板 -->
        <div v-else>
          <div v-if="!board || !board.total" class="empty"><div class="ic">✓</div>
            <div class="t">全公司没有未完成待办</div></div>
          <div v-else>
            <div style="padding:10px 14px;background:#FFF8E8;border-bottom:1px solid #F0E2C0">
              未完成 {{ board.total }} 项，其中 <b style="color:#C0392B">{{ board.overdue }}</b> 项已超期
            </div>
            <div v-for="g in board.groups" :key="g.dept" class="grp">
              <div class="grp-h">{{ g.dept }}
                <span class="tag gray">{{ g.items.length }}</span>
                <span class="tag red" v-if="g.overdue">{{ g.overdue }} 超期</span></div>
              <div class="grp-b">
                <div v-for="t in g.items" :key="t.id" class="task-chip"
                     :class="{late: t.is_overdue}">
                  <div class="tc-t">{{ t.title }}</div>
                  <div class="tc-m">{{ t.task_label }} · {{ t.assignee_name || '未指派' }}
                    <span v-if="t.due_at"> · {{ fmtDate(t.due_at) }}</span></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="modal" v-if="done" @click.self="done=null">
      <div class="modal-h"><div class="ttl">完成待办</div><button class="x-btn" @click="done=null">×</button></div>
      <div class="modal-b">
        <div style="margin-bottom:8px"><b>{{ done.title }}</b></div>
        <div class="muted small" style="margin-bottom:8px" v-if="done.detail">{{ done.detail }}</div>
        <textarea class="inp" rows="3" v-model="result" placeholder="填写处理结果"></textarea>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="done=null">取消</button>
        <button class="btn" @click="finish">标记完成</button></div>
    </div>
  </div>`
};
