/* ============================ 组件：发件设置 ============================ */
const MailSettingsPage = {
  setup() {
    const cfg = reactive({ host: '', port: 465, secure: true, use_auth: true,
      username: '', password: '', sender: '', sender_name: '', provider: '' });
    const providers = ref([]), loading = ref(true), saving = ref(false);
    const testing = ref(false), msg = ref(null);
    const pwdTouched = ref(false);

    async function load() {
      loading.value = true;
      try {
        const [p, c] = await Promise.all([
          api.get('/api/mail/providers'),
          api.get('/api/mail/config'),
        ]);
        providers.value = p.providers || [];
        const { password, ...rest } = c || {};
        Object.assign(cfg, {
          host: c.host || '', port: c.port || 465, secure: c.secure !== false,
          use_auth: c.use_auth !== false, username: c.username || '',
          password: '', sender: c.sender || '', sender_name: c.sender_name || '',
          provider: c.provider || '',
        });
        // 后端返回脱敏占位符，本地不显示，保存时后端会保留原密码
        if (password) cfg.password = '';
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    /* 当前选中的服务商预设（模板里不写 .find()，避免表达式过长） */
    const current = computed(() =>
      providers.value.find(x => x.key === cfg.provider) || null);

    /* 选预设 → 自动填服务器地址与端口，并提示授权码要求 */
    function pick(p) {
      cfg.provider = p.key;
      if (p.key !== 'custom') {
        cfg.host = p.host; cfg.port = p.port; cfg.secure = p.secure;
      }
      msg.value = { type: 'info', text: p.note };
    }

    function openDoc(p) {
      if (!p.doc) return notify('该邮箱暂无官方说明链接', 'warn');
      window.open(p.doc, '_blank', 'noopener');
    }

    async function save() {
      saving.value = true;
      try {
        await api.put('/api/mail/config', { ...cfg });
        notify('发件设置已保存', 'ok');
        const c = await api.get('/api/mail/config');
        cfg.password = '';
      } catch (e) { notify(e.message, 'err'); }
      finally { saving.value = false; }
    }

    async function test() {
      testing.value = true; msg.value = null;
      try {
        // 先保存再测，保证测的是当前表单内容
        await api.put('/api/mail/config', { ...cfg });
        const r = await api.post('/api/mail/test', {});
        msg.value = { type: 'ok', text: '测试邮件已发送，请检查 ' + cfg.sender + ' 收件箱' };
      } catch (e) { msg.value = { type: 'err', text: e.message }; }
      finally { testing.value = false; }
    }

    onMounted(load);
    return { cfg, providers, current, loading, saving, testing, msg, load, pick, openDoc,
             save, test, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">每人一套发件配置，互不影响。密码经加密后存本机数据库。</span>
      <span class="grow"></span>
      <button class="btn" :disabled="testing || loading" @click="test">{{ testing?'发送中…':'发送测试邮件' }}</button>
      <button class="btn primary" :disabled="saving" @click="save">{{ saving?'保存中…':'保存设置' }}</button>
    </div>

    <div v-if="msg" class="warn-box" style="margin-bottom:13px"
         :style="msg.type==='ok'?'background:#EEF9F1;border-color:#B8E3C6;border-left-color:#1E7A4C;color:#17603C'
                :msg.type==='err'?'background:#FFF2F1;border-color:#F2C4C0;border-left-color:#C0392B;color:#9C2B20'
                :''">{{ msg.text }}</div>

    <div class="card"><div class="card-b">
      <div class="sec-title">① 选择邮箱服务商</div>
      <div class="mail-picks">
        <div v-for="p in providers" :key="p.key" class="mail-pick"
             :class="{on: cfg.provider===p.key}" @click="pick(p)">
          <div class="n">{{ p.name }}</div>
          <div class="s">{{ p.host||'自定义' }} : {{ p.port }}</div>
        </div>
      </div>
      <div v-if="current" class="mail-doc">
        <span class="muted small">配置遇到问题？</span>
        <a v-if="current.doc" class="lnk" @click.prevent="openDoc(current)"
           target="_blank" rel="noopener">查看 {{ current.name }} 官方配置说明 ↗</a>
        <span v-else class="muted small">该邮箱暂无官方说明链接</span>
        <span class="muted small">（新窗口打开官方帮助页，含授权码获取步骤）</span>
      </div>
      <div v-if="current" class="warn-box" style="margin-top:10px">
        <b>注意：</b>{{ current.note }}
      </div>

      <div class="sec-title" style="margin-top:18px">② 填写发件信息</div>
      <div class="grid2">
        <div class="field" style="margin:0"><label>SMTP 服务器</label>
          <input class="inp" v-model="cfg.host" placeholder="如 smtp.qq.com"></div>
        <div class="field" style="margin:0"><label>端口</label>
          <input class="inp" v-model.number="cfg.port" placeholder="465 或 587"></div>
      </div>
      <div class="grid2" style="margin-top:11px">
        <div class="field" style="margin:0"><label>登录账号</label>
          <input class="inp" v-model="cfg.username" placeholder="通常是完整邮箱地址"></div>
        <div class="field" style="margin:0"><label>授权码 / 密码</label>
          <input class="inp" type="password" v-model="cfg.password"
                 placeholder="留空表示不修改已保存的密码"></div>
      </div>
      <div class="grid2" style="margin-top:11px">
        <div class="field" style="margin:0"><label>发件人邮箱</label>
          <input class="inp" v-model="cfg.sender" placeholder="对外显示的发件地址"></div>
        <div class="field" style="margin:0"><label>发件人名称</label>
          <input class="inp" v-model="cfg.sender_name" placeholder="如 Felix / 贵司名称"></div>
      </div>
      <div style="margin-top:11px;display:flex;gap:18px;align-items:center">
        <label class="chk"><input type="checkbox" v-model="cfg.secure"> 使用 SSL/TLS 加密</label>
        <label class="chk"><input type="checkbox" v-model="cfg.use_auth"> 需要登录认证</label>
        <span class="muted small">465 端口通常勾选 SSL；587 端口不勾选（用 STARTTLS）</span>
      </div>
    </div></div>
  </div>`
};
