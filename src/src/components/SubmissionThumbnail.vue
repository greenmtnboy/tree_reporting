<template>
  <div class="submission-thumb">
    <img v-if="url" :src="url" alt="Submission photo" class="submission-thumb__img" />
    <div v-else-if="error" class="submission-thumb__placeholder" aria-label="Photo unavailable">
      ✕
    </div>
    <div v-else class="submission-thumb__placeholder" aria-label="Loading photo">…</div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { getSubmissionPhotoUrl } from '../composables/useSubmissions'

const props = defineProps<{ photoPath: string }>()

const url = ref<string | null>(null)
const error = ref(false)

async function load() {
  url.value = null
  error.value = false
  if (!props.photoPath) return
  try {
    url.value = await getSubmissionPhotoUrl(props.photoPath)
  } catch {
    error.value = true
  }
}

onMounted(load)
watch(() => props.photoPath, load)
</script>

<style scoped>
.submission-thumb {
  width: 72px;
  height: 72px;
  flex-shrink: 0;
  background: rgba(167, 227, 178, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1px solid rgba(167, 227, 178, 0.1);
}

.submission-thumb__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.submission-thumb__placeholder {
  color: var(--color-muted);
  font-size: 1rem;
}
</style>
