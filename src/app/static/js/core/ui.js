/* ============================ 通用工具 ============================ */
const toastList = reactive([]);
function notify(msg, type = 'ok', title = '') {
  const id = Date.now() + Math.random();
  toastList.push({ id, msg, type, title });
  setTimeout(() => { const i = toastList.findIndex(t => t.id === id); if (i >= 0) toastList.splice(i, 1); },
    type === 'err' ? 5600 : 3200);
}
const fmtMoney = (n, cur) => (Number(n || 0)).toLocaleString('en-US',
  { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + (cur ? ' ' + cur : '');
const CERT_MAP = {
  CVC: 'CVC食品接触认证', SC: '全国工业产品生产许可证', ISO9001: 'ISO9001质量体系',
  CE: 'CE 认证', FDA: 'FDA食品接触符合性', ETL: 'ETL列名认证', ETL_SANITATION: 'ETL卫生认证'
};
const certName = c => CERT_MAP[c] || c;
const fmtSize = b => !b ? '0 B' : b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1024).toFixed(0) + ' KB';
const emptyProduct = () => ({
  sku: '', name_cn: '', name_en: '', category_code: 'OVEN-DECK', supplier_code: 'SELF',
  brand: 'Felixv', model: '', spec: '', spec_en: '', material: '', power: '', capacity: '',
  dimension: '', packing_size: '', net_weight: null, gross_weight: null, price_cny: null,
  price_usd: null, moq: 1, lead_time: '', hs_code: '', certs: [], image_path: '', images: [],
  desc_en: '', keywords: ''
});
const emptyCustomer = () => ({
  code: '', company_en: '', contact: '', country: '', address: '', email: '', phone: '',
  incoterm: 'FOB Guangzhou', port: 'Guangzhou / Shenzhen',
  payment_terms: '30% T/T deposit, 70% balance before shipment', currency: 'USD', lang: 'en', remark: ''
});

/* 全局工具清单：在 app.js 创建应用时统一注册为全局属性。
   这样任何页面模板都能直接用 fmtMoney(...)，不必每个页面在 setup() 里
   重复 return 一遍 —— 漏一次就是 "fmtMoney is not a function"，页面整块报错。 */
const SHARED_HELPERS = { fmtMoney, certName, fmtSize, emptyProduct, emptyCustomer };

/* 轻量 Modal 组件（用 v-if 控制，不依赖 portal） */
const ModalShell = {
  props: ['title', 'width', 'okText', 'busy', 'hideFooter'],
  emits: ['close'],
  template: `
  <div class="mask" @click.self="$emit('close')">
    <div class="modal" :class="width||''">
      <div class="modal-h">
        <h3>{{ title }}</h3>
        <div style="display:flex;align-items:center;gap:9px">
          <span class="tag" style="background:#F0F1F4;color:#8B90A0">{{ windowLabel }}</span>
          <span class="x-btn" @click="$emit('close')">×</span>
        </div>
      </div>
      <div class="modal-b"><slot></slot></div>
      <div class="modal-f" v-if="!hideFooter">
        <slot name="footer">
          <button class="btn" @click="$emit('close')">取消</button>
          <button class="btn primary" :disabled="busy" @click="$emit('ok')">{{ busy?'处理中…':(okText||'确定') }}</button>
        </slot>
      </div>
    </div>
  </div>`,
  data() { return { windowLabel: '' }; }
};
