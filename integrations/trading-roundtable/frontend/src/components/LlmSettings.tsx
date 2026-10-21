import { useEffect, useState } from 'react';
import { errorText, get, newKey, post } from '../api/v1';
import { Dialog, ErrorNote, Field } from './ui';

export interface LlmProviderInfo {
  id: string; label: string; supports_search: boolean | null;
  default_model: string | null; default_base_url: string | null; key_env: string | null; registered: boolean;
}
export interface LlmModelItem { name: string; long_context?: boolean; label?: string; selected?: boolean }
export interface LlmProfile {
  id: string; name: string; provider: string; label: string; model: string; base_url: string; api_key_env: string;
  daily_token_limit: number | null; json_mode: boolean; active: boolean; enabled: boolean; reason: string;
  key_configured: boolean; key_source?: string; models?: LlmModelItem[]; updated_at?: string | null;
}
export interface LlmStatus {
  provider: string; label?: string; model: string; base_url?: string; enabled: boolean; reason: string;
  key_source?: string; key_configured?: boolean; supports_search: boolean; search_available?: boolean;
  budget: { used: number; limit: number };
  profile_id?: string; profile_name?: string; profiles?: LlmProfile[]; profile_limit?: number; config_saved?: boolean;
  config_path?: string; providers?: LlmProviderInfo[];
  seat_bindings?: Record<string, { profile_id: string; profile_name: string }>;
}

interface Draft {
  id: string | null; name: string; provider: string; model: string; baseUrl: string; keyEnv: string;
  limit: string; jsonMode: boolean; models: LlmModelItem[];
}

const EMPTY: Draft = { id: null, name: '', provider: 'deepseek', model: '', baseUrl: '', keyEnv: '', limit: '',
                       jsonMode: true, models: [] };

function draftOf(p: LlmProfile): Draft {
  return { id: p.id, name: p.name, provider: p.provider, model: p.model, baseUrl: p.base_url,
           keyEnv: p.api_key_env, limit: p.daily_token_limit ? String(p.daily_token_limit) : '',
           jsonMode: p.json_mode, models: (p.models ?? []).map(m => ({ name: m.name, long_context: !!m.long_context })) };
}

/**
 * 模型设置（ADR-008 / ADR-009 / ADR-010）：保存多份模型存档、随时切换；
 * 每份存档带一个「模型列表」——可以问供应商要它支持的模型，也可以手填。
 * 配置写在本机运行目录；密钥只存本机私有文件（0600），接口不回显。
 */
