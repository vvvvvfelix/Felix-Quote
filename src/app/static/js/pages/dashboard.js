/* ============================ 组件：工作台 ============================ */
const DashboardPage = {
  props: ['categories'],
  emits: ['nav'],
  setup(props, { emit }) {
    const d = ref(null), loading = ref(true);
    onMounted(async () => {
      try { d.value = await api.get('/api/dashboard'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    });
    const maxCat = computed(() => Math.max(1, ...((d.value && d.value.categories) || []).map(c => c.n)));
    const usdNumber = computed(() => (Number(d.value && d.value.total_amount) || 0)
      .toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    return { d, loading, maxCat, usdNumber, fmtMoney, emit };
  },
  template: `
  <div>
    <div class="card" style="background:linear-gradient(120deg,#fff,#FDF9F0);border-color:#EFE3C8">
      <div class="card-b flex-between" style="align-items:flex-start">
        <div>
          <div style="font-size:11.5px;color:#8B90A0;font-weight:600;letter-spacing:.5px">WELCOME BACK</div>
          <div style="font-size:19px;font-weight:800;margin-top:5px">Felix 外贸报价工作台</div>
          <div style="font-size:12px;color:#5A6070;margin-top:5px;line-height:1.7">
            本地作业 0 Token · 资料、模板、报价单全部存储于本地 SQLite<br>
            菲力克斯科技（广州）有限公司 — 商用烘焙与厨房设备出口报价
          </div>
        </div>
        <div class="btn-row">
          <button class="btn primary" @click="$emit('nav','builder')">＋ 新建报价单</button>
          <button class="btn" @click="$emit('nav','documents')">⇪ 导入资料</button>
        </div>
      </div>
    </div>

    <div v-if="d" class="stats">
      <div class="stat g"><div class="lb">产品总数</div><div class="vl">{{ d.products }}</div>
        <div class="sf">含自有产线 + 供应商</div></div>
      <div class="stat b"><div class="lb">客户数量</div><div class="vl">{{ d.customers }}</div>
        <div class="sf">可用于快速开单</div></div>
      <div class="stat k"><div class="lb">报价单数量</div><div class="vl">{{ d.quotations }}</div>
        <div class="sf">草稿 / 已发送</div></div>
      <div class="stat r"><div class="lb">累计报价金额</div><div class="vl" style="font-size:21px">\${{ usdNumber }}</div>
        <div class="sf">USD 累计（未拒单）</div></div>
      <div class="stat"><div class="lb">资料库文档</div><div class="vl">{{ d.documents }}</div>
        <div class="sf">PDF / TXT / XLSX</div></div>
    </div>

    <div v-if="loading" class="empty"><div class="ic">◌</div><div class="t">加载中…</div></div>

    <div v-else-if="d" class="split">
      <div class="card">
        <div class="card-h"><div><h3>产品系列分布</h3><div class="d">按分类统计的产品数量</div></div></div>
        <div class="card-b">
          <div v-for="c in d.categories" :key="c.code" style="margin-bottom:11px">
            <div class="flex-between" style="margin-bottom:4px">
              <span style="font-size:12.5px;font-weight:600">{{ c.name_cn }}</span>
              <span class="mono muted">{{ c.n }}</span>
            </div>
            <div style="height:7px;background:#F0F1F4;border-radius:4px;overflow:hidden">
              <div :style="{width:(c.n/maxCat*100)+'%',height:'100%',background:'linear-gradient(90deg,#B8860B,#D9A93B)',borderRadius:'4px'}"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-h"><div><h3>最近报价单</h3><div class="d">最近 8 条</div></div>
          <button class="btn sm" @click="$emit('nav','quotations')">查看全部</button></div>
        <div class="card-b tight">
          <div v-if="!d.recent.length" class="empty"><div class="ic">▧</div>
            <div class="t">还没有报价单</div><div>点击「新建报价单」开始第一单</div></div>
          <div class="tbl-wrap" v-else>
            <table class="tbl responsive">
              <thead><tr><th>单号</th><th>客户</th><th class="r">金额</th><th>状态</th><th>日期</th></tr></thead>
              <tbody><tr v-for="q in d.recent" :key="q.id">
                <td class="mono b" data-label="单号">{{ q.quote_no }}</td>
                <td class="ell" data-label="客户">{{ q.customer_name||'—' }}</td>
                <td class="r mono" data-label="金额">{{ fmtMoney(q.total_amount) }}</td>
                <td data-label="状态"><span class="tag" :class="{draft:'warn',sent:'ok',rejected:'err'}[q.status]||'blue'">
                  {{ {draft:'草稿',sent:'已发送',confirmed:'已确认',rejected:'已拒'}[q.status]||q.status }}</span></td>
                <td class="muted small" data-label="日期">{{ (q.created_at||'').slice(0,10) }}</td>
              </tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>`
};
