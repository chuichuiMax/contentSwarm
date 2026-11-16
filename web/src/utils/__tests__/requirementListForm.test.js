import assert from 'node:assert/strict'
import test from 'node:test'

import { completionOf } from '../requirementListForm.js'

test('completion counts filled answers against every field', () => {
  assert.deepEqual(completionOf(), { filled: 0, total: 25, percent: 0 })
  assert.equal(completionOf({ customerName: '刘洋' }).percent, 4)
  assert.equal(completionOf({ answers: { q3_other: '备注' } }).percent, 4)
  const answers = {
    q1: '平层',
    q2_area: '120',
    q2_layout: '三室',
    q3: '婚房',
    q4: '1个月内',
    q5: '15万以内',
    q6: '男性',
    q7: ['简约现代'],
    q8: '全部保留',
    q9: ['夫(妻)'],
    q10: '医生',
    q11: '满顶',
    q12: ['书房'],
    q13: '封闭式',
    q14: '未养宠',
    q15: ['喝茶'],
    q16: '全屋分体空调',
    q17_0: '洗地机',
    q17_1: '管线机',
    q17_2: '全屋智能',
    q17_3: 'AP',
    q18: '一般'
  }
  assert.equal(completionOf({ answers, customerName: '刘洋', phone: '13800001111', address: '长沙' }).percent, 100)
})
