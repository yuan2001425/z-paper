import { ref, watch } from 'vue'

const KEY = 'zpaper_default_domain'
const defaultDomain = ref(localStorage.getItem(KEY) || '')

watch(defaultDomain, (val) => {
  if (val) localStorage.setItem(KEY, val)
  else localStorage.removeItem(KEY)
})

export function useDefaultDomain() {
  return { defaultDomain }
}
