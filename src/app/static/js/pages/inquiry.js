/* 页面：询盘管理 —— 业务入口，跨部门协作的起点 */
const InquiryPage = {
  props: ['customers', 'users'],
  setup(props, { emit }) {
    const list = ref([]), total = ref(0), loading = ref(false);
    const q = reactive({ keyword: '', status: '', priority: '', mine: 0 });
    const detail = ref(null), tab = ref('list');
    const editing = ref(null), saving = ref(false);
    const stats = ref(null), tasks = ref([]), doneTask = ref(null), taskResult = ref('');
    const STATUS = { new: '新询盘', quoted: '已报价', negotiating: '洽谈中', won: '已成交', lost: '已流失', archived: '已归档' };
    const PRIORITY = { urgent: '紧急', high: '高', normal: '普通', low: '低' };
    const SOURCE = { alibaba: '阿里国际站', email: '邮件', exhibition: '展会', website: '官网', referral: '老客户介绍', phone: '电话', other: '其他' };
    const emptyForm = () => ({
      customer_id: '', customer_name: '', source: 'email', contact_name: '', contact_email: '',
      country: '', subject: '', currency: 'USD', priority: 'normal', remark: '', items: [] });

    async function load() {
      loading.value = true;
      try {
        const p = new URLSearchParams();
        Object.entries(q).forEach(([k, v]) => { if (v) p.append(k, v); });
        p.append('limit', '100');
        const r = await api.get('/api/inquiries?' + p.toString());
        list.value = r.items; total.value = r.total;
        stats.value = await api.get('/api/inquiries/stats');
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function open(id) {
      try {
        detail.value = await api.get('/api/inquiries/' + id);
        tab.value = 'detail';
      } catch (e) { notify(e.message, 'err'); }
    }
    function create() { editing.value = emptyForm(); }
    async function save() {
      if (!editing.value.subject && !editing.value.customer_name) { notify('请至少填写客户或主题', 'err'); return; }
      saving.value = true;
      try {
        if (editing.value.id) {
          await api.put('/api/inquiries/' + editing.value.id, editing.value);
          notify('已保存', 'ok');
        } else {
          const r = await api.post('/api/inquiries', editing.value);
          notify('询盘 ' + r.inquiry_no + ' 已登记', 'ok');
        }
        editing.value = null; await load();
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    function edit(d) {
      editing.value = { ...d, customer_id: d.customer_id || '', items: (d.items || []).map(x => ({ ...x })) };
    }
    async function setStatus(id, status, lost_reason) {
      try {
        await api.post(`/api/inquiries/${id}/status`, { status, lost_reason });
        notify('状态已更新', 'ok');
        if (detail.value && detail.value.id === id) await open(id);
        await load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function remove(id) {
      if (!confirm('确定删除这条询盘？')) return;
      try { await api.del('/api/inquiries/' + id); notify('已删除', 'ok'); editing.value = null; await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    function addItem() { editing.value.items.push({ description: '', qty: 1, unit: 'set' }); }
    async function addTask() {
      const title = prompt('待办标题，例如「确认技术参数」');
      if (!title) return;
      const assignee = prompt('指派给谁？输入用户名，留空则按部门派');
      try {
        let uid = null;
        if (assignee) {
          const us = (props.users || []);
          uid = (us.find(u => u.username === assignee) || {}).id;
          if (!uid) { notify('找不到用户 ' + assignee, 'err'); return; }
        }
        await api.post(`/api/inquiries/${detail.value.id}/tasks`, { title, assignee_id: uid });
        notify('待办已派发', 'ok');
      } catch (e) { notify(e.message, 'err'); }
    }
    async function openTask(t) {
      doneTask.value = t; taskResult.value = '';
    }
    async function finishTask() {
      try {
        await api.post(`/api/tasks/${doneTask.value.id}/done`, { result: taskResult.value });
        notify('已完成', 'ok'); doneTask.value = null;
        if (detail.value) await open(detail.value.id);
        emit('nav', 'tasks');
      } catch (e) { notify(e.message, 'err'); }
    }
    const fmtDate = v => (v || '').slice(0, 10);

    // 进入页面即加载 —— 漏掉 onMounted 会导致表格永远空白
    onMounted(load);

    return {
      list, total, loading, q, detail, tab, editing, saving, stats,
      doneTask, taskResult, STATUS, PRIORITY, SOURCE, fmtDate,
      load, open, create, save, edit, setStatus, remove, addItem,
      addTask, openTask, finishTask,
    };
  },
  template: `
  <div>
    <div class="stats" v-if="stats">
      <div class="stat k"><div class="lb">总询盘</div><div class="vl">{{ stats.total }}</div>
        <div class="sf">待处理 {{ stats.pending }}</div></div>
      <div class="stat g"><div class="lb">已报价</div><div class="vl">{{ stats.quoted }}</div>
        <div class="sf">进入洽谈或成交</div></div>
      <div class="stat b"><div class="lb">已成交</div><div class="vl">{{ stats.won }}</div>
        <div class="sf">赢单率 {{ stats.win_rate }}%</div></div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>询盘列表</h3><div class="d">共 {{ total }} 条</div></div>
        <button class="btn" @click="create">+ 登记询盘</button></div>
      <div class="filters">
        <input class="inp" placeholder="单号/客户/国家" v-model="q.keyword" @keyup.enter="load">
        <select class="inp" v-model="q.status" @change="load">
          <option value="">全部状态</option>
          <option v-for="(v,k) in STATUS" :value="k" :key="k">{{ v }}</option>
        </select>
        <select class="inp" v-model="q.priority" @change="load">
          <option value="">全部优先级</option>
          <option v-for="(v,k) in PRIORITY" :value="k" :key="k">{{ v }}</option>
        </select>
        <label class="chk"><input type="checkbox" v-model="q.mine" @change="load"> 只看我的</label>
        <button class="btn ghost" @click="load">查询</button>
      </div>
      <div class="card-b tight">
        <div v-if="!list.length && !loading" class="empty"><div class="ic">◌</div>
          <div class="t">还没有询盘</div><div>客户来信、展会名片、阿里询盘都可以登记进来</div></div>
        <div v-else class="tbl-wrap"><table class="tbl responsive">
          <thead><tr><th>询盘号</th><th>客户</th><th>主题</th><th>来源</th>
            <th class="r">优先级</th><th>负责人</th><th>状态</th><th>更新</th><th></th></tr></thead>
          <tbody><tr v-for="r in list" :key="r.id" @click="open(r.id)" class="row">
            <td class="mono" data-label="询盘号">{{ r.inquiry_no }}</td>
            <td data-label="客户">{{ r.customer_name || r.customer_name_db || '—' }}</td>
            <td data-label="主题">{{ r.subject || '—' }}</td>
            <td data-label="来源">{{ r.source_label }}</td>
            <td class="r" data-label="优先级"><span class="tag" :class="r.priority==='urgent'?'red':r.priority==='high'?'orange':'gray'">{{ r.priority_label }}</span></td>
            <td data-label="负责人">{{ r.owner_name || '—' }}</td>
            <td data-label="状态"><span class="tag" :class="r.status==='won'?'green':r.status==='lost'?'red':'blue'">{{ r.status_label }}</span></td>
            <td class="muted small" data-label="更新">{{ fmtDate(r.updated_at) }}</td>
            <td data-label="操作"><button class="btn sm ghost" @click.stop="edit(r)">编辑</button></td>
          </tr></tbody>
        </table></div>
      </div>
    </div>

    <!-- 详情 -->
    <div class="modal wide" v-if="detail" @click.self="detail=null">
      <div class="modal-h"><div class="ttl">{{ detail.inquiry_no }} · {{ detail.subject || '未命名询盘' }}</div>
        <button class="x-btn" @click="detail=null">×</button></div>
      <div class="modal-b">
        <div class="grid4">
          <div><label>客户</label><div>{{ detail.customer_name || detail.customer_company || '—' }}</div></div>
          <div><label>国家</label><div>{{ detail.country || '—' }}</div></div>
          <div><label>联系人</label><div>{{ detail.contact_name || '—' }}</div></div>
          <div><label>来源</label><div>{{ detail.source_label }}</div></div>
          <div><label>负责人</label><div>{{ detail.owner_name || '—' }}（{{ detail.dept_name || '—' }}）</div></div>
          <div><label>优先级</label><div>{{ detail.priority_label }}</div></div>
          <div><label>收到日期</label><div>{{ fmtDate(detail.received_at) }}</div></div>
          <div><label>状态</label><div><span class="tag blue">{{ detail.status_label }}</span></div></div>
        </div>

        <h4 style="margin:16px 0 8px">询价明细</h4>
        <table class="tbl responsive"><thead><tr><th>产品描述</th><th>品类</th><th class="r">数量</th><th>目标价</th><th>备注</th></tr></thead>
          <tbody><tr v-for="(i,ix) in detail.items" :key="ix">
            <td data-label="产品描述">{{ i.description || '—' }}</td><td class="muted" data-label="品类">{{ i.category_code || '—' }}</td>
            <td class="r" data-label="数量">{{ i.qty }} {{ i.unit }}</td>
            <td class="r" data-label="目标价">{{ i.target_price ? fmtMoney(i.target_price) : '—' }}</td>
            <td class="muted" data-label="备注">{{ i.remark || '' }}</td>
          </tr></tbody></table>

        <h4 style="margin:16px 0 8px">流转记录</h4>
        <div class="tl">
          <div v-for="a in detail.timeline" :key="a.id" class="tl-i">
            <div class="tl-t">{{ a.created_at }}</div>
            <div class="tl-c"><b>{{ a.username || '系统' }}</b> {{ a.content || a.action }}</div>
          </div>
          <div v-if="!detail.timeline.length" class="muted small">暂无记录</div>
        </div>
      </div>
      <div class="modal-f">
        <select class="inp" style="width:auto" @change="setStatus(detail.id, $event.target.value); $event.target.value=''">
          <option value="">变更状态…</option>
          <option v-for="s in detail.next_states" :value="s.value" :key="s.value">{{ s.label }}</option>
        </select>
        <button class="btn ghost" @click="addTask">派发待办</button>
        <button class="btn" @click="detail=null">关闭</button>
      </div>
    </div>

    <!-- 编辑 -->
    <div class="modal wide" v-if="editing" @click.self="editing=null">
      <div class="modal-h"><div class="ttl">{{ editing.id ? '编辑询盘' : '登记新询盘' }}</div>
        <button class="x-btn" @click="editing=null">×</button></div>
      <div class="modal-b">
        <div class="grid2">
          <div><label>客户名称</label><input class="inp" v-model="editing.customer_name"></div>
          <div><label>联系人</label><input class="inp" v-model="editing.contact_name"></div>
          <div><label>邮箱</label><input class="inp" v-model="editing.contact_email"></div>
          <div><label>国家</label><input class="inp" v-model="editing.country"></div>
          <div><label>来源</label><select class="inp" v-model="editing.source">
            <option v-for="(v,k) in SOURCE" :value="k" :key="k">{{ v }}</option></select></div>
          <div><label>优先级</label><select class="inp" v-model="editing.priority">
            <option v-for="(v,k) in PRIORITY" :value="k" :key="k">{{ v }}</option></select></div>
        </div>
        <div style="margin-top:10px"><label>主题</label>
          <input class="inp" v-model="editing.subject" placeholder="例如：询问六层燃气层炉 FOB 价格"></div>
        <div style="margin-top:10px"><label>备注</label><textarea class="inp" rows="2" v-model="editing.remark"></textarea></div>

        <h4 style="margin:16px 0 8px">询价明细
          <button class="btn sm ghost" @click="addItem">+ 添加一行</button></h4>
        <table class="tbl responsive"><thead><tr><th>产品描述</th><th class="r">数量</th><th>单位</th><th class="r">目标价</th><th></th></tr></thead>
          <tbody><tr v-for="(i,ix) in editing.items" :key="ix">
            <td data-label="产品描述"><input class="inp sm" v-model="i.description"></td>
            <td class="r" data-label="数量"><input class="inp sm" style="width:70px" type="number" v-model="i.qty"></td>
            <td data-label="单位"><input class="inp sm" style="width:60px" v-model="i.unit"></td>
            <td class="r" data-label="目标价"><input class="inp sm" style="width:90px" type="number" v-model="i.target_price"></td>
            <td data-label="操作"><button class="x-btn" @click="editing.items.splice(ix,1)">×</button></td>
          </tr></tbody></table>
      </div>
      <div class="modal-f">
        <button class="btn ghost" @click="remove(editing.id)" v-if="editing.id">删除</button>
        <span style="flex:1"></span>
        <button class="btn ghost" @click="editing=null">取消</button>
        <button class="btn" @click="save" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>

    <!-- 完成任务 -->
    <div class="modal" v-if="doneTask" @click.self="doneTask=null">
      <div class="modal-h"><div class="ttl">完成待办</div><button class="x-btn" @click="doneTask=null">×</button></div>
      <div class="modal-b">
        <div style="margin-bottom:8px"><b>{{ doneTask.title }}</b></div>
        <textarea class="inp" rows="3" v-model="taskResult" placeholder="填写处理结果，会记入单据流转记录"></textarea>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="doneTask=null">取消</button>
        <button class="btn" @click="finishTask">标记完成</button></div>
    </div>
  </div>`
};
