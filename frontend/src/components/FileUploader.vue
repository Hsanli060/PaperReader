<script setup lang="ts">
/**
 * arXiv 输入 + PDF 上传：两种入库方式。
 * 加载态由父组件控制（提交期间禁用），本组件只负责收集输入和触发事件。
 */
import { ref } from 'vue'

const emit = defineEmits<{
  (e: 'add-arxiv', input: string): void
  (e: 'upload', file: File): void
}>()

const arxivInput = ref('')

function submitArxiv() {
  const v = arxivInput.value.trim()
  if (!v) return
  emit('add-arxiv', v)
  arxivInput.value = ''
}

/** 原生 <input type=file> 的 change：取第一个文件转发，然后清空 input
 *  （不清的话选同一个文件第二次不触发 change） */
function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('upload', file)
  input.value = ''
}
</script>

<template>
  <div class="uploader">
    <div class="arxiv-row">
      <input
        v-model="arxivInput"
        class="arxiv-input"
        placeholder="粘贴 arXiv 链接或 ID，如 2312.00752"
        @keyup.enter="submitArxiv"
      />
      <button class="add-btn" @click="submitArxiv">添加</button>
    </div>
    <div class="upload-row">
      <label class="upload-btn">
        ⬆ 上传本地 PDF
        <input
          type="file"
          accept=".pdf"
          hidden
          @change="onFilePicked"
        />
      </label>
      <span class="upload-hint">大 PDF 解析要几分钟，请耐心等待</span>
    </div>
  </div>
</template>

<style scoped>
.uploader { display: flex; flex-direction: column; gap: 10px; }
.arxiv-row { display: flex; gap: 8px; }
.arxiv-input {
  flex: 1;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  outline: none;
  background: var(--color-surface);
}
.arxiv-input:focus { border-color: var(--color-primary); }
.add-btn {
  border: none;
  background: var(--color-primary);
  color: #fff;
  border-radius: 8px;
  padding: 8px 18px;
  cursor: pointer;
  font-size: 13px;
}
.add-btn:hover { background: var(--color-primary-hover); }

.upload-row { display: flex; align-items: center; gap: 10px; }
.upload-btn {
  display: inline-block;
  border: 1px dashed var(--color-primary);
  color: var(--color-primary);
  border-radius: 8px;
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.upload-btn:hover { background: #eef6f5; }
.upload-hint { font-size: 12px; color: var(--color-text-secondary); }
</style>
