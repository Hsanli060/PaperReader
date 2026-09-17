<script setup lang="ts">
/**
 * 设置页（批①）：用户自带 API Key + 服务地址（OpenAI 兼容端点）。
 *
 * - 每个 provider 两个输入：Key（加密存储、只显尾号）+ 服务地址（明文、可回显）
 * - 服务地址留空 = 用服务器默认（DeepSeek 官方 / 阿里云百炼）
 * - 换运营商（Moonshot / OpenAI / SiliconFlow / 自建中转…）= 换地址即可
 * - 保存 / 测试连接 / 清除；「测试连接」测的永远是你自己的 key，绝不拿服务器 key 冒充
 */
import { onMounted, reactive, ref } from 'vue'
import { apiErrorMessage, fetchMyKeys, saveMyKeys, testMyKeys } from '@/services/api'
import type { KeyTestResult, UserKeysStatus } from '@/types'

type Which = 'llm' | 'emb'

const status = ref<UserKeysStatus | null>(null)
const loading = ref(true)
const banner = ref<{ type: 'ok' | 'err'; text: string } | null>(null)

const llmInput = ref('')
const embInput = ref('')
const llmUrl = ref('')
const embUrl = ref('')
const busy = reactive<{ llm: string; emb: string }>({ llm: '', emb: '' })
const testResult = reactive<{ llm: KeyTestResult | null; emb: KeyTestResult | null }>({ llm: null, emb: null })

async function load() {
  loading.value = true
  try {
    const st = (await fetchMyKeys()).data
    status.value = st
    llmUrl.value = st.llm.base_url ?? ''
    embUrl.value = st.embedding.base_url ?? ''
  } catch (err) {
    banner.value = { type: 'err', text: apiErrorMessage(err) }
  } finally {
    loading.value = false
  }
}
onMounted(load)

function keyOf(which: Which) {
  return which === 'llm' ? llmInput : embInput
}
function urlOf(which: Which) {
  return which === 'llm' ? llmUrl : embUrl
}
function statusOf(which: Which) {
  return which === 'llm' ? status.value?.llm : status.value?.embedding
}
function keyField(which: Which) {
  return which === 'llm' ? 'llm_api_key' : 'embedding_api_key'
}
function urlField(which: Which) {
  return which === 'llm' ? 'llm_base_url' : 'embedding_base_url'
}

/** 组装"有变化的字段"载荷：缺省=不动；空串=清除；非空=保存 */
function buildPayload(which: Which, { includeNewUrl = true } = {}) {
  const payload: Record<string, string> = {}
  const keyVal = keyOf(which).value.trim()
  if (keyVal) payload[keyField(which)] = keyVal
  if (includeNewUrl) {
    const urlVal = urlOf(which).value.trim()
    const origUrl = statusOf(which)?.base_url ?? ''
    if (urlVal !== origUrl) payload[urlField(which)] = urlVal
  }
  return payload
}

async function save(which: Which) {
  const payload = buildPayload(which)
  if (!Object.keys(payload).length) {
    banner.value = { type: 'err', text: '没有改动——先粘贴 Key 或修改服务地址再保存' }
    return
  }
  busy[which] = 'save'
  try {
    const st = (await saveMyKeys(payload)).data
    status.value = st
    keyOf(which).value = ''
    urlOf(which).value = statusOf(which)?.base_url ?? ''
    banner.value = { type: 'ok', text: '已保存（Key 加密存储；界面只显示尾号）——立即生效' }
  } catch (err) {
    banner.value = { type: 'err', text: apiErrorMessage(err) }
  } finally {
    busy[which] = ''
  }
}

async function clearKey(which: Which) {
  const name = which === 'llm' ? 'LLM' : '嵌入'
  if (!window.confirm(`确定清除${name}的 Key 和服务地址？清除后相关功能会被停用。`)) return
  busy[which] = 'clear'
  try {
    const st = (await saveMyKeys({ [keyField(which)]: '', [urlField(which)]: '' } as Record<string, string>)).data
    status.value = st
    keyOf(which).value = ''
    urlOf(which).value = ''
    testResult[which] = null
    banner.value = { type: 'ok', text: `${name} 配置已清除` }
  } catch (err) {
    banner.value = { type: 'err', text: apiErrorMessage(err) }
  } finally {
    busy[which] = ''
  }
}

