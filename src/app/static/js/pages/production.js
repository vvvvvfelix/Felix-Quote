/* 页面：生产看板 —— 跨部门共享的生产进度 */
const ProductionPage = {
  setup() {
    const data = ref(null), loading = ref(false);
    const FILTERS = [['', '全部'], ['pending', '待排产'], ['material', '备料中'],
                     ['producing', '生产中'], ['quality', '质检中'], ['finished', '已完工']];
    async function load() {
      loading.value = true;
      try { data.value = await api.get('/api/production-board'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    const fmtDate = v => (v || '').slice(0, 10);
    // 进入页面即加载 —— 漏掉 onMounted 会导致表格永远空白
    onMounted(load);

    return { data, loading, FILTERS, load, fmtDate };
  },
  template: `
  <div>
    <div class="stats" v-if="data">
      <div class="stat" v-for="f in FILTERS.slice(1)" :key="f[0]">
        <div class="lb">{{ f[1] }}</div><div class="vl">{{ data.summary[f[0]] || 0 }}</div>
        <div class="sf">明细行数</div></div>
    </div>
    <div class="card">
      <div class="card-h"><div><h3>生产看板</h3>
        <div class="d">在制与待产订单明细，销售也能看到进度</div></div>
        <button class="btn ghost" @click="load">刷新</button></div>
      <div class="card-b tight">
        <div v-if="!data || !data.items.length" class="empty"><div class="ic">◌</div>
          <div class="t">暂无在制订单</div><div>订单进入「已确认」后出现在这里</div></div>
        <div v-else class="tbl-wrap"><table class="tbl responsive">
          <thead><tr><th>订单号</th><th>客户</th><th>型号</th><th class="r">数量</th>
            <th>交期</th><th>状态</th><th>来源</th></tr></thead>
          <tbody><tr v-for="r in data.items" :key="r.item_id" :class="{rowlate:r.is_late}">
            <td class="mono" data-label="订单号">{{ r.order_no }}</td>
            <td data-label="客户">{{ r.customer_name || '—' }}</td>
            <td class="mono" data-label="型号">{{ r.model || r.sku }}</td>
            <td class="r" data-label="数量">{{ r.qty }}</td>
            <td data-label="交期">{{ fmtDate(r.delivery_date) }}
              <span class="tag red" v-if="r.is_late" style="margin-left:4px">已延期</span></td>
            <td data-label="状态"><span class="tag" :class="r.production_status==='finished'?'green':r.production_status==='producing'?'orange':'gray'">{{ r.status_label }}</span></td>
            <td data-label="来源"><span class="tag" :class="r.source==='out'?'orange':'gray'">{{ r.source==='out'?'外协':'自有' }}</span></td>
          </tr></tbody>
        </table></div>
      </div>
    </div>
  </div>`
};
