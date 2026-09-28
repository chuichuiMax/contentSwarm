<template>
  <div class="basic-settings-section">
    <template v-if="userStore.isAdmin">
      <div class="section-title">默认项配置</div>
      <div class="settings-panel">
        <template v-if="userStore.isSuperAdmin">
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">{{ items?.default_model?.des || '默认对话模型' }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleChatModelSelect"
                  :model_spec="configStore.config?.default_model"
                  placeholder="请选择默认模型"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.fast_model?.des }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleFastModelSelect"
                  :model_spec="configStore.config?.fast_model"
                  placeholder="请选择模型"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">{{ items?.embed_model?.des }}</div>
              <div class="setting-content">
                <EmbeddingModelSelector
                  :value="configStore.config?.embed_model"
                  @change="handleChange('embed_model', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.reranker?.des }}</div>
              <div class="setting-content">
                <RerankModelSelector
                  :value="configStore.config?.reranker"
                  @change="handleChange('reranker', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
          </div>
        </template>
      </div>

      <template v-if="userStore.isSuperAdmin">
        <div class="section-title">内容审查配置</div>
        <div class="section">
          <div class="card">
            <span class="label">{{ items?.enable_content_guard?.des }}</span>
            <a-switch
              :checked="configStore.config?.enable_content_guard"
              @change="handleChange('enable_content_guard', $event)"
            />
          </div>
          <div class="card" v-if="configStore.config?.enable_content_guard">
            <span class="label">{{ items?.enable_content_guard_llm?.des }}</span>
            <a-switch
              :checked="configStore.config?.enable_content_guard_llm"
              @change="handleChange('enable_content_guard_llm', $event)"
            />
          </div>
          <div
            class="card card-select"
            v-if="
              configStore.config?.enable_content_guard &&
              configStore.config?.enable_content_guard_llm
            "
          >
            <span class="label">{{ items?.content_guard_llm_model?.des }}</span>
            <ModelSelectorComponent
              @select-model="handleContentGuardModelSelect"
              :model_spec="configStore.config?.content_guard_llm_model"
              placeholder="请选择模型"
            />
          </div>
        </div>
      </template>
    </template>

    <template v-if="userStore.isSuperAdmin">
      <div class="section-title">当家回调配置</div>
      <div class="settings-panel dangjia-settings-panel">
        <p class="section-description">
          配置当家内容生成完成后的通知地址与最终封面公开地址。保存后立即生效，环境变量作为未配置时的兜底。
        </p>
        <div class="setting-row">
          <div class="setting-label">
            {{ items?.dangjia_callback_base_url?.des || '当家内容生成结果回调地址' }}
          </div>
          <a-input
            v-model:value="dangjiaForm.callbackBaseUrl"
            placeholder="例如：http://test-mgr.example.com:8005"
          />
        </div>
        <div class="setting-row">
          <div class="setting-label">
            {{ items?.dangjia_callback_api_key?.des || '当家内容生成结果回调 API Key' }}
          </div>
          <a-input-password
            v-model:value="dangjiaForm.callbackApiKey"
            :placeholder="
              configStore.config?.dangjia_callback_api_key_configured
                ? '已配置；留空表示保持不变'
                : '请输入回调 API Key'
            "
            autocomplete="new-password"
          />
        </div>
        <div class="setting-row">
          <div class="setting-label">
            {{ items?.dangjia_media_public_base_url?.des || '当家最终封面公开地址' }}
          </div>
          <a-input
            v-model:value="dangjiaForm.mediaPublicBaseUrl"
            placeholder="例如：https://content.example.com/api/dangjia/content/media"
          />
        </div>
        <div class="settings-actions">
          <a-button type="primary" :loading="dangjiaSaving" @click="saveDangjiaSettings">
            保存当家配置
          </a-button>
        </div>
      </div>
    </template>

    <!-- 服务链接部分 -->
    <div v-if="userStore.isAdmin" class="section-title">服务链接</div>
    <div v-if="userStore.isAdmin">
      <p class="section-description">
        快速访问系统相关的外部服务，需要将 localhost 替换为实际的 IP 地址。
      </p>
      <div class="services-grid">
        <div class="service-link-card">
          <div class="service-info">
            <h4>Neo4j 浏览器</h4>
            <p>图数据库管理界面</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:7474/')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>API 接口文档</h4>
            <p>系统接口文档和调试工具</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:5050/docs')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>MinIO 对象存储</h4>
            <p>文件存储管理控制台</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9001')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>

        <div class="service-link-card">
          <div class="service-info">
            <h4>Milvus WebUI</h4>
            <p>向量数据库管理界面</p>
          </div>
          <a-button
            type="default"
            class="lucide-icon-btn"
            @click="openLink('http://localhost:9091/webui/')"
            :icon="h(Globe, { size: 18 })"
          >
            访问
          </a-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import { Globe } from 'lucide-vue-next'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import RerankModelSelector from '@/components/RerankModelSelector.vue'

