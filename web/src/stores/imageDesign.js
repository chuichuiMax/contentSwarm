import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { imageDesignApi } from '@/apis/image_design_api'

export const useImageDesignStore = defineStore('imageDesign', () => {
  const bootstrap = ref(null)
  const materials = ref([])
  const jobs = ref([])
  const results = ref([])
  const activeJob = ref(null)
  const analyses = ref({})
  const activeRefinement = ref(null)
  const loading = ref(false)
  const image2Ready = computed(() => Boolean(bootstrap.value?.image2?.configured))

  async function loadBootstrap() {
    if (bootstrap.value) return bootstrap.value
    bootstrap.value = await imageDesignApi.getBootstrap()
    return bootstrap.value
  }

  async function loadResults(clientId = null) {
    const response = await imageDesignApi.listResults(clientId)
    results.value = response.results || []
    return results.value
  }

  async function loadJobs(clientId = null) {
    const response = await imageDesignApi.listJobs(clientId)
    jobs.value = response.jobs || []
    return jobs.value
  }

  async function submit(payload) {
    loading.value = true
    try {
      const response = await imageDesignApi.generate(payload)
      activeJob.value = response.job
      const index = jobs.value.findIndex((item) => item.id === response.job.id)
      if (index >= 0) jobs.value.splice(index, 1, response.job)
      else jobs.value.unshift(response.job)
      return response.job
    } finally {
      loading.value = false
    }
  }

  async function analyze(materialItemId, role) {
    const key = `${materialItemId}:${role}`
    analyses.value = { ...analyses.value, [key]: { status: 'running', role } }
    try {
      const response = await imageDesignApi.createAnalysis(materialItemId, role)
      let analysis = response.analysis
      analyses.value = { ...analyses.value, [key]: analysis }
      for (let attempt = 0; analysis?.status === 'running' && attempt < 90; attempt += 1) {
        await new Promise((resolve) => globalThis.setTimeout(resolve, 1000))
        analysis = (await imageDesignApi.getAnalysis(analysis.id)).analysis
        analyses.value = { ...analyses.value, [key]: analysis }
      }
      if (analysis?.status === 'running') throw new Error('图片视觉分析仍在进行，请稍后重试')
      if (analysis?.status === 'failed') throw new Error(analysis.error_message || '图片视觉分析失败')
      return analysis
    } catch (error) {
      analyses.value = {
        ...analyses.value,
        [key]: { status: 'failed', role, error_message: error.message || '图片视觉分析失败' }
      }
      throw error
    }
  }

  async function refine(payload) {
    const response = await imageDesignApi.createRefinement(payload)
    activeRefinement.value = response.refinement
    return response.refinement
  }

  function invalidateRefinement() {
    activeRefinement.value = null
  }

  async function poll(jobId) {
    const response = await imageDesignApi.getJob(jobId)
    activeJob.value = response.job
    const index = jobs.value.findIndex((item) => item.id === jobId)
    if (index >= 0) jobs.value.splice(index, 1, response.job)
    else jobs.value.unshift(response.job)
    return response.job
  }

  async function removeResult(assetId) {
    await imageDesignApi.deleteResult(assetId)
    results.value = results.value.filter((item) => item.id !== assetId)
  }

  return {
    bootstrap,
    materials,
    jobs,
    results,
    activeJob,
    analyses,
    activeRefinement,
    loading,
    image2Ready,
    loadBootstrap,
    loadResults,
    loadJobs,
    submit,
    analyze,
    refine,
    invalidateRefinement,
    poll,
    removeResult
  }
})
