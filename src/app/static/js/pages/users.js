/* ============================ 组件：用户管理 ============================ */
const UsersPage = {
  setup() {
    const list = ref([]);
    async function load() { try { list.value = await api.get('/api/users'); } catch (e) { notify(e.message, 'err'); } }
    async function toggle(u) {
      try { await api.del('/api/users/' + u.id); notify('已更新', 'ok'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    onMounted(load);
    return { list, toggle, notify };
  },
  template: `
  <div>
    <div class="toolbar"><span class="muted small">用户注册后自动出现在此列表（仅管理员可见）</span></div>
    <div class="card"><div class="card-b tight">
      <div class="tbl-wrap"><table class="tbl responsive">
        <thead><tr><th>用户名</th><th>姓名</th><th>邮箱</th><th>公司</th><th>角色</th><th>状态</th><th>创建时间</th><th class="c">操作</th></tr></thead>
        <tbody><tr v-for="u in list" :key="u.id">
          <td class="mono b" data-label="用户名">{{ u.username }}</td><td data-label="姓名">{{ u.full_name||'—' }}</td>
          <td class="small" data-label="邮箱">{{ u.email||'—' }}</td><td class="small" data-label="公司">{{ u.company||'—' }}</td>
          <td data-label="角色"><span class="tag" :class="u.role==='admin'?'gold':'blue'">{{ u.role==='admin'?'管理员':'销售' }}</span></td>
          <td data-label="状态"><span class="tag" :class="u.is_active?'ok':'err'">{{ u.is_active?'启用':'停用' }}</span></td>
          <td class="muted small" data-label="创建时间">{{ (u.created_at||'').slice(0,16) }}</td>
          <td class="c" data-label="操作"><button class="btn sm" @click="toggle(u)">{{ u.is_active?'停用':'启用' }}</button></td>
        </tr></tbody>
      </table></div>
    </div></div>
  </div>`
};