const configStore = useConfigStore()
const userStore = useUserStore()
const items = computed(() => configStore.config?._config_items || {})
const dangjiaSaving = ref(false)
const dangjiaForm = reactive({
  callbackBaseUrl: '',
  callbackApiKey: '',
  mediaPublicBaseUrl: ''
})

watch(
  () => [
    configStore.config?.dangjia_callback_base_url,
    configStore.config?.dangjia_media_public_base_url
  ],
  ([callbackBaseUrl, mediaPublicBaseUrl]) => {
    dangjiaForm.callbackBaseUrl = callbackBaseUrl || ''
    dangjiaForm.mediaPublicBaseUrl = mediaPublicBaseUrl || ''
    dangjiaForm.callbackApiKey = ''
  },
  { immediate: true }
)

const handleChange = (key, e) => {
  configStore.setConfigValue(key, e)
}

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('fast_model', spec)
  }
}

const handleContentGuardModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('content_guard_llm_model', spec)
  }
}

const isHttpUrl = (value) => {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol)
  } catch {
    return false
  }
}

const saveDangjiaSettings = async () => {
  const callbackBaseUrl = dangjiaForm.callbackBaseUrl.trim()
  const callbackApiKey = dangjiaForm.callbackApiKey.trim()
  const mediaPublicBaseUrl = dangjiaForm.mediaPublicBaseUrl.trim()
  if (!isHttpUrl(callbackBaseUrl) || !isHttpUrl(mediaPublicBaseUrl)) {
    message.warning('请输入有效的 HTTP 或 HTTPS 地址')
    return
  }
  if (!callbackApiKey && !configStore.config?.dangjia_callback_api_key_configured) {
    message.warning('请输入回调 API Key')
    return
  }

  dangjiaSaving.value = true
  try {
    const values = {
      dangjia_callback_base_url: callbackBaseUrl,
      dangjia_media_public_base_url: mediaPublicBaseUrl
    }
    if (callbackApiKey) values.dangjia_callback_api_key = callbackApiKey
    await configStore.setConfigValues(values)
    dangjiaForm.callbackApiKey = ''
    message.success('当家回调配置已保存')
  } catch (error) {
    message.error(error.message || '当家回调配置保存失败')
  } finally {
    dangjiaSaving.value = false
  }
}

const openLink = (url) => {
  window.open(url, '_blank')
}
</script>

<style lang="less" scoped>
.basic-settings-section {
  .section {
    background-color: var(--gray-0);
    padding: 10px 16px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    border: 1px solid var(--gray-150);
  }

  .settings-panel {
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .dangjia-settings-panel {
    .section-description {
      margin-bottom: 2px;
    }
  }

  .settings-actions {
    display: flex;
    justify-content: flex-end;
  }

  .setting-row {
    display: flex;
    flex-direction: column;
    gap: 8px;

    &.two-cols {
      flex-direction: row;
      gap: 20px;
    }

    .col-item {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
  }

  .setting-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-700);
  }

  .setting-content {
    width: 100%;

    .full-width {
      width: 100%;
    }
  }

  .card {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .label {
      margin-right: 20px;
      font-weight: 500;
      color: var(--gray-800);
      flex-shrink: 0;
      min-width: 140px;
    }

    &.card-select {
      align-items: flex-start;
      gap: 12px;

      .label {
        margin-right: 0;
        margin-top: 6px;
      }
    }
  }

  .agent-select {
    width: 320px;
    max-width: 100%;
  }

  .services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }

  .service-link-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    transition: all 0.2s;
    min-height: 70px;

    &:hover {
      box-shadow: 0 1px 8px var(--gray-150);
      border-color: var(--gray-100);
    }

    .service-info {
      flex: 1;
      margin-right: 16px;

      h4 {
        margin: 0 0 4px 0;
        color: var(--gray-900);
        font-size: 15px;
        font-weight: 500;
      }

      p {
        margin: 0;
        color: var(--gray-600);
        font-size: 13px;
        line-height: 1.4;
      }
    }
  }

  @media (max-width: 768px) {
    .agent-select {
      width: 100%;
    }
  }
}
</style>
