/* ============================ 组件：客户管理 ============================ */
const CustomersPage = {
  emits: ['send'],
  setup(props, { emit }) {
    const list = ref([]), q = ref(''), loading = ref(true);
    const modal = reactive({ open: false, form: emptyCustomer(), saving: false });
    function openSend(c) { emit('send', c); }
    async function load() {
      loading.value = true;
      try { list.value = await api.get('/api/customers' + (q.value ? '?q=' + encodeURIComponent(q.value) : '')); }
      catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }
    function open(c) { modal.form = c ? { ...c } : emptyCustomer(); modal.open = true; }
    async function save() {
      modal.saving = true;
      try {
        if (modal.form.id) await api.put('/api/customers/' + modal.form.id, modal.form);
        else await api.post('/api/customers', modal.form);
        notify('客户已保存', 'ok'); modal.open = false; load();
      } catch (e) { notify(e.message, 'err'); }
      finally { modal.saving = false; }
    }
    async function remove(c) {
      if (!confirm('删除客户「' + c.company_en + '」？')) return;
      try { await api.del('/api/customers/' + c.id); notify('已删除', 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    onMounted(load);
    return { list, q, loading, modal, load, open, save, remove, openSend, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <input class="inp" v-model="q" placeholder="搜索客户 / 国家…" @input="load">
      <span class="grow"></span>
      <button class="btn primary" @click="open(null)">＋ 新增客户</button>
    </div>
    <div class="card"><div class="card-b tight">
      <div v-if="!list.length && !loading" class="empty"><div class="ic">◍</div>
        <div class="t">暂无客户</div><div>新增客户后可在报价单中一键选用</div></div>
      <div v-else class="tbl-wrap">
        <table class="tbl responsive">
          <thead><tr><th>编号</th><th>公司</th><th>联系人</th><th>国家</th>
            <th>联系方式</th><th>贸易条款</th><th>币种</th><th class="c">操作</th></tr></thead>
          <tbody><tr v-for="c in list" :key="c.id">
            <td class="mono small" data-label="编号">{{ c.code }}</td>
            <td class="b ell" data-label="公司">{{ c.company_en }}</td>
            <td data-label="联系人">{{ c.contact||'—' }}</td>
            <td data-label="国家">{{ c.country||'—' }}</td>
            <td class="small" data-label="联系方式"><div>
              <a v-if="c.email" class="mail-link" :title="'给 ' + (c.contact||c.company_en) + ' 发送报价单'"
                 @click.stop="openSend(c)">{{ c.email }}</a>
              <span v-else class="muted">未填邮箱</span>
            </div><div class="muted">{{ c.phone||'' }}</div></td>
            <td class="small" data-label="贸易条款">{{ c.incoterm }}<div class="muted">{{ c.port }}</div></td>
            <td data-label="币种"><span class="tag">{{ c.currency }}</span></td>
            <td class="c" data-label="操作" style="white-space:nowrap">
              <button class="btn sm" @click="open(c)">编辑</button>
              <button class="btn sm danger" @click="remove(c)">删除</button></td>
          </tr></tbody>
        </table>
      </div>
    </div></div>

    <div v-if="modal.open" class="mask" @click.self="modal.open=false">
      <div class="modal narrow">
        <div class="modal-h"><h3>{{ modal.form.id?'编辑客户':'新增客户' }}</h3>
          <span class="x-btn" @click="modal.open=false">×</span></div>
        <div class="modal-b">
          <div class="field"><label>公司名称 <span class="req">*</span></label><input class="inp" v-model="modal.form.company_en"></div>
          <div class="grid2">
            <div class="field"><label>联系人</label><input class="inp" v-model="modal.form.contact"></div>
            <div class="field"><label>国家 / 地区</label><input class="inp" v-model="modal.form.country"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>邮箱</label><input class="inp" v-model="modal.form.email"></div>
            <div class="field"><label>电话</label><input class="inp" v-model="modal.form.phone"></div>
          </div>
          <div class="field"><label>地址</label><input class="inp" v-model="modal.form.address"></div>
          <div class="grid2">
            <div class="field"><label>贸易条款</label><input class="inp" v-model="modal.form.incoterm"></div>
            <div class="field"><label>装运港</label><input class="inp" v-model="modal.form.port"></div>
          </div>
          <div class="grid2">
            <div class="field"><label>币种</label><select class="inp" v-model="modal.form.currency">
              <option>USD</option><option>EUR</option><option>GBP</option><option>CNY</option></select></div>
            <div class="field"><label>报价语言</label><select class="inp" v-model="modal.form.lang">
              <option value="en">英文</option><option value="cn">中文</option></select></div>
          </div>
          <div class="field"><label>付款方式</label><input class="inp" v-model="modal.form.payment_terms"></div>
          <div class="field"><label>备注</label><textarea class="inp" rows="2" v-model="modal.form.remark"></textarea></div>
        </div>
        <div class="modal-f"><button class="btn" @click="modal.open=false">取消</button>
          <button class="btn primary" :disabled="modal.saving" @click="save">保存</button></div>
      </div>
    </div>
  </div>`
};