async function test(which: Which) {
  // 测试载荷：输入框有值就带上（不落库）；没动过的地址字段不带（让后端用已保存的）
  const payload: Record<string, string> = {}
  const keyVal = keyOf(which).value.trim()
  if (keyVal) payload[keyField(which)] = keyVal
  const urlVal = urlOf(which).value.trim()
  if (urlVal) payload[urlField(which)] = urlVal

  busy[which] = 'test'
  testResult[which] = null
  try {
    const resp = await testMyKeys(payload)
    testResult[which] = which === 'llm' ? resp.data.llm : resp.data.embedding
  } catch (err) {
    testResult[which] = { ok: false, error: apiErrorMessage(err) }
  } finally {
    busy[which] = ''
  }
}
</script>

<template>
  <div class="settings-page">
    <h1 class="page-title">⚙️ 设置</h1>

    <div class="intro pr-card">
      <h2>自带 API Key + 服务地址</h2>
      <p>
        PaperReader 不提供共享 Key——请填入你自己的 Key：问答与论文处理都走你自己的账户。
        Key 会<b>加密存储</b>，界面只显示尾号；服务地址明文保存（要能回显给你看）。
      </p>
      <ul class="intro-list">
        <li>
          <b>LLM Key</b>（问答/摘要/引用）：任何 <b>OpenAI 兼容</b>的服务都行——
          DeepSeek、Moonshot、OpenAI、SiliconFlow……填对应服务地址即可；留空用默认。
        </li>
        <li>
          <b>嵌入 Key</b>（向量化/检索/精排）：需兼容 OpenAI Embeddings 接口；
          用阿里云百炼时留空默认地址即可（精排 qwen3-rerank 同 Key 同地址）。
        </li>
      </ul>
    </div>

    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>
    <div v-if="loading" class="loading">加载中…</div>

    <template v-else-if="status">
      <!-- LLM Key -->
      <div class="key-card pr-card">
        <div class="key-head">
          <h3>LLM Key</h3>
          <span class="chip" :class="status.llm.configured ? 'on' : 'off'">
            {{ status.llm.configured ? `已配置 · 尾号 ${status.llm.tail}` : '未配置' }}
          </span>
        </div>
        <div class="key-row">
          <input v-model="llmInput" type="password" class="key-input"
                 placeholder="粘贴你的 LLM Key（sk-…）" autocomplete="off" />
          <button class="btn primary" :disabled="!!busy.llm" @click="save('llm')">
            {{ busy.llm === 'save' ? '保存中…' : '保存' }}
          </button>
          <button class="btn" :disabled="!!busy.llm" @click="test('llm')">
            {{ busy.llm === 'test' ? '测试中…' : '测试连接' }}
          </button>
          <button class="btn danger" :disabled="!!busy.llm || !status.llm.configured" @click="clearKey('llm')">
            清除
          </button>
        </div>
        <div class="url-row">
          <span class="url-label">服务地址</span>
          <input v-model="llmUrl" type="text" class="url-input" spellcheck="false" autocomplete="off"
                 placeholder="留空 = 默认（DeepSeek 官方）；可填任意 OpenAI 兼容端点，如 https://api.moonshot.cn/v1" />
        </div>
        <div v-if="testResult.llm" class="test-result" :class="testResult.llm.ok ? 'ok' : 'err'">
          {{ testResult.llm.ok ? '✓ 连接成功，Key 有效（测试不落库）' : `✗ ${testResult.llm.error ?? '测试失败'}` }}
        </div>
      </div>

      <!-- 嵌入 Key -->
      <div class="key-card pr-card">
        <div class="key-head">
          <h3>嵌入 Key</h3>
          <span class="chip" :class="status.embedding.configured ? 'on' : 'off'">
            {{ status.embedding.configured ? `已配置 · 尾号 ${status.embedding.tail}` : '未配置' }}
          </span>
        </div>
        <div class="key-row">
          <input v-model="embInput" type="password" class="key-input"
                 placeholder="粘贴你的嵌入 Key（sk-…）" autocomplete="off" />
          <button class="btn primary" :disabled="!!busy.emb" @click="save('emb')">
            {{ busy.emb === 'save' ? '保存中…' : '保存' }}
          </button>
          <button class="btn" :disabled="!!busy.emb" @click="test('emb')">
            {{ busy.emb === 'test' ? '测试中…' : '测试连接' }}
          </button>
          <button class="btn danger" :disabled="!!busy.emb || !status.embedding.configured" @click="clearKey('emb')">
            清除
          </button>
        </div>
        <div class="url-row">
          <span class="url-label">服务地址</span>
          <input v-model="embUrl" type="text" class="url-input" spellcheck="false" autocomplete="off"
                 placeholder="留空 = 默认（阿里云百炼兼容端点）；需支持 OpenAI Embeddings 接口" />
        </div>
        <div v-if="testResult.emb" class="test-result" :class="testResult.emb.ok ? 'ok' : 'err'">
          {{ testResult.emb.ok ? '✓ 连接成功，Key 有效（测试不落库）' : `✗ ${testResult.emb.error ?? '测试失败'}` }}
        </div>
      </div>

      <!-- 安全说明 -->
      <div class="notes pr-card">
        <h3>安全说明</h3>
        <ul>
          <li>Key 以 Fernet 密文存储（密钥由服务端配置派生），数据库里没有明文</li>
          <li>Key 接口与页面永远只返回尾号；服务地址非机密，可回显</li>
          <li>你的配置只用于你自己的请求（问答 / 添加论文 / 摘要），不与其它用户共享</li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.settings-page {
  max-width: 760px;
  margin: 0 auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.page-title { margin: 0; font-size: 22px; }

.intro h2 { margin: 0 0 8px; font-size: 16px; }
.intro p { margin: 0 0 10px; font-size: 13px; line-height: 1.7; color: var(--color-text-secondary); }
.intro-list { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; color: var(--color-text-secondary); }

.banner {
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
}
.banner.ok { background: #eefaf5; color: #0a7d4f; border: 1px solid #bfe8d6; }
.banner.err { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
.loading { color: var(--color-text-secondary); padding: 20px 0; }

.key-card { display: flex; flex-direction: column; gap: 10px; }
.key-head { display: flex; align-items: center; justify-content: space-between; }
.key-head h3 { margin: 0; font-size: 15px; }
.chip {
  font-size: 12px;
  border-radius: 10px;
  padding: 2px 10px;
}
.chip.on { background: #eefaf5; color: #0a7d4f; border: 1px solid #bfe8d6; }
.chip.off { background: var(--color-code-bg); color: var(--color-text-secondary); border: 1px solid var(--color-border); }

.key-row { display: flex; gap: 8px; flex-wrap: wrap; }
.key-input {
  flex: 1;
  min-width: 260px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  font-family: var(--font-mono);
  outline: none;
}
.key-input:focus { border-color: var(--color-primary); }

.url-row { display: flex; align-items: center; gap: 10px; }
.url-label { font-size: 12px; color: var(--color-text-secondary); white-space: nowrap; }
.url-input {
  flex: 1;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 7px 12px;
  font-size: 12px;
  font-family: var(--font-mono);
  outline: none;
  color: var(--color-text);
  background: var(--color-surface);
}
.url-input:focus { border-color: var(--color-primary); }
.url-input::placeholder { font-family: var(--font-sans); }

.btn {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 8px;
  padding: 8px 16px;
  font-size: 13px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { border-color: var(--color-primary); color: var(--color-primary); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}
.btn.primary:hover:not(:disabled) { background: var(--color-primary-hover); color: #fff; }
.btn.danger:hover:not(:disabled) { border-color: #dc2626; color: #dc2626; }

.test-result { font-size: 13px; }
.test-result.ok { color: #0a7d4f; }
.test-result.err { color: #b91c1c; word-break: break-all; }

.notes h3 { margin: 0 0 8px; font-size: 15px; }
.notes ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; color: var(--color-text-secondary); }
</style>
