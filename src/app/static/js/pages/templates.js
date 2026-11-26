const TemplatesPage = {
  props: [], emits: ['changed'],
  setup(props, { emit }) {
    const list = ref([]), edit = ref(null), saving = ref(false), loading = ref(true);
    const itemFields = ['model', 'sku', 'name_cn', 'name_en', 'spec_en', 'material',
      'dimension', 'net_weight', 'gross_weight', 'carton', 'qty', 'price', 'amount', 'certs'];
    // 明细行变量名（供模板提示展示），格式：{{ it.xxx }}
    const itemTokens = itemFields.map(f => '{{ it.' + f + ' }}');
    // 变量速查面板默认收起——列模板时不需要看它
    const showVars = ref(false);
    // 编辑弹窗里可点选的常用变量（高频，先列出来）
    const quickTokens = ['{{ quote.quote_no }}', '{{ quote.date }}', '{{ quote.valid_until }}',
      '{{ customer.company_en }}', '{{ customer.contact }}', '{{ company.name_en }}',
      '{{ totals.subtotal }}', '{{ totals.total }}'];
    const bodyRef = ref(null);
    /* 把变量插到 textarea 光标处。浏览器里 textarea 的 selectionStart
       只能读不能写（除非用 setRangeText），setRangeText 兼容性最好。 */
    function insertToken(tok) {
      const ta = bodyRef.value;
      if (!ta || !edit.value) return;
      const s = ta.selectionStart || 0, e = ta.selectionEnd || 0;
      const cur = edit.value.body_html || '';
      edit.value.body_html = cur.slice(0, s) + tok + cur.slice(e);
      nextTick(() => {
        ta.focus();
        const pos = s + tok.length;
        ta.setSelectionRange(pos, pos);
      });
    }
    async function load() {
      loading.value = true;
      try { list.value = await api.get('/api/templates'); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    async function open(t) {
      const r = await api.get('/api/templates/' + t.id);
      edit.value = { ...r };
    }
    async function save() {
      saving.value = true;
      try {
        await api.put('/api/templates/' + edit.value.id, edit.value);
        notify('模板已保存', 'ok'); edit.value = null; load(); emit('changed');
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    async function remove(t) {
      if (t.is_builtin) return notify('内置模板不可删除，可复制后新建', 'warn');
      if (!confirm('删除模板「' + t.name + '」？')) return;
      try { await api.del('/api/templates/' + t.id); notify('已删除', 'ok'); load(); emit('changed'); }
      catch (e) { notify(e.message, 'err'); }
    }
    function reset() {
      edit.value = {
        key: 'custom_' + Date.now().toString(36), name: '自定义模板', lang: 'en',
        body_html: '<!DOCTYPE html><html><head><meta charset="utf-8"><style>{{css}}</style></head><body>\n' +
          '<h1>{{quote.quote_no}}</h1>\n<p>TO: {{customer.company_en}}</p>\n<table>{{items}}</table>\n</body></html>',
        css: 'body{font-family:Arial,sans-serif;font-size:12px}'
      };
    }
    onMounted(load);
    return { list, edit, saving, loading, open, save, remove, reset, itemFields, itemTokens,
             showVars, quickTokens, bodyRef, insertToken, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small" style="flex:0 0 auto">共 {{ list.length }} 套模板</span>
      <span class="grow"></span>
      <button class="btn" @click="reset">＋ 新建模板</button>
    </div>
    <div class="var-fold">
      <button class="var-fold-h" @click="showVars=!showVars">
        <i class="ic">{{ showVars ? '▾' : '▸' }}</i>
        <span>Jinja2 可用变量速查</span>
        <span class="muted small">{{ showVars ? '（点击收起）' : '（编辑模板时需要，点击展开）' }}</span>
      </button>
      <div v-if="showVars" class="var-hint">
      <span class="grp"><i>报价单</i><code v-pre>{{ quote.quote_no }}</code><code v-pre>{{ quote.date }}</code><code v-pre>{{ quote.valid_until }}</code><code v-pre>{{ quote.currency }}</code><code v-pre>{{ quote.incoterm }}</code><code v-pre>{{ quote.port }}</code><code v-pre>{{ quote.payment_terms }}</code><code v-pre>{{ quote.delivery }}</code><code v-pre>{{ quote.valid_days }}</code><code v-pre>{{ quote.remark }}</code></span>
      <span class="grp"><i>客户与公司</i><code v-pre>{{ customer.company_en }}</code><code v-pre>{{ customer.contact }}</code><code v-pre>{{ customer.country }}</code><code v-pre>{{ customer.address }}</code><code v-pre>{{ company.name_en }}</code><code v-pre>{{ company.name_cn }}</code><code v-pre>{{ company.brand }}</code><code v-pre>{{ company.phone }}</code><code v-pre>{{ company.website }}</code></span>
      <span class="grp"><i>明细行</i><code v-for="f in itemTokens" :key="f">{{ f }}</code></span>
      <span class="grp"><i>合计</i><code v-pre>{{ totals.subtotal }}</code><code v-pre>{{ totals.freight }}</code><code v-pre>{{ totals.insurance }}</code><code v-pre>{{ totals.discount }}</code><code v-pre>{{ totals.total }}</code><code v-pre>{{ css }}</code></span>
      </div>
    </div>
    <div class="card"><div class="card-b tight">
      <div v-if="!list.length && !loading" class="empty"><div class="ic">⌘</div><div class="t">暂无模板</div></div>
      <div v-else class="tbl-wrap">
        <table class="tbl responsive">
          <thead><tr><th>模板键</th><th>名称</th><th>语言</th><th>类型</th>
            <th class="r">HTML 大小</th><th>创建时间</th><th class="c">操作</th></tr></thead>
          <tbody><tr v-for="t in list" :key="t.id">
            <td class="mono" data-label="模板键">{{ t.key }}</td>
            <td class="b" data-label="名称">{{ t.name }}</td>
            <td data-label="语言">{{ t.lang==='cn'?'中文':'英文' }}</td>
            <td data-label="类型"><span class="tag" :class="t.is_builtin?'gold':'blue'">{{ t.is_builtin?'内置':'自定义' }}</span></td>
            <td class="r mono muted" data-label="HTML 大小">{{ (t.size/1024).toFixed(1) }} KB</td>
            <td class="muted small" data-label="创建时间">{{ (t.created_at||'').slice(0,10) }}</td>
            <td class="c" data-label="操作" style="white-space:nowrap">
              <button class="btn sm" @click="open(t)">编辑</button>
              <button class="btn sm danger" @click="remove(t)">删除</button></td>
          </tr></tbody>
        </table>
      </div>
    </div></div>

    <div v-if="edit" class="mask" @click.self="edit=null">
      <div class="modal wide">
        <div class="modal-h"><h3>编辑模板 — {{ edit.name }}</h3><span class="x-btn" @click="edit=null">×</span></div>
        <div class="modal-b">
          <div class="grid3" style="margin-bottom:13px">
            <div class="field" style="margin:0"><label>模板键（唯一标识）</label>
              <input class="inp" v-model="edit.key" :disabled="!!edit.id"></div>
            <div class="field" style="margin:0"><label>名称</label><input class="inp" v-model="edit.name"></div>
            <div class="field" style="margin:0"><label>语言</label><select class="inp" v-model="edit.lang">
              <option value="en">英文</option><option value="cn">中文</option></select></div>
          </div>
          <div class="sec-title">HTML 模板（Jinja2 语法）</div>
          <div class="var-pick">
            <span class="muted small">点击变量插入到光标处：</span>
            <code v-for="f in quickTokens" :key="f" class="pickable" @click="insertToken(f)">{{ f }}</code>
          </div>
          <textarea class="inp mono" rows="18" v-model="edit.body_html" ref="bodyRef"
            style="font-size:11.5px;line-height:1.55"></textarea>
          <div class="sec-title">CSS 样式</div>
          <textarea class="inp mono" rows="10" v-model="edit.css" style="font-size:11.5px;line-height:1.55"></textarea>
        </div>
        <div class="modal-f"><button class="btn" @click="edit=null">取消</button>
          <button class="btn primary" :disabled="saving" @click="save">保存模板</button></div>
      </div>
    </div>
  </div>`
};
