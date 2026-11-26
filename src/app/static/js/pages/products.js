/* ============================ 组件：产品库 ============================ */
const ProductsPage = {
  props: ['categories', 'suppliers'],
  setup() {
    const items = ref([]), total = ref(0), loading = ref(false);
    const q = reactive({ keyword: '', category: '', supplier: '', maxPrice: '' });
    const edit = ref(null), saving = ref(false), tab = ref('list');
    const picker = reactive({ open: false, keyword: '', list: [], loading: false });
    const supName = c => (props_supplierName(c));

    function props_supplierName(code) {
      const s = (arguments[0] && supplierLookup.value || []).find(x => x.code === code);
      return s ? s.name_cn : code;
    }
    const supplierLookup = ref([]);

    async function load() {
      loading.value = true;
      try {
        const p = new URLSearchParams();
        if (q.keyword) p.set('q', q.keyword);
        if (q.category) p.set('category', q.category);
        if (q.supplier) p.set('supplier', q.supplier);
        if (q.maxPrice) p.set('price_max', q.maxPrice);
        p.set('limit', '500');
        const r = await api.get('/api/products?' + p.toString());
        items.value = r.items; total.value = r.total;
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    let tmr;
    watch(() => [q.keyword, q.category, q.supplier, q.maxPrice], () => {
      clearTimeout(tmr); tmr = setTimeout(load, 250);
    });

    /* 品类参数模板：不同品类字段不同，随 category_code 动态切换 */
    const paramTpl = ref({ label: '通用/其他', params: [] });
    async function loadParamTpl(code) {
      if (!code) { paramTpl.value = { label: '通用/其他', family: '', params: [] }; return; }
      try {
        const r = await api.get('/api/param-templates/' + encodeURIComponent(code));
        paramTpl.value = { label: r.label, family: r.family, params: r.params };
      } catch (e) { paramTpl.value = { label: '通用/其他', family: '', params: [] }; }
    }
    // 品类切换时重载模板，并保证 edit.specs 存在
    watch(() => edit.value && edit.value.category_code, (c) => {
      loadParamTpl(c);
      if (edit.value && !edit.value.specs) edit.value.specs = {};
    }, { immediate: true });

    // 品类模板字段中，哪些已在固定列里（避免重复渲染）。
    // capacity/power/material 等是全品类通用列，已有固定输入框，因此不重复渲染；
    // 但它们的标签文案需随品类语义变化（搅拌机 capacity 是"缸容量"、冷藏是"有效容积"）。
    const FIXED_KEYS = ['sku', 'model', 'name_cn', 'name_en', 'material', 'power',
      'capacity', 'dimension', 'packing_size', 'net_weight', 'gross_weight',
      'price_cny', 'price_usd', 'moq', 'lead_time', 'hs_code', 'spec', 'spec_en'];
    const dynamicParams = computed(() =>
      (paramTpl.value.params || []).filter(p => !FIXED_KEYS.includes(p.key)));
    const paramValue = (k) => (edit.value && edit.value.specs ? edit.value.specs[k] : '');

    // 通用列标签随品类语义调整
    const SEMANTIC_LABELS = {
      capacity: { OVEN: '容积 / 层数 / 盘数', MIXER: '缸容量', FREEZER: '有效容积',
WASH: '洗涤容量', PROCESS: '规格 / 处理能力', DISPLAY: '容积' },
      power: { MIXER: '电机功率', FREEZER: '功率' },
      material: { OVEN: '内胆材质', MIXER: '缸体材质', FREEZER: '内胆 / 台面材质',
                  PROCESS: '接触物料材质' },
    };
    const DEFAULT_FIXED_LABELS = { capacity: '容积 / 层数 / 盘数',
      power: '功率 / 电压 / 能源', material: '主体材质' };
    const fixedLabel = (key) => {
      const fam = paramTpl.value.family || '';
      const m = SEMANTIC_LABELS[key] || {};
      return m[fam] || DEFAULT_FIXED_LABELS[key] || '';
    };

    async function openEdit(p) {
      if (p) {
        const r = await api.get('/api/products/' + p.id);
        edit.value = { ...emptyProduct(), ...r, specs: r.specs || {} };
      } else {
        edit.value = { ...emptyProduct(), specs: {} };
      }
      await loadParamTpl(edit.value.category_code);
      tab.value = 'basic';
    }
    async function save() {
      saving.value = true;
      try {
        const body = { ...edit.value, specs: edit.value.specs || {} };
        if (edit.value.id) await api.put('/api/products/' + edit.value.id, body);
        else await api.post('/api/products', body);
        notify('产品已保存', 'ok'); edit.value = null; load();
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    async function remove(p) {
      if (!confirm('确定下架产品「' + p.name_cn + '」？')) return;
      try { await api.del('/api/products/' + p.id); notify('已下架', 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    async function searchPicker() {
      picker.loading = true;
      try {
        const r = await api.get('/api/products?limit=40&q=' + encodeURIComponent(picker.keyword));
        picker.list = r.items;
      } catch (e) { notify(e.message, 'err'); }
      finally { picker.loading = false; }
    }
    async function openPicker() { picker.open = true; await searchPicker(); }
    async function uploadImage(ev) {
      const f = ev.target.files[0]; if (!f) return;
      try {
        const r = await api.upload('/api/uploads', f);
        edit.value.image_path = r.url; notify('图片已上传', 'ok');
      } catch (e) { notify(e.message, 'err'); }
      ev.target.value = '';
    }
    onMounted(async () => {
      await load();
      supplierLookup.value = await api.get('/api/suppliers').catch(() => []);
    });
    return {
      items, total, loading, q, edit, saving, tab, picker, load, openEdit, save, remove,
      openPicker, searchPicker, uploadImage, supName, notify, fmtMoney, certName,
      paramTpl, dynamicParams, paramValue, fixedLabel
    };
  },
  template: `
  <div>
    <div class="toolbar">
      <input class="inp" v-model="q.keyword" placeholder="搜索型号 / 名称 / 规格 / 材质…">
      <select class="inp" v-model="q.category" style="max-width:180px">
        <option value="">全部分类</option>
        <option v-for="c in categories" :key="c.code" :value="c.code">{{ c.name_cn }}</option>
      </select>
      <select class="inp" v-model="q.supplier" style="max-width:170px">
        <option value="">全部供应商</option>
        <option v-for="s in suppliers" :key="s.code" :value="s.code">{{ s.name_cn }}</option>
      </select>
      <input class="inp" type="number" v-model="q.maxPrice" placeholder="USD 价格上限" style="max-width:130px">
      <span class="grow"></span>
      <span class="muted small">共 {{ total }} 个产品</span>
      <button class="btn primary" @click="openEdit(null)">＋ 新增产品</button>
    </div>

    <div class="card"><div class="card-b tight">
      <div v-if="!items.length" class="empty"><div class="ic">▤</div><div class="t">没有匹配的产品</div>
        <div>调整筛选条件，或新增一个产品</div></div>
      <div v-else class="tbl-wrap">
        <table class="tbl responsive">
          <thead><tr>
            <th>型号 / SKU</th><th>品名</th><th>材质</th><th>规格</th>
            <th class="r">净重</th><th class="r">毛重</th><th class="r">USD</th>
            <th>证书</th><th>供应商</th><th class="c">操作</th>
          </tr></thead>
          <tbody><tr v-for="p in items" :key="p.id">
            <td data-label="型号 / SKU"><div class="mono b">{{ p.model||p.sku }}</div><div class="mono muted" style="font-size:10.5px">{{ p.sku }}</div></td>
            <td data-label="品名"><div class="ell b">{{ p.name_cn }}</div><div class="ell muted small">{{ p.name_en }}</div></td>
            <td class="small ell" data-label="材质" style="max-width:130px">{{ p.material||'—' }}</td>
            <td class="small ell" data-label="规格" style="max-width:190px">{{ p.capacity||p.spec||'—' }}</td>
            <td class="r mono" data-label="净重">{{ p.net_weight||'—' }}</td>
            <td class="r mono" data-label="毛重">{{ p.gross_weight||'—' }}</td>
            <td class="r mono b" data-label="USD" style="color:#B8860B">{{ p.price_usd? fmtMoney(p.price_usd):'—' }}</td>
            <td data-label="证书"><span v-for="c in (p.certs||[]).slice(0,3)" :key="c" class="tag gold" style="margin-right:3px">{{ c }}</span></td>
            <td class="small ell" data-label="供应商" style="max-width:120px">{{ supName(p.supplier_code) }}</td>
            <td class="c" data-label="操作" style="white-space:nowrap">
              <button class="btn sm" @click="openEdit(p)">编辑</button>
              <button class="btn sm danger" @click="remove(p)">下架</button>
            </td>
          </tr></tbody>
        </table>
      </div>
    </div></div>

    <!-- 编辑弹窗 -->
    <div v-if="edit" class="mask" @click.self="edit=null">
      <div class="modal wide">
        <div class="modal-h"><h3>{{ edit.id?'编辑产品':'新增产品'}}</h3>
          <span class="x-btn" @click="edit=null">×</span></div>
        <div class="modal-b">
          <div class="tabs-line">
            <button :class="{on:tab==='basic'}" @click="tab='basic'">基本信息</button>
            <button :class="{on:tab==='spec'}" @click="tab='spec'">规格与材质</button>
            <button :class="{on:tab==='logistics'}" @click="tab='logistics'">重量与价格</button>
            <button :class="{on:tab==='desc'}" @click="tab='desc'">外贸描述</button>
          </div>

          <template v-if="tab==='basic'">
            <div class="grid3">
              <div class="field"><label>SKU 货号 <span class="req">*</span></label><input class="inp" v-model="edit.sku"></div>
              <div class="field"><label>产品型号</label><input class="inp" v-model="edit.model" placeholder="如 ZKX-6"></div>
              <div class="field"><label>品牌</label><input class="inp" v-model="edit.brand"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>中文品名 <span class="req">*</span></label><input class="inp" v-model="edit.name_cn"></div>
              <div class="field"><label>英文品名</label><input class="inp" v-model="edit.name_en" placeholder="用于外贸报价单"></div>
            </div>
            <div class="grid3">
              <div class="field"><label>所属分类</label><select class="inp" v-model="edit.category_code">
                <option v-for="c in categories" :key="c.code" :value="c.code">{{ c.name_cn }}</option></select></div>
              <div class="field"><label>供应商</label><select class="inp" v-model="edit.supplier_code">
                <option v-for="s in suppliers" :key="s.code" :value="s.code">{{ s.name_cn }}</option></select></div>
              <div class="field"><label>MOQ 起订量</label><input class="inp" type="number" v-model="edit.moq"></div>
            </div>
          </template>

          <template v-if="tab==='spec'">
            <div class="field"><label>规格描述（中文）</label><input class="inp" v-model="edit.spec"></div>
            <div class="field"><label>规格描述（英文）</label><input class="inp" v-model="edit.spec_en" placeholder="用于外贸报价单描述"></div>
            <div class="grid3">
              <div class="field"><label>{{ fixedLabel('material') }}</label>
                <input class="inp" v-model="edit.material" placeholder="如 SUS304"></div>
              <div class="field"><label>{{ fixedLabel('power') }}</label>
                <input class="inp" v-model="edit.power"></div>
              <div class="field"><label>{{ fixedLabel('capacity') }}</label>
                <input class="inp" v-model="edit.capacity"></div>
            </div>
            <div class="grid2">
              <div class="field"><label>外形尺寸 (mm)</label><input class="inp" v-model="edit.dimension" placeholder="1100x780x1400"></div>
              <div class="field"><label>包装尺寸 (mm)</label><input class="inp" v-model="edit.packing_size" placeholder="用于计算体积"></div>
            </div>
            <div class="sec-title">
              {{ paramTpl.label }} · 品类专属参数
              <span class="muted small" style="font-weight:400;margin-left:8px">
                — 同行为主，不同品类参数体系不同，随品类自动切换</span>
            </div>
            <div v-if="!dynamicParams.length"
              style="padding:11px 13px;background:#FAFBFC;border-radius:7px;font-size:11.5px;color:#8B90A0">
              该品类使用通用参数（规格/功率/材质/尺寸/净重毛重）
            </div>
            <div v-else class="grid3">
              <div v-for="p in dynamicParams" :key="p.key" class="field">
                <label>{{ p.label_cn }}<span v-if="p.required" class="req">*</span>
                  <span v-if="p.unit" class="muted" style="font-weight:400">（{{ p.unit }}）</span>
                </label>
                <select v-if="p.type === 'select' && p.options && p.options.length" class="inp"
                  v-model="edit.specs[p.key]">
                  <option value="">请选择…</option>
                  <option v-for="o in p.options" :key="o" :value="o">{{ o }}</option>
                </select>
                <input v-else class="inp" :type="p.type === 'number' ? 'number' : 'text'"
                  v-model="edit.specs[p.key]"
                  :placeholder="'英文报单显示为 ' + p.label_en">
              </div>
            </div>
            <div class="sec-title">相关证书</div>
            <label v-for="(v,k) in {CVC:'CVC食品接触认证',SC:'生产许可证',ISO9001:'ISO9001',CE:'CE',FDA:'FDA',ETL:'ETL',ETL_SANITATION:'ETL卫生认证'}"
              :key="k" style="display:inline-flex;align-items:center;gap:6px;margin-right:18px;cursor:pointer;margin-bottom:8px">
              <input type="checkbox" :value="k" v-model="edit.certs" style="cursor:pointer">
              <span class="small">{{ v }}</span>
            </label>
          </template>

          <template v-if="tab==='logistics'">
            <div class="grid4">
              <div class="field"><label>净重 (kg)</label><input class="inp" type="number" step="0.1" v-model="edit.net_weight"></div>
              <div class="field"><label>毛重 (kg)</label><input class="inp" type="number" step="0.1" v-model="edit.gross_weight"></div>
              <div class="field"><label>参考价 ¥</label><input class="inp" type="number" step="0.01" v-model="edit.price_cny"></div>
              <div class="field"><label>FOB 价 USD</label><input class="inp" type="number" step="0.01" v-model="edit.price_usd"></div>
            </div>
            <div class="grid3">
              <div class="field"><label>交期</label><input class="inp" v-model="edit.lead_time" placeholder="如 30-45天"></div>
              <div class="field"><label>HS 海关编码</label><input class="inp" v-model="edit.hs_code"></div>
            </div>
            <div class="field"><label>产品图片</label>
              <div style="display:flex;gap:11px;align-items:center">
                <img v-if="edit.image_path" :src="edit.image_path" style="width:110px;height:82px;object-fit:cover;border-radius:7px;border:1px solid #E3E5EA">
                <label class="btn" style="cursor:pointer">{{ edit.image_path?'更换图片':'上传图片' }}
                  <input type="file" accept="image/*" style="display:none" @change="uploadImage"></label>
                <span class="muted small">{{ edit.image_path?'':'支持 JPG / PNG / WEBP' }}</span>
              </div>
            </div>
          </template>

          <template v-if="tab==='desc'">
            <div class="field"><label>英文详情描述（外贸用）</label>
              <textarea class="inp" rows="5" v-model="edit.desc_en"
                placeholder="面向海外买家的产品卖点描述"></textarea></div>
            <div class="field"><label>搜索关键词</label><input class="inp" v-model="edit.keywords" placeholder="用空格分隔，便于检索"></div>
          </template>
        </div>
        <div class="modal-f">
          <button class="btn" @click="edit=null">取消</button>
          <button class="btn primary" :disabled="saving" @click="save">{{ saving?'保存中…':'保存产品' }}</button>
        </div>
      </div>
    </div>
  </div>`
};
