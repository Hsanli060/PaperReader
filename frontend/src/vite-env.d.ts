/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  // Vue 单文件组件对 TS 是"不认识的后缀"，这里声明：
  // 任何 .vue 导入都当作一个组件构造器，props 类型放宽为 any
  const component: DefineComponent<{}, {}, any>
  export default component
}
