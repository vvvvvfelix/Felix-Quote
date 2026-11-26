/* ============================ 组件：供应商 ============================ */
const SuppliersPage = {
  setup() {
    const list = ref([]), modal = reactive({ open: false, form: {}, saving: false });
    async function load() { try { list.value = await api.get('/api/suppliers'); } catch (e) { notify(e.message, 'err'); } }
    function open(s) { modal.form = s ? { ...s } : { code: '', name_cn: '', name_en: '', contact: '', phone: '', email: '', remark: '' }; modal.open = true; }
    async function save() {
      modal.saving = true;
      try { await api.post('/api/suppliers', modal.form); notify('供应商已保存', 'ok'); modal.open = false; load(); }
      catch (e) { notify(e.message, 'err'); }
      finally { modal.saving = false; }
    }
    onMounted(load);
    return { list, modal, load, open, save, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">自有产线 + 外协供应商，统一管理配件与代工产品</span>
      <span class="grow"></span>
      <button class="btn primary" @click="open(null)">＋ 新增供应商</button>
    </div>
    <div class="card"><div class="card-b tight">
      <div class="tbl-wrap">
        <table class="tbl responsive">
          <thead><tr><th>编号</th><th>供应商</th><th>主营</th><th>联系方式</th><th>备注</th><th class="c">操作</th></tr></thead>
          <tbody><tr v-for="s in list" :key="s.id">
            <td class="mono" data-label="编号">{{ s.code }}</td>
            <td data-label="供应商"><div class="b">{{ s.name_cn }}</div><div class="muted small">{{ s.name_en }}</div></td>
            <td class="small" data-label="主营">{{ s.remark||'—' }}</td>
            <td class="small" data-label="联系方式"><div>{{ s.contact||'' }}</div><div class="muted">{{ s.phone||'' }}</div>
              <div class="muted">{{ s.email||'' }}</div></td>
            <td class="small muted" data-label="备注">—</td>
            <td class="c" data-label="操作"><button class="btn sm" @click="open(s)">编辑</button></td>
          </tr></tbody>
        </table>
      </div>
    </div></div>

    <div v-if="modal.open" class="mask" @click.self="modal.open=false">
      <div class="modal narrow">
        <div class="modal-h"><h3>{{ modal.form.code?'编辑供应商':'新增供应商' }}</h3>
          <span class="x-btn" @click="modal.open=false">×</span></div>
        <div class="modal-b">
          <div class="grid2">
            <div class="field"><label>编号 <span class="req">*</span></label><input class="inp" v-model="modal.form.code"></div>
            <div class="field"><label>联系人</label><input class="inp" v-model="modal.form.contact"></div>
          </div>
          <div class="field"><label>中文名称 <span class="req">*</span></label><input class="inp" v-model="modal.form.name_cn"></div>
          <div class="field"><label>英文名称</label><input class="inp" v-model="modal.form.name_en"></div>
          <div class="grid2">
            <div class="field"><label>电话</label><input class="inp" v-model="modal.form.phone"></div>
            <div class="field"><label>邮箱</label><input class="inp" v-model="modal.form.email"></div>
          </div>
          <div class="field"><label>主营 / 备注</label><textarea class="inp" rows="2" v-model="modal.form.remark"></textarea></div>
        </div>
        <div class="modal-f"><button class="btn" @click="modal.open=false">取消</button>
          <button class="btn primary" :disabled="modal.saving" @click="save">保存</button></div>
      </div>
    </div>
  </div>`
};
