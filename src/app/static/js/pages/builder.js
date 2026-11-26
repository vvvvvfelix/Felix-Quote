/* ============================ 组件：报价单制作 ============================ */
const BuilderPage = {
  props: ['categories', 'templates'],
  emits: ['saved'],
  setup(props, { emit }) {
    const form = ref({
      customer_id: null, title: '', lang: 'en', template_key: 'standard_en',
      incoterm: 'FOB Guangzhou', port: 'Guangzhou / Shenzhen', currency: 'USD',
      payment_terms: '30% T/T deposit, 70% balance before shipment', valid_days: 30,
      freight: 0, insurance: 0, discount_rate: 0, remark: '', items: []
    });
    const quoteId = ref(null), quoteNo = ref('');
    const customers = ref([]), products = ref([]);
    const saving = ref(false), picker = reactive({ open: false, keyword: '', list: [], loading: false });
    const custModal = reactive({ open: false, form: emptyCustomer(), saving: false });
    const preview = reactive({ open: false, html: '', loading: false });

    const totals = computed(() => {
      let sub = 0;
      form.value.items.forEach(i => { sub += (Number(i.qty) || 0) * (Number(i.price) || 0); });
      const disc = sub * (Number(form.value.discount_rate) || 0) / 100;
      return {
        subtotal: sub, discount: disc,
        total: sub + (Number(form.value.freight) || 0) + (Number(form.value.insurance) || 0) - disc
      };
    });
    const totalGW = computed(() => form.value.items.reduce((s, i) => s + (Number(i.gross_weight) || 0) * (Number(i.qty) || 0), 0));
    const totalNW = computed(() => form.value.items.reduce((s, i) => s + (Number(i.net_weight) || 0) * (Number(i.qty) || 0), 0));

    async function loadRefs() {
      customers.value = await api.get('/api/customers').catch(() => []);
      products.value = (await api.get('/api/products?limit=500').catch(() => ({ items: [] }))).items;
    }
    async function searchPicker() {
      picker.loading = true;
      try {
        const r = await api.get('/api/products?limit=50&q=' + encodeURIComponent(picker.keyword));
        picker.list = r.items;
      } catch (e) { notify(e.message, 'err'); }
      finally { picker.loading.value = false; }
    }
    async function openPicker() { picker.open = true; picker.keyword = ''; await searchPicker(); }
    function addProduct(p) {
      const dup = form.value.items.findIndex(i => i.product_id === p.id);
      const row = {
        product_id: p.id, sku: p.sku, model: p.model, name_cn: p.name_cn, name_en: p.name_en,
        material: p.material, spec: p.spec, spec_en: p.spec_en, dimension: p.dimension,
        packing_size: p.packing_size, net_weight: p.net_weight, gross_weight: p.gross_weight,
        power: p.power, capacity: p.capacity, certs: p.certs || [], lead_time: p.lead_time,
        qty: 1, price: p.price_usd || p.price_cny || 0
      };
      if (dup >= 0) { form.value.items[dup].qty = Number(form.value.items[dup].qty || 0) + 1; }
      else form.value.items.push(row);
      notify('已加入：' + (p.name_cn || p.sku), 'ok');
    }
    function addBlank() {
      form.value.items.push({ qty: 1, price: 0, certs: [] });
    }
    function removeItem(i) { form.value.items.splice(i, 1); }
    async function save(status) {
      if (!form.value.items.length) return notify('请先添加产品', 'warn');
      if (!form.value.customer_id) return notify('请选择客户', 'warn');
      saving.value = true;
      const body = { ...form.value, status: status || 'draft' };
      try {
        if (quoteId.value) {
          await api.put('/api/quotations/' + quoteId.value, body);
          quoteNo.value = quoteNo.value || ('WGQ 已保存');
        } else {
          const r = await api.post('/api/quotations', body);
          quoteId.value = r.id;
          quoteNo.value = r.quote_no;
        }
        notify('报价单已保存：' + (quoteNo.value || '（更新）'), 'ok');
        // 不自动跳转：保持在编辑页，便于连续预览 / 导出 PDF
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    async function doPreview() {
      if (!quoteId.value) { await save('draft'); if (!quoteId.value) return; }
      preview.loading = true; preview.open = true; preview.html = '';
      try {
        const r = await fetch('/api/quotations/' + quoteId.value + '/preview',
          { headers: { Authorization: 'Bearer ' + localStorage.getItem(TOKEN_KEY) } });
        preview.html = await r.text();
      } catch (e) { notify(e.message, 'err'); }
      finally { preview.loading = false; }
    }
    async function exportPdf() {
      if (!quoteId.value) { await save('draft'); if (!quoteId.value) return; }
      try {
        await api.download('/api/quotations/' + quoteId.value + '/pdf', (quoteNo.value || 'quote') + '.pdf');
        notify('PDF 已下载', 'ok');
      } catch (e) { notify(e.message, 'err'); }
    }
    function pickCustomer(cid) {
      const c = customers.value.find(x => x.id === Number(cid));
      if (c) {
        form.value.payment_terms = c.payment_terms || form.value.payment_terms;
        form.value.incoterm = c.incoterm || form.value.incoterm;
        form.value.port = c.port || form.value.port;
        form.value.currency = c.currency || 'USD';
        form.value.lang = c.lang || 'en';
        if (!form.value.title) form.value.title = 'Quotation for ' + c.company_en;
      }
    }
    async function saveCustomer() {
      custModal.saving = true;
      try {
        const r = custModal.form.id ? await api.put('/api/customers/' + custModal.form.id, custModal.form)
          : await api.post('/api/customers', custModal.form);
        customers.value = await api.get('/api/customers');
        custModal.open = false;
        form.value.customer_id = r.id;
        pickCustomer(r.id);
        notify('客户已保存', 'ok');
      } catch (e) { notify(e.message, 'err'); }
      finally { custModal.saving = false; }
    }
    async function loadQuote(qid) {
      try {
        const r = await api.get('/api/quotations/' + qid);
        quoteId.value = r.id; quoteNo.value = r.quote_no;
        form.value = {
          customer_id: r.customer_id, title: r.title, lang: r.lang, template_key: r.template_key,
          incoterm: r.incoterm, port: r.port, currency: r.currency, payment_terms: r.payment_terms,
          valid_days: r.valid_days, freight: r.freight, insurance: r.insurance,
          discount_rate: r.discount_rate, remark: r.remark, items: r.items
        };
        notify('已载入 ' + r.quote_no, 'ok');
      } catch (e) { notify(e.message, 'err'); }
    }
    function resetForm() {
      quoteId.value = null; quoteNo.value = '';
      form.value = { ...emptyQuote(), items: [] };
    }
    function emptyQuote() {
      return {
        customer_id: null, title: '', lang: 'en', template_key: 'standard_en',
        incoterm: 'FOB Guangzhou', port: 'Guangzhou / Shenzhen', currency: 'USD',
        payment_terms: '30% T/T deposit, 70% balance before shipment', valid_days: 30,
        freight: 0, insurance: 0, discount_rate: 0, remark: '', items: []
      };
    }
    onMounted(() => { loadRefs(); });
    return {
      form, quoteId, quoteNo, customers, products, saving, picker, custModal, preview,
      totals, totalGW, totalNW, openPicker, searchPicker, addProduct, addBlank, removeItem,
      save, doPreview, exportPdf, pickCustomer, saveCustomer, loadQuote, resetForm,
      fmtMoney, certName, notify, categories: props.categories, templates: props.templates
    };
  },
  template: `
  <div>
    <div class="card">
      <div class="card-h">
        <div><h3>{{ quoteNo ? '编辑报价单 '+quoteNo : '新建报价单' }}</h3>
          <div class="d">选择客户与模板 → 添加产品 → 生成 PDF</div></div>
        <div class="btn-row">
          <button class="btn sm" @click="resetForm">新建空白</button>
          <button class="btn sm" @click="doPreview">👁 预览</button>
          <button class="btn primary sm" :disabled="saving" @click="save('draft')">保存草稿</button>
          <button class="btn dark sm" :disabled="saving" @click="exportPdf">⬇ 导出 PDF</button>
        </div>
      </div>
      <div class="card-b">
        <div class="grid3">
          <div class="field"><label>客户 <span class="req">*</span></label>
            <div style="display:flex;gap:6px">
              <select class="inp" v-model="form.customer_id" @change="pickCustomer(form.customer_id)">
                <option :value="null">请选择客户…</option>
                <option v-for="c in customers" :key="c.id" :value="c.id">{{ c.company_en }}</option>
              </select>
              <button class="btn" style="white-space:nowrap" @click="custModal.open=true;custModal.form=emptyCustomer()">＋</button>
            </div>
          </div>
          <div class="field"><label>报价单标题</label><input class="inp" v-model="form.title" placeholder="Quotation for …"></div>
          <div class="field"><label>模板</label><select class="inp" v-model="form.template_key">
            <option v-for="t in templates" :key="t.key" :value="t.key">{{ t.name }}</option></select></div>
        </div>
        <div class="grid4">
          <div class="field"><label>贸易条款</label><select class="inp" v-model="form.incoterm">
            <option>FOB Guangzhou</option><option>FOB Shenzhen</option><option>CIF {{ form.port }}</option>
            <option>EXW</option><option>DDP</option><option>CFR</option></select></div>
          <div class="field"><label>装运港</label><input class="inp" v-model="form.port"></div>
          <div class="field"><label>币种</label><select class="inp" v-model="form.currency">
            <option>USD</option><option>EUR</option><option>GBP</option><option>CNY</option></select></div>
          <div class="field"><label>有效期（天）</label><input class="inp" type="number" v-model="form.valid_days"></div>
        </div>
        <div class="field"><label>付款方式</label><input class="inp" v-model="form.payment_terms"></div>
        <div class="field" style="margin-bottom:0"><label>备注（显示在报价单）</label>
          <textarea class="inp" rows="2" v-model="form.remark"></textarea></div>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div><h3>报价明细</h3><div class="d">{{ form.items.length }} 项 · 合计净重 {{ totalNW.toFixed(0) }} kg / 毛重 {{ totalGW.toFixed(0) }} kg</div></div>
        <button class="btn primary sm" @click="openPicker">＋ 从产品库添加</button>
      </div>
      <div class="card-b tight">
        <div v-if="!form.items.length" class="empty"><div class="ic">✎</div>
          <div class="t">还没有产品</div><div>点击右上角从产品库添加，或手工新增一行</div>
          <button class="btn sm" style="margin-top:12px" @click="addBlank">手工新增一行</button></div>
        <div v-else class="tbl-wrap">
          <table class="tbl responsive">
            <thead><tr>
              <th style="width:34px">#</th><th style="min-width:220px">产品</th>
              <th style="width:110px">材质</th><th style="width:110px">外形尺寸</th>
              <th style="width:58px" class="r">净重</th><th style="width:58px" class="r">毛重</th>
              <th style="width:64px" class="r">数量</th><th style="width:96px" class="r">单价</th>
              <th style="width:104px" class="r">金额</th><th style="width:32px"></th>
            </tr></thead>
            <tbody>
              <tr v-for="(it,i) in form.items" :key="i">
                <td class="muted" data-label="#">{{ i+1 }}</td>
                <td data-label="产品">
                  <input v-model="it.name_cn" placeholder="中文品名" style="font-weight:600;margin-bottom:3px">
                  <input v-model="it.name_en" placeholder="English name">
                  <div class="mono muted" style="font-size:10px;margin-top:2px">{{ it.model||'' }} {{ it.sku? '· '+it.sku:'' }}</div>
                </td>
                <td data-label="材质"><input v-model="it.material"></td>
                <td data-label="外形尺寸"><input v-model="it.dimension"></td>
                <td class="r mono" data-label="净重">{{ it.net_weight||'—' }}</td>
                <td class="r mono" data-label="毛重">{{ it.gross_weight||'—' }}</td>
                <td class="r" data-label="数量"><input v-model.number="it.qty" type="number" min="1" style="text-align:right"></td>
                <td class="r" data-label="单价"><input v-model.number="it.price" type="number" step="0.01" style="text-align:right"></td>
                <td class="r mono b" data-label="金额">{{ fmtMoney((it.qty||0)*(it.price||0)) }}</td>
                <td class="rm" data-label="操作" @click="removeItem(i)">×</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="card-b" v-if="form.items.length" style="border-top:1px solid #E3E5EA">
        <div style="max-width:430px;margin-left:auto">
          <div class="grid3" style="gap:11px">
            <div class="field" style="margin:0"><label>运费</label>
              <input class="inp" type="number" v-model.number="form.freight"></div>
            <div class="field" style="margin:0"><label>保险费</label>
              <input class="inp" type="number" v-model.number="form.insurance"></div>
            <div class="field" style="margin:0"><label>折扣 %</label>
              <input class="inp" type="number" step="0.1" v-model.number="form.discount_rate"></div>
          </div>
          <div class="total-box">
            <div class="total-row"><span>小计</span><b>{{ fmtMoney(totals.subtotal, form.currency) }}</b></div>
            <div class="total-row" v-if="form.freight"><span>运费</span><span>{{ fmtMoney(form.freight, form.currency) }}</span></div>
            <div class="total-row" v-if="form.insurance"><span>保险费</span><span>{{ fmtMoney(form.insurance, form.currency) }}</span></div>
            <div class="total-row" v-if="form.discount_rate"><span>折扣 ({{ form.discount_rate }}%)</span>
              <span>-{{ fmtMoney(totals.discount, form.currency) }}</span></div>
            <div class="total-row grand"><span>总计</span><span>{{ fmtMoney(totals.total, form.currency) }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 产品选择器 -->
    <div v-if="picker.open" class="mask" @click.self="picker.open=false">
      <div class="modal">
        <div class="modal-h"><h3>从产品库添加</h3><span class="x-btn" @click="picker.open=false">×</span></div>
        <div style="padding:14px 20px 0">
          <input class="inp" v-model="picker.keyword" placeholder="搜索型号 / 名称 / 材质…"
            @input="searchPicker" style="width:100%" autofocus>
        </div>
        <div class="modal-b picker" style="padding-top:12px">
          <div v-if="!picker.list.length" class="empty"><div class="ic">◌</div><div>{{picker.loading?'搜索中…':'无匹配产品'}}</div></div>
          <div v-for="p in picker.list" :key="p.id" class="picker-item" @click="addProduct(p)">
            <div class="flex-between">
              <div style="min-width:0">
                <div class="t">{{ p.name_cn }} <span class="mono muted" style="font-weight:400">{{ p.model||p.sku }}</span></div>
                <div class="s">{{ p.name_en }}</div>
                <div class="s">{{ p.material }} · {{ p.capacity||'' }} · NW {{p.net_weight||'?'}} / GW {{p.gross_weight||'?'}} kg</div>
              </div>
              <div style="text-align:right;white-space:nowrap">
                <div class="b" style="color:#B8860B">{{ fmtMoney(p.price_usd, 'USD') }}</div>
                <div class="s">{{ (p.certs||[]).slice(0,2).join(' / ') }}</div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-f"><button class="btn primary" @click="picker.open=false">完成</button></div>
      </div>
    </div>

    <!-- 新建客户 -->
    <div v-if="custModal.open" class="mask" @click.self="custModal.open=false">
      <div class="modal narrow">
        <div class="modal-h"><h3>新建客户</h3><span class="x-btn" @click="custModal.open=false">×</span></div>
        <div class="modal-b">
          <div class="field"><label>客户公司名 <span class="req">*</span></label><input class="inp" v-model="custModal.form.company_en"></div>
          <div class="grid2">
            <div class="field"><label>联系人</label><input class="inp" v-model="custModal.form.contact"></div>
            <div class="field"><label>国家 / 地区</label><input class="inp" v-model="custModal.form.country"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>邮箱</label><input class="inp" v-model="custModal.form.email"></div>
            <div class="field"><label>电话</label><input class="inp" v-model="custModal.form.phone"></div>
          </div>
          <div class="field"><label>地址</label><input class="inp" v-model="custModal.form.address"></div>
          <div class="grid2">
            <div class="field"><label>贸易条款</label><input class="inp" v-model="custModal.form.incoterm"></div>
            <div class="field"><label>币种</label><select class="inp" v-model="custModal.form.currency">
              <option>USD</option><option>EUR</option><option>GBP</option><option>CNY</option></select></div>
          </div>
          <div class="field"><label>付款方式</label><input class="inp" v-model="custModal.form.payment_terms"></div>
        </div>
        <div class="modal-f">
          <button class="btn" @click="custModal.open=false">取消</button>
          <button class="btn primary" :disabled="custModal.saving" @click="saveCustomer">保存客户</button>
        </div>
      </div>
    </div>

    <!-- 预览 -->
    <div v-if="preview.open" class="mask" @click.self="preview.open=false">
      <div class="modal wide" style="max-width:980px">
        <div class="modal-h"><h3>报价单预览</h3><span class="x-btn" @click="preview.open=false">×</span></div>
        <div class="modal-b" style="background:#E9EBEF">
          <div v-if="preview.loading" class="empty">生成中…</div>
          <iframe v-else class="page-embed" style="height:660px" :srcdoc="preview.html" sandbox="allow-same-origin"></iframe>
        </div>
        <div class="modal-f">
          <button class="btn" @click="preview.open=false">关闭</button>
          <button class="btn dark" @click="exportPdf">⬇ 导出 PDF</button>
        </div>
      </div>
    </div>
  </div>`
};
