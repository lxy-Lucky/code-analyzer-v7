import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'
import ja from './ja'

const saved = localStorage.getItem('lang') || 'zh'

export const i18n = createI18n({
  legacy: false,
  locale: saved,
  fallbackLocale: 'zh',
  messages: { zh, en, ja },
})
