/* ============================ 组件：连接器（M120） ============================
 * 企业微信 / 钉钉 / 飞书 SSO 连接器配置页。
 *
 * 现状（诚实标注，避免下一任误判）：
 * - 企业微信：已完整实现（配置存取 / 授权地址 / 回调换 token），本页可直接配置启用。
 * - 钉钉、飞书：后端仅有 ConnectorBase 占位子类（enabled() 恒 false），
 *   **本轮未实现**，页面里明确标灰说明，不做假入口。
 *
 * 安全：secret 后端读取时脱敏为 ******，绝不回传明文；本页保存时若留空，
 * 后端会保留原 secret（见 modules/connectors.py 的 api_wecom_config_set）。
 */
const ConnectorsPage = {
  setup() {
    const cfg = reactive({ corpid: '', agentid: '', secret: '', enabled: false });
    const loading = ref(true), saving = ref(false), msg = ref(null);
    const enabledNow = ref(false);
    /* 回调地址：企微后台「可信域名 / 回调 URL」要填的就是这个，直接显示给管理员复制 */
    const callbackUrl = ref('');

    async function load() {
      loading.value = true;
      try {
        const r = await api.get('/api/connectors/wecom/config');
        const c = r.config || {};
        cfg.corpid = c.corpid || '';
        cfg.agentid = c.agentid || '';
        cfg.secret = '';              // 后端脱敏回传，本地永远不显示明文
        cfg.enabled = !!c.enabled;
        enabledNow.value = !!r.enabled;
      } catch (e) { notify(e.message, 'err'); }
      finally { loading.value = false; }
    }

    async function save() {
      saving.value = true; msg.value = null;
      try {
        const r = await api.post('/api/connectors/wecom/config', {
          corpid: cfg.corpid, agentid: cfg.agentid,
          secret: cfg.secret, enabled: cfg.enabled,
        });
        enabledNow.value = !!r.enabled;
        cfg.secret = '';              // 保存后清空输入框，避免明文滞留页面
        msg.value = {
          type: 'ok',
          text: r.enabled ? '已保存，企业微信连接器已启用'
                          : '已保存，连接器当前为停用状态',
        };
        await load();
      } catch (e) { msg.value = { type: 'err', text: e.message }; }
      finally { saving.value = false; }
    }

    function copyCallback() {
      const v = callbackUrl.value;
      if (!v) return notify('回调地址尚未计算', 'warn');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(v).then(
          () => notify('回调地址已复制', 'ok'),
          () => notify('复制失败，请手动选中复制', 'warn'));
      } else {
        notify('当前浏览器不支持一键复制，请手动选中复制', 'warn');
      }
    }

    onMounted(async () => {
      callbackUrl.value = location.origin + '/api/connectors/wecom/callback';
      await load();
    });

    return { cfg, loading, saving, msg, enabledNow, callbackUrl, load, save,
             copyCallback, notify };
  },
  template: `
  <div>
    <div class="toolbar">
      <span class="muted small">连接器让用户在企业微信 / 钉钉 / 飞书内免密登录本系统。凭据保存在本机数据库。</span>
      <span class="grow"></span>
      <button class="btn" :disabled="loading" @click="load">{{ loading?'载入中…':'刷新' }}</button>
      <button class="btn primary" :disabled="saving" @click="save">{{ saving?'保存中…':'保存设置' }}</button>
    </div>

    <div v-if="msg" class="warn-box" style="margin-bottom:13px"
         :style="msg.type==='ok'?'background:#EEF9F1;border-color:#B8E3C6;border-left-color:#1E7A4C;color:#17603C'
                :msg.type==='err'?'background:#FFF2F1;border-color:#F2C4C0;border-left-color:#C0392B;color:#9C2B20'
                :''">{{ msg.text }}</div>

    <!-- ---------------- 企业微信：本轮唯一完整实现 ---------------- -->
    <div class="card"><div class="card-b">
      <div class="sec-title">① 企业微信（已实现）</div>
      <div class="muted small" style="margin-bottom:10px">
        状态：<b :style="enabledNow?'color:#1E7A4C':'color:#8B90A0'">
          {{ enabledNow ? '已启用' : '未启用' }}</b>
        <span v-if="!enabledNow">（未启用时，企微内置浏览器不会跳转授权，走普通账号登录）</span>
      </div>

      <div class="grid2">
        <div class="field" style="margin:0"><label>CorpID（企业ID）</label>
          <input class="inp" v-model="cfg.corpid" placeholder="如 ww1234567890abcdef"></div>
        <div class="field" style="margin:0"><label>AgentID（应用ID）</label>
          <input class="inp" v-model="cfg.agentid" placeholder="如 1000002"></div>
      </div>
      <div class="field" style="margin-top:10px"><label>Secret（应用密钥）</label>
        <input class="inp" type="password" v-model="cfg.secret"
               placeholder="留空则保留已保存的密钥"></div>

      <label style="display:flex;align-items:center;gap:7px;margin-top:12px;cursor:pointer">
        <input type="checkbox" v-model="cfg.enabled" style="width:15px;height:15px">
        <span>启用企业微信登录</span>
      </label>

      <div class="warn-box" style="margin-top:13px">
        <b>回调地址（填到企微后台「网页授权及JS-SDK / 可信域名」）：</b><br>
        <code style="user-select:all">{{ callbackUrl }}</code>
        <button class="btn" style="margin-left:8px" @click="copyCallback">复制</button>
        <div class="muted small" style="margin-top:6px">
          账号匹配规则：系统用户的<b>登录名</b>需与企业微信 userid 一致；
          未匹配时回调会提示「请联系管理员绑定」，不会放行。
        </div>
      </div>
    </div></div>

    <!-- ---------------- 钉钉 / 飞书：接口预留，本轮未实现 ---------------- -->
    <div class="card" style="margin-top:14px"><div class="card-b">
      <div class="sec-title">② 钉钉 / 飞书（接口预留，本轮未实现）</div>
      <div class="muted small">
        后端 <code>modules/connectors.py</code> 已为这两个平台留好
        <code>ConnectorBase</code> 子类（<code>DingTalkConnector</code> /
        <code>FeishuConnector</code>），但 <code>enabled()</code> 恒返回 false，
        授权地址与回调均未接通 —— 当前<b>不可配置、不可用</b>。
      </div>
      <div class="tbl responsive" style="margin-top:11px">
        <table>
          <thead><tr><th>平台</th><th>状态</th><th>后续要做</th></tr></thead>
          <tbody>
            <tr><td data-label="平台">钉钉</td>
                <td data-label="状态">占位</td>
                <td data-label="后续要做">接 oapi.dingtalk.com 扫码登录 + 免登 code→userid，补 config 接口</td></tr>
            <tr><td data-label="平台">飞书</td>
                <td data-label="状态">占位</td>
                <td data-label="后续要做">接 open.feishu.cn authen/v1/authorize + code→userinfo，补 config 接口</td></tr>
          </tbody>
        </table>
      </div>
      <div class="muted small" style="margin-top:8px">
        扩展方式：在 <code>connectors.py</code> 里给对应子类实现
        <code>authorize_url()</code> / <code>exchange_code()</code>，
        再加一组 <code>/api/connectors/&lt;platform&gt;/config</code> 与
        <code>callback</code> 路由即可（settings 键建议 <code>connector:&lt;platform&gt;</code>）。
      </div>
    </div></div>
  </div>
  `
};
