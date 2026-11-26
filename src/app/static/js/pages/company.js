/* ============================ 组件：公司信息 ============================ */
const CompanyPage = {
  props: ['company'], emits: ['saved'],
  setup(props, { emit }) {
    const f = ref({}), saving = ref(false);
    watch(() => props.company, v => { if (v && Object.keys(v).length) f.value = JSON.parse(JSON.stringify(v)); },
      { immediate: true, deep: true });
    async function save() {
      saving.value = true;
      try { await api.put('/api/company', f.value); notify('公司信息已保存', 'ok'); emit('saved'); }
      catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }
    return { f, saving, save, notify };
  },
  template: `
  <div>
    <div class="card">
      <div class="card-h"><div><h3>公司主体信息</h3>
        <div class="d">用于报价单页眉与抬头</div></div>
        <button class="btn primary sm" :disabled="saving" @click="save">保存</button></div>
      <div class="card-b">
        <div class="grid3">
          <div class="field"><label>品牌</label><input class="inp" v-model="f.brand"></div>
          <div class="field"><label>公司中文名</label><input class="inp" v-model="f.name_cn"></div>
          <div class="field"><label>公司英文名</label><input class="inp" v-model="f.name_en"></div>
        </div>
        <div class="grid3">
          <div class="field"><label>电话</label><input class="inp" v-model="f.phone"></div>
          <div class="field"><label>服务热线</label><input class="inp" v-model="f.hotline"></div>
          <div class="field"><label>网址</label><input class="inp" v-model="f.website"></div>
        </div>
        <div class="field"><label>地址</label><input class="inp" v-model="f.address_cn"></div>
        <div class="field"><label>公司简介（中文）</label><textarea class="inp" rows="3" v-model="f.intro_cn"></textarea></div>
        <div class="field"><label>公司简介（英文）</label><textarea class="inp" rows="3" v-model="f.intro_en"></textarea></div>
        <div class="grid3">
          <div class="field"><label>成立日期</label><input class="inp" v-model="f.founded"></div>
          <div class="field"><label>注册资本</label><input class="inp" v-model="f.capital"></div>
          <div class="field"><label>员工人数</label><input class="inp" v-model="f.employees"></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><div><h3>企业资质与证书</h3>
        <div class="d">勾选后会出现在报价单的 Certification 栏</div></div></div>
      <div class="card-b">
        <label v-for="c in (f.certs||[])" :key="c.code" style="display:flex;align-items:center;gap:10px;
          padding:10px 0;border-bottom:1px solid #F0F1F4;cursor:pointer">
          <input type="checkbox" :checked="true" disabled style="cursor:pointer">
          <span class="tag gold">{{ c.code }}</span>
          <div><div class="b small">{{ c.name_cn }}</div><div class="muted small">{{ c.name_en }}</div></div>
        </label>
        <div style="margin-top:13px;padding:12px 14px;background:var(--gold-l);border-radius:8px;
          font-size:11.5px;color:#5A6070;line-height:1.8">
          <b>企业荣誉</b><br>
          <span v-for="h in (f.honors||[])" :key="h">{{ h }}<br></span>
        </div>
      </div>
    </div>
  </div>`
};
