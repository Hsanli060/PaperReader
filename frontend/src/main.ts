/**
 * 应用入口：装配三件套。
 * 装配顺序有讲究：pinia 先于 router——路由守卫和页面里都用 useXxxStore()，
 * store 依赖 pinia 先就位；mount 最后（一切就绪了才把组件树画上屏幕）。
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './styles/global.css'

const app = createApp(App)
app.use(createPinia())   // 状态管理
app.use(router)          // 路由
app.mount('#app')        // 挂载
