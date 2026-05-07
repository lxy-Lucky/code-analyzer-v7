import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useSettingStore = defineStore('setting', () => {
  const lang = ref<'zh' | 'en' | 'ja'>(
    (localStorage.getItem('lang') as 'zh' | 'en' | 'ja') || 'zh',
  )
  const updateBadge = ref(0)

  function setLang(l: 'zh' | 'en' | 'ja') {
    lang.value = l
    localStorage.setItem('lang', l)
  }

  function bumpBadge() {
    updateBadge.value++
  }

  function clearBadge() {
    updateBadge.value = 0
  }

  return { lang, updateBadge, setLang, bumpBadge, clearBadge }
})
