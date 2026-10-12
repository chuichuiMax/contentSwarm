<template>
  <main class="case-share-page">
    <p v-if="loading" class="state">正在加载案例…</p>
    <p v-else-if="error" class="state error">{{ error }}</p>
    <article v-else-if="share" class="case-share">
      <img v-if="firstImage" class="case-image case-cover" :src="imageUrl(firstImage.url)" :alt="firstImage.file_name" />
      <section class="case-details">
        <h1>{{ share.gallery_name }}</h1>
        <div class="project-fields">
          <span v-if="share.building_name" class="project-building">楼盘：{{ share.building_name }}</span>
          <span v-if="formattedArea" class="project-area">面积：{{ formattedArea }}</span>
          <span v-if="share.sharer_name" class="project-contact">联系人：{{ share.sharer_name }}</span>
          <span v-if="share.design_style" class="project-style" :class="{ wide: !share.sharer_name }">
            风格：{{ share.design_style }}
          </span>
          <a v-if="share.sharer_phone" class="share-phone" :href="`tel:${share.sharer_phone}`">
            电话：{{ share.sharer_phone }}
          </a>
        </div>
      </section>
      <img
        v-for="image in remainingImages"
        :key="image.order"
        class="case-image"
        :src="imageUrl(image.url)"
        :alt="image.file_name"
      />
    </article>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { apiGet } from '@/apis/base'
import { apiUrl } from '@/utils/apiUrl'

const route = useRoute()
const loading = ref(true)
const error = ref('')
const share = ref(null)

const firstImage = computed(() => share.value?.images?.[0] || null)
const remainingImages = computed(() => share.value?.images?.slice(1) || [])
const formattedArea = computed(() => {
  const area = share.value?.area || ''
  return area && !area.endsWith('㎡') ? `${area}㎡` : area
})
const imageUrl = (url) => apiUrl(url || '')

onMounted(async () => {
  try {
    const response = await apiGet(`/api/material-library/shares/${encodeURIComponent(route.params.shareId)}`, {}, false)
    share.value = response.share
    document.title = share.value?.gallery_name || '案例分享'
  } catch (requestError) {
    error.value = requestError?.message || '案例不存在或已不可用'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped lang="less">
.case-share-page { min-height: 100vh; background: #f5f4f1; color: #27231f; }
.case-share { max-width: 760px; margin: 0 auto; padding-bottom: 24px; }
.case-image { display: block; width: 100%; height: auto; background: #e7e3dd; }
.case-details { padding: 18px 20px 20px; background: #fff; }
.case-details h1 { margin: 0; font-size: 21px; line-height: 1.35; }
.project-fields { --share-phone-color: #5b7cff; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 16px; margin-top: 12px; color: var(--gray-900); font-size: 15px; line-height: 1.5; }
.project-fields span { min-width: 0; overflow-wrap: anywhere; }
.project-building, .project-contact { grid-column: 1; }
.project-area, .project-style { grid-column: 2; }
.project-style.wide { grid-column: 1 / -1; }
.share-phone { grid-column: 1 / -1; color: var(--share-phone-color); text-decoration: none; overflow-wrap: anywhere; }
.state { margin: 0; padding: 88px 20px; color: #766f67; text-align: center; }
.error { color: #b54b40; }
@media (min-width: 761px) { .case-share { margin-top: 24px; overflow: hidden; border-radius: 10px; box-shadow: 0 4px 22px rgba(49, 37, 26, .08); } }
</style>
