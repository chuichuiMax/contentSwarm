import assert from 'node:assert/strict'

// 对真实编译包重跑选图链路；微信原生选图界面的视觉效果仍需实测。
assert.ok(process.env.MP_WEIXIN_BUNDLE_DIR, 'Set MP_WEIXIN_BUNDLE_DIR to the compiled mp-weixin directory')
await import('../unit/test_mp_image_selection.mjs')
