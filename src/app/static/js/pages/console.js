/* ============================ 组件：管理控制台 ============================ */
const ConsolePage = {
  setup() {
    const status = ref(null), mods = ref([]), failures = ref([]);
    const integ = ref(null), loading = ref(true), saving = ref(false);
    const dirty = ref({});          // code -> bool（待保存的勾选）
    // 连接器配置（M120）：企业微信 SSO / 消息推送
    const wecomCfg = ref({ enabled: false, corpid: '', agentid: '', secret: '' });
    const wecomSaving = ref(false);
    const wecomMsg = ref('');

    async function load() {
      loading.value = true;
      try {
        const [s, m] = await Promise.all([
          api.get('/api/platform/status'),
          api.get('/api/platform/modules')
        ]);
        status.value = s; mods.value = m.modules || []; failures.value = m.failures || [];
        dirty.value = {};
        try { integ.value = await api.get('/api/org/integrity'); }
        catch (e) { integ.value = null; }
        try { const w = await api.get('/api/connectors/wecom/config'); wecomCfg.value = w.config || wecomCfg.value; }
        catch (e) { /* 非管理员或无权限时忽略 */ }
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    async function saveWecom() {
      wecomSaving.value = true; wecomMsg.value = '';
      try {
        const r = await api.post('/api/connectors/wecom/config', {
          corpid: wecomCfg.value.corpid, agentid: wecomCfg.value.agentid,
          secret: wecomCfg.value.secret, enabled: !!wecomCfg.value.enabled
        });
        wecomCfg.value = r.config || wecomCfg.value;
        wecomMsg.value = '已保存企业微信连接器配置（密钥已脱敏存储）';
        notify('企业微信连接器配置已保存', 'ok');
      } catch (e) { wecomMsg.value = e.message; notify(e.message, 'err'); }
      finally { wecomSaving.value = false; }
    }

    function checked(m) {
      return Object.prototype.hasOwnProperty.call(dirty.value, m.code)
        ? dirty.value[m.code] : m.enabled;
    }
    function toggle(m) {
      if (m.core) return;
      dirty.value[m.code] = !checked(m);
    }
    function isDirty(m) {
      return Object.prototype.hasOwnProperty.call(dirty.value, m.code)
        && dirty.value[m.code] !== m.enabled;
    }
    function changedCount() { return mods.value.filter(isDirty).length; }

    async function save() {
      const list = mods.value.filter(isDirty);
      if (!list.length) { notify('没有需要保存的改动', 'info'); return; }
      saving.value = true;
      const errs = [];
      // 先开后关：开启时依赖可能尚未启用，先处理开启可少一轮往返
      for (const m of list.sort((a, b) => (checked(b) ? 1 : 0) - (checked(a) ? 1 : 0))) {
        try {
          await api.post('/api/platform/modules/toggle',
            { code: m.code, enabled: !!checked(m) });
        } catch (e) { errs.push(`${m.name}：${e.message}`); }
      }
      saving.value = false;
      await load();
      if (errs.length) { notify(errs.join('；'), 'err'); return; }
      notify('已保存，重启服务后生效', 'ok');
    }

    async function preset(keep) {
      for (const m of mods.value) {
        if (m.core) continue;
        dirty.value[m.code] = keep.indexOf(m.code) >= 0;
      }
    }
    function presetMinimal() {
      preset(['masterdata', 'quotation', 'order', 'task', 'workbench']);
    }
    function presetAll() { for (const m of mods.value) if (!m.core) dirty.value[m.code] = true; }

    onMounted(load);
    return { status, mods, failures, integ, loading, saving, dirty, load,
             checked, toggle, isDirty, changedCount, save, presetMinimal, presetAll,
             wecomCfg, wecomSaving, wecomMsg, saveWecom, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">运行状态 · 模块开关 · 组织体检　（模块开关重启服务后生效）</span>
      <span class="grow"></span>
      <button class="btn" @click="presetMinimal">精简预设</button>
      <button class="btn" @click="presetAll">全部启用</button>
      <button class="btn primary" :disabled="saving || !changedCount()" @click="save">
        {{ saving ? '保存中…' : '保存改动（' + changedCount() + '）' }}</button>
      <button class="btn" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="card"><div class="card-b"><div class="muted">加载中…</div></div></div>

    <div v-else>
      <!-- 运行状态 -->
      <div class="card">
        <div class="card-h">运行状态</div>
        <div class="card-b">
          <div class="stat-row">
            <div class="stat"><div class="sv">● 运行中</div><div class="sl">服务状态</div></div>
            <div class="stat"><div class="sv">{{ status.pid }}</div><div class="sl">进程 PID</div></div>
            <div class="stat"><div class="sv">{{ status.db_size_mb }} MB</div><div class="sl">数据库</div></div>
            <div class="stat"><div class="sv">{{ status.tables }}</div><div class="sl">数据表</div></div>
            <div class="stat"><div class="sv">{{ status.modules_enabled }}/{{ status.modules_total }}</div><div class="sl">已启用模块</div></div>
          </div>
          <div class="muted small" style="margin-top:8px">数据库文件：{{ status.db_path }} · 后端：{{ status.db_backend }}</div>
          <div v-if="status.license" class="muted small">
            授权：{{ status.license.mode }}{{ status.license.activated ? '（已激活）' : '（试用中）' }}
            <span v-if="status.license.days_left!=null"> · 剩余 {{ status.license.days_left }} 天</span>
            <span v-if="status.license.expired" style="color:var(--err)"> · 已到期</span>
          </div>
          <div v-if="failures.length" class="alert err" style="margin-top:10px">
            装配失败模块：{{ failures.map(f => f.module + '（' + f.error + '）').join('；') }}
          </div>
        </div>
      </div>

      <!-- 组织体检 -->
      <div class="card" v-if="integ">
        <div class="card-h">组织架构体检</div>
        <div class="card-b">
          <div class="stat-row">
            <div class="stat"><div class="sv">{{ integ.active }}/{{ integ.count }}</div><div class="sl">有效/全部组织</div></div>
            <div class="stat"><div class="sv">{{ integ.roots }}</div><div class="sl">顶层组织</div></div>
            <div class="stat"><div class="sv">{{ integ.orphans.length }}</div><div class="sl">孤儿节点</div></div>
            <div class="stat"><div class="sv">{{ integ.broken_links.length }}</div><div class="sl">断链（父级停用）</div></div>
            <div class="stat"><div class="sv">{{ integ.cycles.length }}</div><div class="sl">环</div></div>
          </div>
          <div v-if="integ.healthy" class="muted small" style="margin-top:8px">
            组织树健康。中间层级缺失时系统会自动穿透关联到最近的有效组织，不影响运行。</div>
          <div v-else class="alert warn" style="margin-top:10px">
            发现问题但系统仍可运行（已自动跳层兜底）：
            <div v-if="integ.orphans.length">· 父级不存在的组织：{{ integ.orphans.map(o => o.name).join('、') }}</div>
            <div v-if="integ.broken_links.length">· 父级已停用的组织：{{ integ.broken_links.map(o => o.name + '（已自动挂到 ' + o.auto_parent + '）').join('、') }}</div>
            <div v-if="integ.cycles.length">· 成环：{{ integ.cycles.map(o => o.name).join('、') }}</div>
          </div>
        </div>
      </div>

      <!-- 模块开关 -->
      <div class="card">
        <div class="card-h">模块开关</div>
        <div class="card-b">
          <table class="tbl responsive">
            <thead><tr><th style="width:44px">启用</th><th style="width:120px">模块</th>
              <th style="width:70px">分组</th><th>说明</th><th style="width:120px">依赖</th></tr></thead>
            <tbody>
              <tr v-for="m in mods" :key="m.code" :class="{dirty: isDirty(m)}">
                <td data-label="启用">
                  <input type="checkbox" :checked="checked(m)" :disabled="m.core"
                         @change="toggle(m)">
                </td>
                <td data-label="模块"><b>{{ m.name }}</b><span v-if="m.core" class="tag gray">核心</span>
                  <div v-if="isDirty(m)" class="tag">待保存</div></td>
                <td class="muted small" data-label="分组">{{ m.group }}</td>
                <td class="muted small" data-label="说明">{{ m.desc }}</td>
                <td class="muted small" data-label="依赖">{{ m.depends.join('、') || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <div class="muted small" style="margin-top:10px">
            核心模块（登录、平台、组织身份）不可关闭；被其他已启用模块依赖的模块无法单独关闭。
            关闭模块只隐藏功能与菜单，<b>数据保留</b>，随时可再开启。
          </div>
        </div>
      </div>

      <!-- 连接器配置（M120）：企业微信 SSO / 消息推送 -->
      <div class="card">
        <div class="card-h">连接器（企业微信）</div>
        <div class="card-b">
          <label class="row" style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <input type="checkbox" v-model="wecomCfg.enabled">
            <span>启用企业微信登录与消息推送</span>
          </label>
          <div class="grid2">
            <div class="field"><label>CorpID（企业 ID）</label>
              <input class="inp" v-model="wecomCfg.corpid" placeholder="如 ww8b3a1c2d3e4f5a6b"></div>
            <div class="field"><label>AgentID（应用 ID）</label>
              <input class="inp" v-model="wecomCfg.agentid" placeholder="如 1000002"></div>
          </div>
          <div class="field"><label>Secret（应用密钥）</label>
            <input class="inp" type="password" v-model="wecomCfg.secret"
              :placeholder="wecomCfg.secret ? '已保存（留空表示不修改）' : '在企业微信后台「应用管理」获取'"></div>
          <div class="muted small" style="margin:8px 0">
            配置后，企业微信内置浏览器访问将自动跳授权；回调按「内部用户名 = 企业微信 userid」匹配登录。
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn primary" :disabled="wecomSaving" @click="saveWecom">
              {{ wecomSaving ? '保存中…' : '保存连接器配置' }}</button>
            <span v-if="wecomMsg" class="muted small">{{ wecomMsg }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>`
};
