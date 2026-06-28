import { createApp } from 'vue'
import './assets/tailwind.css'
import './assets/layout.css'

import App from './App.vue'
import router from './router'
import { initDarkMode } from './composables/useDarkMode'

initDarkMode()

const app = createApp(App)
app.use(router)
app.mount('#app')
