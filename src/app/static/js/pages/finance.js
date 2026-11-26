/* 页面：财务应收 —— 只有 finance.view 的人能看到 */
const FinancePage = {
  setup() {
    const list = ref([]), total = ref(0), summary = ref(null), loading = ref(false);
    const q = reactive({ keyword: '', status: '' });
    const detail = ref(null), paying = ref(null), payForm = ref(null);
    const STATUS = { open: '未收款', partial: '部分收款', settled: '已结清', overdue: '已逾期' };
    const METHOD = { 'T/T': '电汇', 'L/C': '信用证', 'D/P': '付款交单', 'D/A': '承兑交单', P: '即期支票', cash: '现金' };

    async function load() {
      loading.value = true;
      try {
        const p = new URLSearchParams();
        Object.entries(q).forEach(([k, v]) => { if (v) p.append(k, v); });
        p.append('limit', '100');
        const r = await api.get('/api/finance/receivables?' + p.toString());
        list.value = r.items; total.value = r.total;
        summary.value = await api.get('/api/finance/receivables/summary');
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function open(id) {
      try { detail.value = await api.get('/api/finance/receivables/' + id); }
      catch (e) { notify(e.message, 'err'); }
    }
    function pay(r) {
      paying.value = r;
      payForm.value = { amount: r.outstanding, method: 'T/T', received_at: new Date().toISOString().slice(0, 10), remark: '' };
    }
    async function submitPay() {
      try {
        const r = await api.post(`/api/finance/receivables/${paying.value.id}/payments`, payForm.value);
        notify(r.status === 'settled' ? '已结清' : `已收，未收 ${r.outstanding}`, 'ok');
        paying.value = null; await load();
        if (detail.value && detail.value.id === r.id) await open(r.id);
      } catch (e) { notify(e.message, 'err'); }
    }
    const fmtDate = v => (v || '').slice(0, 10);
    // 进入页面即加载 —— 漏掉 onMounted 会导致表格永远空白
    onMounted(load);

    return { list, total, summary, loading, q, detail, paying, payForm,
             STATUS, METHOD, load, open, pay, submitPay, fmtDate };
  },
  template: `
  <div>
    <div class="stats" v-if="summary">
      <div class="stat k" v-for="(c,cur) in summary.by_currency" :key="cur">
        <div class="lb">{{ cur }} 应收</div><div class="vl">{{ fmtMoney(c.outstanding) }}</div>
        <div class="sf">已收 {{ fmtMoney(c.received) }} / 共 {{ fmtMoney(c.amount) }}</div></div>
      <div class="stat r" v-if="!Object.keys(summary.by_currency).length">
        <div class="lb">暂无应收</div><div class="vl">0</div><div class="sf">订单出货后生成</div></div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>应收台账</h3><div class="d">共 {{ total }} 条</div></div>
        <button class="btn ghost" @click="load">刷新</button></div>
      <div class="filters">
        <input class="inp" placeholder="单号/发票号/客户" v-model="q.keyword" @keyup.enter="load">
        <select class="inp" v-model="q.status" @change="load">
          <option value="">全部状态</option>
          <option v-for="(v,k) in STATUS" :value="k" :key="k">{{ v }}</option>
        </select>
        <button class="btn ghost" @click="load">查询</button>
      </div>
      <div class="card-b tight">
        <div v-if="!list.length && !loading" class="empty"><div class="ic">◌</div>
          <div class="t">暂无应收记录</div><div>订单出货后由财务建立应收</div></div>
        <div v-else class="tbl-wrap"><table class="tbl responsive">
          <thead><tr><th>应收号</th><th>客户</th><th class="r">金额</th><th class="r">已收</th>
            <th class="r">未收</th><th>到期日</th><th>状态</th><th></th></tr></thead>
          <tbody><tr v-for="r in list" :key="r.id" class="row" @click="open(r.id)">
            <td class="mono" data-label="应收号">{{ r.receivable_no }}</td>
            <td data-label="客户">{{ r.customer_name || '—' }}</td>
            <td class="r" data-label="金额">{{ fmtMoney(r.amount) }}</td>
            <td class="r" data-label="已收">{{ fmtMoney(r.received_amount) }}</td>
            <td class="r b" data-label="未收">{{ fmtMoney(r.outstanding) }}</td>
            <td data-label="到期日">{{ fmtDate(r.due_date) }}
              <span class="tag red" v-if="r.is_overdue" style="margin-left:4px">逾期{{ r.days_overdue }}天</span></td>
            <td data-label="状态"><span class="tag" :class="r.status==='settled'?'green':r.is_overdue?'red':'orange'">{{ r.status_label }}</span></td>
            <td data-label="操作"><button class="btn sm" v-if="r.status!=='settled'" @click.stop="pay(r)">登记收款</button></td>
          </tr></tbody>
        </table></div>
      </div>
    </div>

    <div class="modal wide" v-if="detail" @click.self="detail=null">
      <div class="modal-h"><div class="ttl">{{ detail.receivable_no }} · {{ detail.customer_name }}</div>
        <button class="x-btn" @click="detail=null">×</button></div>
      <div class="modal-b">
        <div class="grid4">
          <div><label>应收金额</label><div class="b">{{ fmtMoney(detail.amount) }} {{ detail.currency }}</div></div>
          <div><label>已收</label><div>{{ fmtMoney(detail.received_amount) }}</div></div>
          <div><label>未收</label><div class="b">{{ fmtMoney(detail.outstanding) }}</div></div>
          <div><label>发票号</label><div>{{ detail.invoice_no || '—' }}</div></div>
        </div>
        <h4 style="margin:14px 0 6px">收款记录</h4>
        <table class="tbl responsive"><thead><tr><th>日期</th><th class="r">金额</th><th>方式</th><th>备注</th></tr></thead>
          <tbody><tr v-for="p in detail.payments" :key="p.id">
            <td data-label="日期">{{ fmtDate(p.received_at) }}</td><td class="r b" data-label="金额">{{ fmtMoney(p.amount) }}</td>
            <td data-label="方式">{{ METHOD[p.method] || p.method }}</td><td class="muted" data-label="备注">{{ p.remark || '' }}</td>
          </tr><tr v-if="!detail.payments.length"><td colspan="4" class="muted" data-label="">尚无收款</td></tr></tbody></table>
      </div>
      <div class="modal-f">
        <button class="btn" v-if="detail.status!=='settled'" @click="pay(detail)">登记收款</button>
        <span style="flex:1"></span><button class="btn ghost" @click="detail=null">关闭</button>
      </div>
    </div>

    <div class="modal" v-if="paying" @click.self="paying=null">
      <div class="modal-h"><div class="ttl">登记收款</div><button class="x-btn" @click="paying=null">×</button></div>
      <div class="modal-b">
        <div class="muted small" style="margin-bottom:10px">
          {{ paying.receivable_no }} · 未收 {{ fmtMoney(paying.outstanding) }} {{ paying.currency }}</div>
        <div class="grid2">
          <div><label>收款金额</label><input class="inp" type="number" step="0.01" v-model.number="payForm.amount"></div>
          <div><label>收款日期</label><input class="inp" type="date" v-model="payForm.received_at"></div>
          <div><label>收款方式</label><select class="inp" v-model="payForm.method">
            <option v-for="(v,k) in METHOD" :value="k" :key="k">{{ v }}</option></select></div>
          <div><label>备注</label><input class="inp" v-model="payForm.remark"></div>
        </div>
      </div>
      <div class="modal-f"><span style="flex:1"></span>
        <button class="btn ghost" @click="paying=null">取消</button>
        <button class="btn" @click="submitPay">确认收款</button></div>
    </div>
  </div>`
};
