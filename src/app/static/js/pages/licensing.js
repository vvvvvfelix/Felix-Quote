/* ============================ 组件：授权激活 ============================ */
const LicensingPage = {
  setup() {
    const state = ref(null), token = ref(''), busy = ref(false), msg = ref('');
    async function load() {
      try { state.value = await api.get('/api/licensing/status'); } catch (e) { notify(e.message, 'err'); }
    }
    async function activate() {
      if (!token.value.trim()) { notify('请粘贴授权码', 'warn'); return; }
      busy.value = true; msg.value = '';
      try {
        const r = await api.post('/api/licensing/activate', { token: token.value.trim() });
        state.value = r.state; msg.value = '激活成功：' + (r.state.owner || '');
        notify('激活成功', 'ok');
      } catch (e) { msg.value = e.message; notify(e.message, 'err'); }
      finally { busy.value = false; }
    }
    async function deactivate() {
      if (!confirm('确认解绑本机授权？')) return;
      try { const r = await api.post('/api/licensing/deactivate'); state.value = r.state; notify('已解绑', 'ok'); }
      catch (e) { notify(e.message, 'err'); }
    }
    onMounted(load);
    return { state, token, busy, msg, load, activate, deactivate, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">试用期 30 天，到期请联系作者获取授权码激活</span>
      <span class="grow"></span>
      <button class="btn" @click="load">刷新状态</button>
    </div>

    <div v-if="!state" class="card"><div class="card-b muted">加载中…</div></div>
    <template v-else>
      <div class="card">
        <div class="card-h">当前授权状态</div>
        <div class="card-b">
          <div class="kv"><span>模式</span><b>{{ state.mode }}</b></div>
          <div class="kv"><span>是否激活</span><b>{{ state.activated ? '是' : '否（试用中）' }}</b></div>
          <div class="kv" v-if="state.owner"><span>授权归属</span><b>{{ state.owner }}</b></div>
          <div class="kv" v-if="state.expires"><span>到期时间</span><b>{{ state.expires }}</b></div>
          <div class="kv" v-if="state.days_left!=null"><span>剩余天数</span><b>{{ state.days_left }}</b></div>
          <div class="kv"><span>机器绑定</span><b>{{ state.machine_bound ? '已绑定本机' : '通用/未绑定' }}</b></div>
          <div class="kv"><span>说明</span><b>{{ state.message }}</b></div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">激活授权</div>
        <div class="card-b">
          <textarea v-model="token" rows="4" style="width:100%;padding:8px;border:1px solid #d4d9e2;border-radius:8px" placeholder="在此粘贴作者签发的授权码…"></textarea>
          <div style="margin-top:10px;display:flex;gap:10px;align-items:center">
            <button class="btn" :disabled="busy" @click="activate">{{ busy ? '激活中…' : '激活' }}</button>
            <button class="btn" v-if="state.activated" @click="deactivate">解绑本机</button>
            <span v-if="msg" class="muted small">{{ msg }}</span>
          </div>
          <div class="muted small" style="margin-top:10px">
            授权码由作者通过 <code>tools/license_gen.py</code> 签发，绑定本机安装指纹或设为通用。
            未激活时为试用模式，30 天后系统将限制使用，激活后即可恢复正常。
          </div>
        </div>
      </div>
    </template>
  </div>
  `,
};
