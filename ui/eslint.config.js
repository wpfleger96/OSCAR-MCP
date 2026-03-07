import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import vuePrettierConfig from '@vue/eslint-config-prettier'

export default defineConfigWithVueTs(
    { name: 'app/files-to-lint', files: ['**/*.{ts,vue}'] },
    { name: 'app/files-to-ignore', ignores: ['dist/**'] },
    js.configs.recommended,
    ...pluginVue.configs['flat/essential'],
    vueTsConfigs.recommended,
    vuePrettierConfig,
)
