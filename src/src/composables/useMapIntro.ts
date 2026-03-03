import { ref } from 'vue'

const introComplete = ref(false)

export function useMapIntro() {
  function setIntroComplete() {
    introComplete.value = true
  }

  return { introComplete, setIntroComplete }
}
