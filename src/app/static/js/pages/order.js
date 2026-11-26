/* 页面：销售订单 —— 跨部门协作核心（技术确认交期、价格审批、生产推进） */
const OrderPage = {
  props: ['customers', 'users'],
  setup(props, { emit }) {
    const list = ref([]), total = ref(0), loading = ref(false);
    const q = reactive({ keyword: '', status: '', approval: '' });
    const detail = ref(null), stats = ref(null), making = ref(null), saving = ref(false);
    const STATUS = { draft: '草稿', confirming: '待确认', confirmed: '已确认', producing: '生产中', shipped: '已出货', completed: '已完成', canceled: '已取消' };
    const PROD = { pending: '待排产', material: '备料中', producing: '生产中', quality: '质检中', finished: '已完工' };
    const APPROVAL = { none: '无需审批', pending: '待审批', approved: '已通过', rejected: '已驳回' };

    async function load() {
      loading.value = true;
      try {
        const p = new URLSearchParams();
        Object.entries(q).forEach(([k, v]) => { if (v) p.append(k, v); });
        p.append('limit', '100');
        const r = await api.get('/api/orders?' + p.toString());
        list.value = r.items; total.value = r.total;
        stats.value = await api.get('/api/orders/stats');
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function open(id) {
      try { detail.value = await api.get('/api/orders/' + id); }
      catch (e) { notify(e.message, 'err'); }
    }
    function create() {
      making.value = { customer_id: '', currency: 'USD', incoterm: 'FOB Guangzhou', items: [] };
    }
    async function save() {
      const m = making.value;
      if (!m.customer_id) { notify('请选择客户', 'err'); return; }
      if (!m.items.length) { notify('请至少添加一行明细', 'err'); return; }
      saving.value = true;
      try {
        const r = await api.post('/api/orders', m);
        notify(`订单 ${r.order_no} 已创建` + (r.need_approval ? '（需审批）' : ''), 'ok');
        making.value = null; await load();
        if (r.need_approval) {
          if (confirm('该订单触发审批规则：\n' + r.approval_reasons.join('\n') + '\n\n是否现在去审批？')) {
            await open(r.id);
          }
        }
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    async function setStatus(id, status) {
      try { await api.post(`/api/orders/${id}/status`, { status }); notify('状态已更新', 'ok'); await open(id); await load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function approve(id, ok) {
      const comment = ok ? prompt('审批意见（可空）') : prompt('驳回原因') || '';
      try {
        await api.post(`/api/orders/${id}/approval`, { action: ok ? 'approve' : 'reject', comment });
        notify(ok ? '已审批通过' : '已驳回', 'ok'); await open(id); await load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function confirmDelivery(id, reject) {
      let date = '', remark = '';
      if (reject) {
        remark = prompt('为什么无法承诺该交期？') || '';
        if (!remark) return;
      } else {
        date = prompt('请填写可承诺的交期（YYYY-MM-DD）', new Date().toISOString().slice(0, 10));
        if (!date) return;
        remark = prompt('补充说明（可空）') || '';
      }
      try {
        await api.post(`/api/orders/${id}/confirm-delivery`, { delivery_date: date, remark, reject: !!reject });
        notify(reject ? '已驳回并通知销售' : '交期已确认', 'ok'); await open(id);
      } catch (e) { notify(e.message, 'err'); }
    }
    async function setProd(id, status) {
      try {
        const r = await api.post(`/api/orders/${id}/production`, { item_id: id, production_status: status });
        notify(r.all_done ? '全部完工' : '已更新', 'ok'); await open(detail.value.id);
      } catch (e) { notify(e.message, 'err'); }
    }
    async function fromQuote() {
      const qid = prompt('输入要转订单的报价单 id');
      if (!qid) return;
      try {
        const r = await api.post('/api/orders/from-quotation', { quotation_id: Number(qid) });
        notify(`已生成订单 ${r.order_no}`, 'ok');
        if (r.need_approval) notify('触发审批：' + r.approval_reasons.join('；'), 'warn');
        await load(); await open(r.id);
      } catch (e) { notify(e.message, 'err'); }
    }
    function addItem() { making.value.items.push({ qty: 1, unit_price: 0, source: 'own' }); }
    const fmtDate = v => (v || '').slice(0, 10);
    const can = p => (props.user && props.user.permissions || []).includes(p)
      || (props.user && props.user.permissions || []).includes('*');

    // 进入页面即加载 —— 漏掉 onMounted 会导致表格永远空白
    onMounted(load);

    return {
      list, total, loading, q, detail, stats, making, saving,
      STATUS, PROD, APPROVAL, fmtDate, can,
      load, open, create, save, setStatus, approve, confirmDelivery,
      setProd, fromQuote, addItem,
    };
  },
  template: `
  <div>
    <div class="stats" v-if="stats">
      <div class="stat k"><div class="lb">订单金额</div><div class="vl">{{ fmtMoney(stats.total_amount) }}</div>
        <div class="sf">不含草稿与已取消</div></div>
      <div class="stat g"><div class="lb">毛利</div><div class="vl">{{ fmtMoney(stats.gross_profit) }}</div>
        <div class="sf">毛利率 {{ stats.gross_rate }}%</div></div>
      <div class="stat r"><div class="lb">待审批</div><div class="vl">{{ stats.pending_approval }}</div>
        <div class="sf">超阈值需经理确认</div></div>
      <div class="stat b"><div class="lb">生产中</div><div class="vl">{{ stats.by_status.producing || 0 }}</div>
        <div class="sf">在制订单</div></div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>销售订单</h3><div class="d">共 {{ total }} 单</div></div>
        <div style="display:flex;gap:8px">
          <button class="btn ghost" @click="fromQuote">报价转订单</button>
          <button class="btn" @click="create">+ 新建订单</button>
        </div></div>
      <div class="filters">
        <input class="inp" placeholder="订单号/客户PO" v-model="q.keyword" @keyup.enter="load">
        <select class="inp" v-model="q.status" @change="load">
          <option value="">全部状态</option>
          <option v-for="(v,k) in STATUS" :value="k" :key="k">{{ v }}</option>
        </select>
        <select class="inp" v-model="q.approval" @change="load">
          <option value="">全部审批</option>
          <option v-for="(v,k) in APPROVAL" :value="k" :key="k">{{ v }}</option>
        </select>
        <button class="btn ghost" @click="load">查询</button>
      </div>
      <div class="card-b tight">
        <div v-if="!list.length && !loading" class="empty"><div class="ic">◌</div>
          <div class="t">还没有订单</div><div>可以从报价单一键转单，或直接新建</div></div>
        <div v-else class="tbl-wrap"><table class="tbl responsive">
          <thead><tr><th>订单号</th><th>客户</th><th>客户PO</th><th class="r">金额</th>
            <th>交期</th><th>负责人</th><th>审批</th><th>状态</th><th></th></tr></thead>
          <tbody>            <tr v-for="r in list" :key="r.id" class="row" @click="open(r.id)">
            <td class="mono" data-label="订单号">{{ r.order_no }}</td>
            <td data-label="客户">{{ r.customer_name || '—' }}</td>
            <td class="muted" data-label="客户PO">{{ r.po_number || '—' }}</td>
            <td class="r b" data-label="金额">{{ fmtMoney(r.total_amount) }}</td>
            <td data-label="交期">{{ fmtDate(r.delivery_date) || '待确认' }}</td>
            <td data-label="负责人">{{ r.owner_name || '—' }}</td>
            <td data-label="审批"><span class="tag" :class="r.approval_status==='approved'?'green':r.approval_status==='pending'?'orange':r.approval_status==='rejected'?'red':'gray'">{{ APPROVAL[r.approval_status] }}</span></td>
            <td data-label="状态"><span class="tag blue">{{ r.status_label }}</span></td>
            <td data-label="操作"><button class="btn sm ghost" @click.stop="open(r.id)">详情</button></td>
          </tr></tbody>
        </table></div>
      </div>
    </div>

    <!-- 详情 -->
    <div class="modal wide" v-if="detail" @click.self="detail=null">
      <div class="modal-h"><div class="ttl">{{ detail.order_no }} · {{ detail.customer_company }}</div>
        <button class="x-btn" @click="detail=null">×</button></div>
      <div class="modal-b">
        <div class="grid4">
          <div><label>金额</label><div class="b">{{ fmtMoney(detail.total_amount) }} {{ detail.currency }}</div></div>
          <div><label>成本</label><div>{{ fmtMoney(detail.cost_amount) }}</div></div>
          <div><label>毛利</label><div>{{ fmtMoney(detail.gross_profit || 0) }}
            <span class="muted small">({{ detail.gross_rate || 0 }}%)</span></div></div>
          <div><label>交期</label><div>{{ fmtDate(detail.delivery_date) || '待确认' }}</div></div>
          <div><label>客户PO</label><div>{{ detail.po_number || '—' }}</div></div>
          <div><label>贸易条款</label><div>{{ detail.incoterm || '—' }}</div></div>
          <div><label>付款方式</label><div>{{ detail.payment_terms || '—' }}</div></div>
          <div><label>审批</label><div><span class="tag" :class="detail.approval_status==='approved'?'green':'orange'">{{ APPROVAL[detail.approval_status] }}</span></div></div>
        </div>

        <div style="margin:14px 0" v-if="detail.approval_status==='pending' && can('quotations.approve')">
          <div class="warn-box">该订单待审批。确认通过后系统会自动派发「技术确认交期」任务。</div>
          <div style="display:flex;gap:8px;margin-top:10px">
            <button class="btn" @click="approve(detail.id, true)">审批通过</button>
            <button class="btn danger" @click="approve(detail.id, false)">驳回</button>
          </div>
        </div>

        <div style="margin:14px 0" v-if="detail.approval_status==='approved' && detail.status!=='completed'">
          <button class="btn ghost" @click="confirmDelivery(detail.id, false)">确认交期（技术/生产）</button>
          <button class="btn ghost danger" @click="confirmDelivery(detail.id, true)">驳回交期</button>
        </div>

        <h4>订单明细</h4>
        <table class="tbl responsive"><thead><tr><th>型号</th><th>品名</th><th class="r">数量</th><th class="r">单价</th>
          <th class="r">金额</th><th>来源</th><th>生产进度</th></tr></thead>
          <tbody>            <tr v-for="i in detail.items" :key="i.id">
            <td class="mono" data-label="型号">{{ i.model || i.sku }}</td>
            <td data-label="品名">{{ i.name_en || '—' }}</td>
            <td class="r" data-label="数量">{{ i.qty }}</td>
            <td class="r" data-label="单价">{{ fmtMoney(i.unit_price) }}</td>
            <td class="r b" data-label="金额">{{ fmtMoney(i.amount) }}</td>
            <td data-label="来源"><span class="tag" :class="i.source==='out'?'orange':'gray'">{{ i.source==='out'?'外协':'自有' }}</span></td>
            <td data-label="生产进度">
              <select v-if="can('orders.production')" class="inp sm" style="width:100px"
                      :value="i.production_status" @change="setProd(i.id, $event.target.value)">
                <option v-for="(v,k) in PROD" :value="k" :key="k">{{ v }}</option>
              </select>
              <span v-else class="tag gray">{{ PROD[i.production_status] }}</span>
            </td>
          </tr></tbody>
        </table>

        <div class="grid3" style="margin-top:14px">
          <div><label>采购单</label><div v-if="!detail.purchase_orders.length" class="muted">无</div>
            <div v-for="p in detail.purchase_orders" :key="p.id" class="mono small">{{ p.po_no }}</div></div>
          <div><label>出货单</label><div v-if="!detail.shipments.length" class="muted">无</div>
            <div v-for="s in detail.shipments" :key="s.id" class="mono small">{{ s.shipment_no }}</div></div>
          <div><label>应收</label><div v-if="!detail.receivable.length" class="muted">无</div>
            <div v-for="r in detail.receivable" :key="r.id" class="mono small">
              {{ r.receivable_no }} {{ fmtMoney(r.received_amount) }}/{{ fmtMoney(r.amount) }}</div></div>
        </div>

        <h4 style="margin:14px 0 6px">流转记录</h4>
        <div class="tl">
          <div v-for="a in detail.timeline" :key="a.id" class="tl-i">
            <div class="tl-t">{{ a.created_at }}</div>
            <div class="tl-c"><b>{{ a.username || '系统' }}</b> {{ a.content || a.action }}</div>
          </div>
        </div>
      </div>
      <div class="modal-f">
        <select class="inp" style="width:auto" @change="setStatus(detail.id, $event.target.value); $event.target.value=''">
          <option value="">推进状态…</option>
          <option v-for="s in detail.next_states" :value="s.value" :key="s.value">{{ s.label }}</option>
        </select>
        <span style="flex:1"></span>
        <button class="btn" @click="detail=null">关闭</button>
      </div>
    </div>

    <!-- 新建 -->
    <div class="modal wide" v-if="making" @click.self="making=null">
      <div class="modal-h"><div class="ttl">新建订单</div><button class="x-btn" @click="making=null">×</button></div>
      <div class="modal-b">
        <div class="grid2">
          <div><label>客户</label><select class="inp" v-model="making.customer_id">
            <option value="">请选择</option>
            <option v-for="c in customers" :value="c.id" :key="c.id">{{ c.company_en }}</option>
          </select></div>
          <div><label>客户PO 号</label><input class="inp" v-model="making.po_number"></div>
          <div><label>贸易条款</label><input class="inp" v-model="making.incoterm"></div>
          <div><label>交期</label><input class="inp" type="date" v-model="making.delivery_date"></div>
        </div>
        <h4 style="margin:16px 0 8px">明细 <button class="btn sm ghost" @click="addItem">+ 添加</button></h4>
        <table class="tbl responsive"><thead><tr><th>型号</th><th>品名(英)</th><th class="r">数量</th>
          <th class="r">单价</th><th>来源</th><th></th></tr></thead>
          <tbody>            <tr v-for="(i,ix) in making.items" :key="ix">
            <td data-label="型号"><input class="inp sm" v-model="i.model"></td>
            <td data-label="品名(英)"><input class="inp sm" v-model="i.name_en"></td>
            <td class="r" data-label="数量"><input class="inp sm" style="width:70px" type="number" v-model="i.qty"></td>
            <td class="r" data-label="单价"><input class="inp sm" style="width:90px" type="number" v-model="i.unit_price"></td>
            <td data-label="来源"><select class="inp sm" style="width:80px" v-model="i.source">
              <option value="own">自有</option><option value="out">外协</option></select></td>
            <td data-label="操作"><button class="x-btn" @click="making.items.splice(ix,1)">×</button></td>
          </tr></tbody></table>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="making=null">取消</button>
        <button class="btn" @click="save" :disabled="saving">{{ saving ? '提交中…' : '创建' }}</button></div>
    </div>
  </div>`
};
