/* ============================ 组件：报价单管理 ============================ */
const QuotationsPage = {
  props: [],
  emits: ['edit', 'new', 'print', 'mail', 'preview'],
  setup(props, { emit }) {
    const list = ref([]), loading = ref(true);
    async function load() {
      loading.value = true;
      try { list.value = await api.get('/api/quotations'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function remove(q) {
      if (!confirm('删除报价单 ' + q.quote_no + '？此操作不可恢复。')) return;
      try { await api.del('/api/quotations/' + q.id); notify('已删除', 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function dl(q) {
      try { await api.download('/api/quotations/' + q.id + '/pdf', q.quote_no + '.pdf'); notify('PDF 已下载', 'ok'); }
      catch (e) { notify(e.message, 'err'); }
    }
    /* PDF 预览 —— 不下载到本地，直接在浏览器里看 */
    function preview(q) { emit('preview', q); }
    onMounted(load);
    return { list, loading, remove, dl, preview, fmtMoney, emit };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">共 {{ list.length }} 份报价单</span>
      <span class="grow"></span>
      <button class="btn primary" @click="$emit('new')">＋ 新建报价单</button>
    </div>
    <div class="card"><div class="card-b tight">
      <div v-if="!list.length && !loading" class="empty"><div class="ic">▧</div>
        <div class="t">还没有报价单</div><div>点击右上角新建</div></div>
      <div v-else class="tbl-wrap">
        <table class="tbl responsive">
          <thead><tr><th>单号</th><th>标题</th><th>客户</th><th>模板</th>
            <th class="r">金额</th><th>状态</th><th>创建时间</th><th class="c">操作</th></tr></thead>
          <tbody><tr v-for="q in list" :key="q.id">
            <td class="mono b" data-label="单号">{{ q.quote_no }}</td>
            <td class="ell" data-label="标题">{{ q.title||'—' }}</td>
            <td class="ell" data-label="客户">{{ q.customer_name||'—' }}</td>
            <td class="small mono muted" data-label="模板">{{ q.template_key }}</td>
            <td class="r mono b" data-label="金额" style="color:#B8860B">{{ fmtMoney(q.total_amount, q.currency) }}</td>
            <td data-label="状态"><span class="tag" :class="{draft:'warn',sent:'ok',confirmed:'blue',rejected:'err'}[q.status]||''">
              {{ {draft:'草稿',sent:'已发送',confirmed:'已确认',rejected:'已拒'}[q.status]||q.status }}</span></td>
            <td class="muted small" data-label="创建时间">{{ (q.created_at||'').slice(0,16) }}</td>
            <td class="c" data-label="操作" style="white-space:nowrap">
              <button class="btn sm" @click="$emit('edit', q.id)">编辑</button>
              <button class="btn sm" @click="preview(q)">预览</button>
              <button class="btn sm" @click="dl(q)">下载</button>
              <button class="btn sm" @click="$emit('print', q.id)">打印</button>
              <button class="btn sm" @click="$emit('mail', q)">发邮件</button>
              <button class="btn sm danger" @click="remove(q)">删除</button>
            </td>
          </tr></tbody>
        </table>
      </div>
    </div></div>
  </div>`
};
