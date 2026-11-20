<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { deleteDoc, doc, getDocFromServer, setDoc, type Firestore } from 'firebase/firestore'

const props = defineProps<{ uid: string; firestore: Firestore }>()
const preference = doc(props.firestore, 'contributorPreferences', props.uid)
const share = ref(false)
const name = ref('')
const ready = ref(false)
const busy = ref(false)
const message = ref('')

async function load() {
  busy.value = true
  message.value = ''
  try {
    const saved = (await getDocFromServer(preference)).data()
    share.value = saved?.shareName === true
    name.value = share.value && typeof saved?.publicName === 'string' ? saved.publicName : ''
    ready.value = true
  } catch {
    message.value = 'Could not load your credit preference.'
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!ready.value || busy.value || (share.value && !name.value.trim())) return
  busy.value = true
  message.value = ''
  try {
    if (share.value) {
      name.value = name.value.trim()
      await setDoc(preference, { shareName: true, publicName: name.value })
    } else {
      await deleteDoc(preference)
      name.value = ''
    }
    message.value = 'Saved. Your choice takes effect in the next daily report.'
  } catch {
    message.value = 'Could not save your credit preference. Try again.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <form class="credit" @submit.prevent="save">
    <fieldset :disabled="!ready || busy">
      <legend>Public contributor credit</legend>
      <p>Unless you share a name, the arborary.world contributor list only counts your trees in one combined total for all anonymous contributors.</p>
      <label><input v-model="share" type="checkbox" @change="message = ''"> Share a name beside my totals</label>
      <label v-if="share">Public name <input v-model="name" type="text" maxlength="60" required aria-describedby="credit-name-hint" @input="message = ''"></label>
      <p v-if="share" id="credit-name-hint" class="hint">Shown publicly beside your own totals. Use a nickname or first name, not an email address.</p>
      <button type="submit" :disabled="share && !name.trim()">Save preference</button>
    </fieldset>
    <p v-if="message" role="status">{{ message }}</p>
    <button v-if="!ready && !busy" type="button" @click="load">Retry loading preference</button>
  </form>
</template>

<style scoped>
.credit fieldset { display: grid; gap: .7rem; margin: 0; padding: 1rem; border: 1px solid var(--color-moss); }
.credit legend { padding: 0 .3rem; font-weight: 600; }
.credit p { margin: 0; }
.credit .hint { font-size: .85em; opacity: .8; }
.credit button { justify-self: start; padding: .4rem .7rem; cursor: pointer; }
.credit input[type="text"] { max-width: 100%; padding: .3rem; }
</style>
