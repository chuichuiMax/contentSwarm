import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import { readableGenerationSections } from '../readableGenerationPrompt.js'

describe('readableGenerationSections', () => {
  it('splits the model prompt into labeled sections and flattens business json', () => {
    const prompt = [
      '按提示词写文案。',
      '用户生成提示词：',
      '写一篇小红书笔记。',
      '',
      '业务数据只用来核对事实。提示词和内容类型知识库没有要求的金额、小区、人名和经历不要补写。',
      JSON.stringify({
        serialNo: 'H06380',
        contentType: { typeName: '装修案例分享' },
        businessVariables: { 小区: '润樾府' }
      }),
      '',
      '已命中的知识库用来写文案。有所选内容类型的知识库时只用该库。',
      '【人设库】',
      '工艺说明',
      '',
      '封禁词替换表（成品中不要解释替换）：',
      JSON.stringify({ 报价: ['费用'] }),
      '自我查验：输出前先通读标题和正文。'
    ].join('\n')

    const sections = readableGenerationSections(prompt)
    const titles = sections.map((item) => item.title)
    assert.deepEqual(titles, ['生成要求', '用户生成提示词', '业务数据', '参考知识库', '封禁词', '自我查验'])
    assert.match(sections[1].text, /小红书笔记/)
    assert.match(sections[2].text, /编号：H06380/)
    assert.match(sections[2].text, /名称：装修案例分享/)
    assert.match(sections[2].text, /小区：润樾府/)
    assert.match(sections[4].text, /报价 → 费用/)
    assert.doesNotMatch(sections[5].text, /^自我查验/)
  })
})