export function LlmSettingsDialog({ open, onClose, onSaved }:
  { open: boolean; onClose: () => void; onSaved?: (s: LlmStatus) => void }) {
  const [st, setSt] = useState<LlmStatus | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [modelsMsg, setModelsMsg] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const apply = (s: LlmStatus, pickId?: string | null) => {
    setSt(s);
    const list = s.profiles ?? [];
    const target = pickId !== undefined
      ? list.find(p => p.id === pickId)
      : list.find(p => p.id === draft.id) ?? list.find(p => p.id === s.profile_id) ?? list[0];
    if (target) setDraft(draftOf(target));
    setApiKey(''); setClearKey(false); setDirty(false);
    onSaved?.(s);
  };

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setErr(null); setMsg(null); setTestMsg(null); setModelsMsg(null);
    get<LlmStatus>('/api/v1/llm/status').then(s => {
      if (!alive) return;
      const list = s.profiles ?? [];
      const cur = list.find(p => p.id === s.profile_id) ?? list[0];
      setSt(s); setDraft(cur ? draftOf(cur) : EMPTY);
      setApiKey(''); setClearKey(false); setDirty(false);
    }, e => alive && setErr(e));
    return () => { alive = false; };
  }, [open]);

  const call = async (body: Record<string, unknown>) => {
    setBusy(true); setErr(null);
    try {
      const { data } = await post<LlmStatus>('/api/v1/llm/profiles', body, newKey('llm-profile'));
      return data;
    } catch (e) { setErr(e); return null; } finally { setBusy(false); }
  };

  const edit = (patch: Partial<Draft>) => { setDraft(d => ({ ...d, ...patch })); setDirty(true); setMsg(null); setTestMsg(null); };

  const providers = (st?.providers ?? []).filter(p => p.registered);
  const current = providers.find(p => p.id === draft.provider);
  const saved = draft.id ? (st?.profiles ?? []).find(p => p.id === draft.id) : undefined;
  const isActive = Boolean(saved?.active);

  const pick = (p: LlmProfile) => {
    setDraft(draftOf(p)); setApiKey(''); setClearKey(false);
    setMsg(null); setTestMsg(null); setModelsMsg(null); setDirty(false);
  };
  const fresh = () => {
    setDraft(EMPTY); setApiKey(''); setClearKey(false);
    setMsg(null); setTestMsg(null); setModelsMsg(null); setDirty(false);
  };

  const updateModel = (i: number, patch: Partial<LlmModelItem>) => {
    const old = draft.models[i];
    const next = draft.models.map((m, j) => (j === i ? { ...m, ...patch } : m));
    const model = draft.model === old.name ? (patch.name ?? old.name) : draft.model;
    edit({ models: next, model });
  };
  const addModel = () => edit({ models: [...draft.models, { name: '', long_context: false }] });
  const removeModel = (i: number) => {
    const gone = draft.models[i];
    const next = draft.models.filter((_, j) => j !== i);
    edit({ models: next, model: draft.model === gone.name ? '' : draft.model });
  };

  const fetchModels = async () => {
    if (!draft.id) return;
    setBusy(true); setErr(null); setModelsMsg('正在向供应商要模型清单…');
    try {
      const { data } = await post<{ ok: boolean; count?: number; models?: LlmModelItem[]; reason?: string; code?: string }>(
        '/api/v1/llm/models', { id: draft.id }, newKey('llm-models'));
      if (!data.ok) {
        setModelsMsg(`没拿到模型清单：${data.reason ?? data.code ?? '未知原因'}（可以手填「添加模型」）`);
        return;
      }
      const got = data.models ?? [];
      const mine = draft.models.filter(m => m.name && !got.some(g => g.name === m.name));
      const merged = got.map(g => {
        const local = draft.models.find(m => m.name === g.name);
        return { name: g.name, long_context: local?.long_context ?? !!g.long_context };
      });
      edit({ models: [...merged, ...mine], model: draft.model || merged[0]?.name || mine[0]?.name || '' });
      setModelsMsg(`拉到 ${data.count ?? got.length} 个模型${mine.length ? `，另有 ${mine.length} 个是你手填的` : ''}`);
    } catch (e) { setErr(e); setModelsMsg(null); } finally { setBusy(false); }
  };

  const save = async () => {
    const models = draft.models.filter(m => m.name.trim())
      .map(m => ({ name: m.name.trim(), long_context: !!m.long_context }));
    const cur = draft.model.trim();
    if (cur && !models.some(m => m.name === cur)) models.push({ name: cur, long_context: false });
    const body: Record<string, unknown> = {
      action: 'save',
      activate: !draft.id || isActive,       // 新建默认设为当前；改别的存档时不动当前
      profile: {
        id: draft.id ?? undefined,
        name: draft.name.trim() || `${current?.label ?? draft.provider}${draft.model ? ` · ${draft.model}` : ''}`,
        provider: draft.provider,
        model: draft.model.trim(),
        base_url: draft.baseUrl.trim(),
        api_key_env: draft.keyEnv.trim(),
        daily_token_limit: draft.limit.trim() ? Number(draft.limit.trim()) : null,
        json_mode: draft.jsonMode,
        models,
      },
    };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    if (clearKey) body.clear_api_key = true;
    const data = await call(body);
    if (!data) return;
    const savedProf = (data as LlmStatus & { profile?: LlmProfile }).profile;
    apply(data, savedProf?.id ?? data.profile_id);
    setMsg(savedProf && !savedProf.enabled ? `已保存「${savedProf.name}」，但还不能用：${savedProf.reason}`
      : `已保存「${savedProf?.name ?? data.profile_name}」${data.profile_id === savedProf?.id ? '，已设为当前' : ''}`);
  };

  const activate = async () => {
    if (!draft.id) return;
    const data = await call({ action: 'activate', id: draft.id });
    if (!data) return;
    apply(data, draft.id);
    setMsg(`已切换：「${draft.name}」`);
  };

  const duplicate = async () => {
    if (!draft.id) return;
    const data = await call({ action: 'duplicate', id: draft.id });
    if (!data) return;
    const copy = (data as LlmStatus & { profile?: LlmProfile }).profile;
    apply(data, copy?.id);
    setMsg(`已复制「${copy?.name}」`);
  };

  const remove = async () => {
    if (!draft.id || !saved) return;
    if (!window.confirm(`删掉存档「${saved.name}」？它在页面里填过的密钥也会一起删掉。`)) return;
    const data = await call({ action: 'delete', id: draft.id });
    if (!data) return;
    apply(data);
    setMsg(`已删除「${saved.name}」`);
  };

  const test = async () => {
    if (!draft.id) return;
    setBusy(true); setErr(null); setTestMsg('正在试连…');
    try {
      const { data } = await post<{ ok: boolean; ms?: number; model?: string; code?: string; reason?: string }>(
        '/api/v1/llm/test', { id: draft.id }, newKey('llm-test'));
      const why = data.reason ?? data.code ?? '未知原因';
      setTestMsg(data.ok ? `连通正常：${data.model}（${data.ms ?? 0} ms，不超过 16 tokens）`
        : data.code === 'LLM_DISABLED' ? `还不能用：${why}`
        : why.startsWith('连不上') ? why : `连不上：${why}`);
    } catch (e) { setErr(e); setTestMsg(null); } finally { setBusy(false); }
  };

  if (!open) return null;
  const list = st?.profiles ?? [];
  return (
    <Dialog title="模型设置" onClose={onClose} dirty={dirty} wide>
      <div className="llm-cfg" data-testid="llm-settings">
        <aside className="llm-saves" data-testid="llm-profile-list">
          <div className="row spread">
            <strong>保存的模型</strong>
            <span className="note">{list.length}/{st?.profile_limit ?? 20}</span>
          </div>
          {list.map(p => (
            <button key={p.id} type="button" className={`llm-save ${p.id === draft.id ? 'is-sel' : ''}`}
                    onClick={() => pick(p)} data-testid={`llm-profile-${p.id}`}>
              <span className="nm">{p.name}{p.active ? <span className="tag ok">当前</span> : null}</span>
              <span className="sub">{p.label} · {p.model || '默认模型'}</span>
              <span className={`sub ${p.key_configured ? '' : 'warn'}`}>
                {p.key_configured ? `密钥已配置（${p.key_source}）` : `无密钥${p.enabled ? '' : ' · 不可用'}`}</span>
            </button>
          ))}
          {list.length === 0 ? <p className="note">还没有保存的模型。</p> : null}
          <button type="button" className="btn quiet small" onClick={fresh} data-testid="llm-profile-new">＋ 新建一份</button>
        </aside>

        <section className="llm-fields">
          <p className="note" style={{ marginTop: 0 }}>
            当前用：<strong>{st?.profile_name ?? '—'}</strong>
            {st ? `（${st.label ?? st.provider} · ${st.model || '默认模型'}；${st.enabled ? '已启用' : `未启用：${st.reason}`}）` : ' 读取中…'}
            。保存和切换都立即生效，不用重启；密钥只存本机私有文件，接口不会回显。
          </p>
          <div className="form-grid">
            <Field label="存档名" hint="自己看得懂就行，例如「DeepSeek 官方」「公司网关」">
              <input type="text" value={draft.name} placeholder={current?.label ?? ''}
                     onChange={e => edit({ name: e.target.value })} data-testid="llm-name" />
            </Field>
            <Field label="供应商" hint="内置三种；要接新厂商需要在代码注册表里加一个子类">
              <select value={draft.provider} data-testid="llm-provider"
                      onChange={e => edit({ provider: e.target.value, model: '', baseUrl: '', keyEnv: '', models: [] })}>
                {providers.map(p => <option key={p.id} value={p.id}>{p.label}（{p.id}）</option>)}
              </select>
            </Field>
            <Field label="当前模型" hint={current?.default_model ? `留空使用默认 ${current.default_model}` : '在下面的模型列表里选，或直接填一个名字'}>
              <input type="text" value={draft.model} placeholder={current?.default_model ?? ''}
                     onChange={e => edit({ model: e.target.value })} data-testid="llm-model" />
            </Field>
            <Field label="接口地址" hint={current?.default_base_url ? `留空使用 ${current.default_base_url}` : '例如 https://api.openai.com/v1'}>
              <input type="text" value={draft.baseUrl} placeholder={current?.default_base_url ?? ''}
                     onChange={e => edit({ baseUrl: e.target.value })} data-testid="llm-base-url" />
            </Field>
            <Field label="密钥所在环境变量" hint={current?.key_env ? `留空使用 ${current.key_env}` : '例如 OPENAI_API_KEY'}>
              <input type="text" value={draft.keyEnv} placeholder={current?.key_env ?? ''}
                     onChange={e => edit({ keyEnv: e.target.value })} data-testid="llm-key-env" />
            </Field>
            <Field label="每天 token 上限" hint="每份存档各自设上限，但所有存档共用一本当天总账">
              <input type="number" min={1} value={draft.limit} placeholder={String(st?.budget.limit ?? 200000)}
                     onChange={e => edit({ limit: e.target.value })} data-testid="llm-limit" />
            </Field>
          </div>

          <div className="llm-models" data-testid="llm-models">
            <div className="row spread">
              <strong>模型列表</strong>
              <span className="row">
                <button className="btn quiet small" disabled={busy || !draft.id} onClick={fetchModels}
                        data-testid="llm-fetch-models"
                        title={draft.id ? '用这份存档的密钥问供应商要 /models' : '先保存这份存档再拉取'}>获取模型列表</button>
                <button className="btn quiet small" onClick={addModel} data-testid="llm-add-model">＋ 添加模型</button>
              </span>
            </div>
            <p className="note" style={{ marginTop: 0 }}>
              这份存档可用的模型；左边圆点是当前使用的那一个（也可以直接在上面「当前模型」里手填）。
              勾「1M」只是标记它声明支持 100 万上下文，本工作台的提示词远小于这个量级。
            </p>
            {draft.models.length === 0
              ? <p className="empty small">还没有模型：点「获取模型列表」自动拉，或「添加模型」手填一个。</p>
              : null}
            {draft.models.map((m, i) => (
              <div className={`llm-model ${m.name && draft.model === m.name ? 'is-on' : ''}`} key={`${i}-${m.name}`}>
                <input type="radio" name="llm-model-pick" checked={Boolean(m.name) && draft.model === m.name}
                       onChange={() => edit({ model: m.name })} aria-label={`使用 ${m.name || '这个模型'}`} />
                <input type="text" value={m.name} placeholder="模型名，例如 deepseek-flash"
                       onChange={e => updateModel(i, { name: e.target.value })} data-testid={`llm-model-name-${i}`} />
                <label className="check inline">
                  <input type="checkbox" checked={!!m.long_context}
                         onChange={e => updateModel(i, { long_context: e.target.checked })} />1M
                </label>
                <button className="btn quiet small" onClick={() => removeModel(i)} aria-label="删除这个模型"
                        data-testid={`llm-model-del-${i}`}>✕</button>
              </div>
            ))}
            {modelsMsg ? <p className="note" role="status" data-testid="llm-models-msg">{modelsMsg}</p> : null}
          </div>

          <Field label="密钥（可选）" hint="只写到这份存档自己的私有条目（0600）；留空表示不改。也可以用环境变量或 ~/.llm.env">
            <input type="password" autoComplete="off" value={apiKey} data-testid="llm-api-key"
                   onChange={e => { setApiKey(e.target.value); setDirty(true); }}
                   placeholder={saved?.key_configured ? `已配置（${saved.key_source}），留空不改` : '粘贴密钥'} />
          </Field>
          <label className="check"><input type="checkbox" checked={clearKey}
            onChange={e => { setClearKey(e.target.checked); setDirty(true); }} />清除这份存档已保存的密钥</label>
          <label className="check"><input type="checkbox" checked={draft.jsonMode}
            onChange={e => edit({ jsonMode: e.target.checked })} />
            使用 JSON 模式（多数接口支持；不支持的实现会自动退回普通输出再解析）</label>

          <div className="row">
            <button className="btn" disabled={busy} onClick={save} data-testid="llm-save">{busy ? '保存中…' : '保存'}</button>
            <button className="btn quiet" disabled={busy || !draft.id || isActive} onClick={activate} data-testid="llm-activate">
              {isActive ? '已是当前' : '设为当前'}</button>
            <button className="btn quiet" disabled={busy || !draft.id} onClick={test} data-testid="llm-test">试连</button>
            <button className="btn quiet small" disabled={busy || !draft.id} onClick={duplicate} data-testid="llm-duplicate">复制</button>
            <button className="btn quiet small" disabled={busy || !draft.id} onClick={remove} data-testid="llm-delete">删除</button>
          </div>
          {msg ? <p className="note" role="status" data-testid="llm-save-msg">{msg}</p> : null}
          {testMsg ? <p className="note" role="status" data-testid="llm-test-msg">{testMsg}</p> : null}
          <ErrorNote error={err} title={err ? errorText(err) : undefined} />
          <p className="note">
            配置文件：<code>{st?.config_path}</code>{st?.config_saved ? '' : '（还没有保存过，当前是内置默认）'}；
            存档列表在终端里也能操作：<code>python3 tools/llm_config.py profiles</code>。
          </p>
        </section>
      </div>
    </Dialog>
  );
}
