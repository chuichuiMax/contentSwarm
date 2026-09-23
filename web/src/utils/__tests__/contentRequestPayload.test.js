import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'

import { buildContentRequestPayload, formatContentRequestJson } from '../contentRequestPayload.js'

describe('buildContentRequestPayload', () => {
  it('builds a pre-production pack from employee, content type and business variables', () => {
    const payload = buildContentRequestPayload({
      employee: {
        name: '朱穆',
        employee_code: 'H06380',
        login_account: '17872384482',
        gender: 'female',
        age: 27,
        role: '新媒体运营',
        current_branch: '家装事业部',
        current_department: '渠道管理中心/网络获客部'
      },
      user: { username: 'admin', uid: 'admin', role: 'admin' },
      contentType: { id: '6c79d8ca-1774-4e79-a622-213104f1e7b8', name: '工艺施工展示' },
      businessVariables: {
        居住人口: '三口之家',
        工艺类型: '个性定制系统',
        工艺名称: 'HYB-吊顶与背景墙造型实现工艺',
        楼盘信息: '洋湖天街',
        项目阶段: '拆改阶段',
        外框面积: ''
      }
    })

    assert.deepEqual(payload, {
      serialNo: 'H06380',
      contentType: {
        typeName: '工艺施工展示',
        contentTypeId: '6c79d8ca-1774-4e79-a622-213104f1e7b8',
        contentTypeCode: 'CT05'
      },
      persona: {
        name: '朱穆',
        employeeCode: 'H06380',
        loginAccount: '17872384482',
        gender: '女',
        age: '27',
        role: '新媒体运营',
        currentBranch: '家装事业部',
        currentDepartment: '渠道管理中心/网络获客部'
      },
      businessVariables: {
        居住人口: '三口之家',
        工艺类型: '个性定制系统',
        工艺名称: 'HYB-吊顶与背景墙造型实现工艺',
        楼盘信息: '洋湖天街',
        项目阶段: '拆改阶段'
      },
      facts: {
        persona_fact: '朱穆，27岁，新媒体运营，家装事业部渠道管理中心/网络获客部，工号H06380。',
        process: ['个性定制系统', 'HYB-吊顶与背景墙造型实现工艺'],
        advantage: [
          '个性定制系统；HYB-吊顶与背景墙造型实现工艺',
          '项目施工鸿扬家装',
          '鸿扬家装定制化家装与透明施工'
        ],
        result: '拆改阶段按工艺规范落实HYB-吊顶与背景墙造型实现工艺',
        product: '洋湖天街定制化家装项目',
        location: '洋湖天街',
        scene: '拆改阶段',
        audience: ['三口之家'],
        pain: '业主关心个性定制系统是否规范、细节是否到位、会不会走过场'
      }
    })
  })

  it('falls back to the logged-in user when no employee is bound', () => {
    const payload = buildContentRequestPayload({
      user: {
        username: '张文杰',
        uid: 'zwj',
        phoneNumber: '15251638888',
        role: 'superadmin',
        departmentName: '默认部门'
      },
      contentType: { id: 'nrlx-2', name: '装修报价清单' },
      businessVariables: { 外框面积: '110-130㎡' }
    })

    assert.equal(payload.serialNo, 'zwj')
    assert.equal(payload.persona.name, '张文杰')
    assert.equal(payload.persona.currentDepartment, '默认部门')
    assert.deepEqual(payload.contentType, {
      typeName: '装修报价清单',
      contentTypeId: 'nrlx-2',
      contentTypeCode: 'CT02'
    })
    assert.match(payload.facts.quantity, /^\d+㎡$/)
    assert.notEqual(payload.facts.quantity, '110-130㎡')
    assert.equal(payload.facts.quote_type, 'budget')
    assert.deepEqual(payload.facts.process, ['定制化家装交付'])
    assert.equal(payload.facts.persona_fact, '张文杰，superadmin，默认部门。')
    assert.match(formatContentRequestJson({ user: { uid: 'zwj' } }), /"serialNo": "zwj"/)
  })

  it('maps quotation-list fees to price facts instead of process', () => {
    const payload = buildContentRequestPayload({
      employee: {
        name: '朱穆',
        employee_code: 'H06380',
        login_account: '17872384482',
        gender: 'female',
        age: 27,
        role: '管理员',
        current_branch: '家装事业部',
        current_department: '渠道管理中心/网络获客部'
      },
      contentType: { id: '5cbde95f-7cff-4ab3-8ba7-3d67d7326034', name: '装修报价清单' },
      businessVariables: {
        外框面积: '130-150㎡',
        基础: '12万',
        木制品: '6万',
        居住人口: '五口之家',
        楼盘信息: '洋湖天旭',
        项目阶段: '拆改阶段'
      }
    })

    assert.deepEqual(payload.facts.price, ['基础 12万', '木制品 6万'])
    assert.equal(payload.facts.quote_type, 'budget')
    assert.deepEqual(payload.facts.process, ['定制化家装交付'])
    assert.match(payload.facts.quantity, /^\d+㎡$/)
    assert.ok(!payload.facts.quantity.includes('-'))
    assert.ok(payload.facts.result.includes(payload.facts.quantity))
    assert.equal(payload.facts.product, '洋湖天旭定制化家装项目')
    assert.ok(!payload.facts.quote_block)
    assert.ok(!payload.facts.title_price)
  })

  it('maps case-share pack to CT01 audience/process/result without dumping style or fees into process', () => {
    const payload = buildContentRequestPayload({
      employee: {
        name: '朱穆',
        employee_code: 'H06380',
        login_account: '17872384482',
        gender: 'female',
        age: 27,
        role: '管理员',
        current_branch: '家装事业部',
        current_department: '渠道管理中心/网络获客部'
      },
      contentType: { id: '2bbe1451-9fec-4cf0-9c59-8aa797900bbb', name: '装修案例分享' },
      businessVariables: {
        外框面积: '110-130㎡',
        基础: '9.5万',
        木制品: '4万',
        主材: '5.5万',
        目标人群: '毛坯',
        楼盘信息: '新芙蓉之都',
        设计风格: '复合写意',
        所在区域: '测试',
        居住人口: '四口之家'
      }
    })

    assert.deepEqual(payload.contentType, {
      typeName: '装修案例分享',
      contentTypeId: '2bbe1451-9fec-4cf0-9c59-8aa797900bbb',
      contentTypeCode: 'CT01'
    })
    assert.deepEqual(payload.facts.process, ['定制化家装交付'])
    assert.deepEqual(payload.facts.price, ['基础 9.5万', '木制品 4万', '主材 5.5万'])
    assert.equal(payload.facts.quote_type, 'budget')
    assert.match(payload.facts.quantity, /^\d+㎡$/)
    assert.ok(!payload.facts.quantity.includes('-'))
    assert.ok(!payload.facts.process.includes('复合写意'))
    assert.ok(!payload.facts.process.some((item) => String(item).includes('㎡')))
    assert.equal(payload.facts.scene, '复合写意')
    assert.equal(payload.facts.location, '测试 · 新芙蓉之都')
    assert.equal(payload.facts.product, '新芙蓉之都定制化家装项目')
    assert.deepEqual(payload.facts.audience, ['四口之家', '毛坯'])
    assert.ok(payload.facts.result.includes(payload.facts.quantity))
    assert.ok(payload.facts.result.includes('复合写意'))
    assert.ok(!payload.facts.quote_block)
    assert.ok(!payload.facts.title_price)
  })

  it('maps knowledge pack to CT06 pain/process without dumping style into process', () => {
    const payload = buildContentRequestPayload({
      employee: {
        name: '朱穆',
        employee_code: 'H06380',
        login_account: '17872384482',
        gender: 'female',
        age: 27,
        role: '管理员',
        current_branch: '家装事业部',
        current_department: '渠道管理中心/网络获客部'
      },
      contentType: { id: '95b91a82-ab50-4c78-b1dc-cdb469e50828', name: '装修知识科普' },
      businessVariables: {
        目标人群: '毛坯',
        工艺类型: '个性定制系统',
        设计风格: '复合写意',
        工艺名称: 'HYB-吊顶与背景墙造型实现工艺',
        所在区域: '长沙'
      }
    })

    assert.deepEqual(payload.contentType, {
      typeName: '装修知识科普',
      contentTypeId: '95b91a82-ab50-4c78-b1dc-cdb469e50828',
      contentTypeCode: 'CT06'
    })
    assert.deepEqual(payload.facts.process, ['个性定制系统', 'HYB-吊顶与背景墙造型实现工艺'])
    assert.ok(!payload.facts.process.includes('复合写意'))
    assert.equal(payload.facts.scene, '复合写意')
    assert.equal(payload.facts.product, 'HYB-吊顶与背景墙造型实现工艺')
    assert.equal(payload.facts.location, '长沙')
    assert.deepEqual(payload.facts.audience, ['毛坯'])
    assert.match(payload.facts.pain, /HYB-吊顶与背景墙造型实现工艺/)
    assert.match(payload.facts.result, /判断标准/)
    assert.ok(payload.facts.advantage.some((item) => item.includes('鸿扬家装')))
    assert.ok(!payload.facts.advantage.some((item) => item.includes('鸿扬家居')))
    assert.ok(!payload.facts.price)
    assert.ok(!payload.facts.quote_type)
    assert.ok(!payload.facts.quote_block)
  })

  it('maps persona pack to CT07 persona_fact/advantage with job and years', () => {
    const payload = buildContentRequestPayload({
      employee: {
        name: '朱穆',
        employee_code: 'H06380',
        login_account: '17872384482',
        gender: 'female',
        age: 27,
        role: '管理员',
        current_branch: '家装事业部',
        current_department: '渠道管理中心/网络获客部'
      },
      contentType: { id: 'bbd42313-6031-4444-bc75-46d66836da83', name: '人设自荐' },
      businessVariables: {
        目标人群: '毛坯',
        岗位: '设计师',
        从业年限: '5',
        所在区域: '长沙'
      }
    })

    assert.deepEqual(payload.contentType, {
      typeName: '人设自荐',
      contentTypeId: 'bbd42313-6031-4444-bc75-46d66836da83',
      contentTypeCode: 'CT07'
    })
    assert.equal(
      payload.facts.persona_fact,
      '朱穆，27岁，设计师，从业5年，服务长沙，家装事业部渠道管理中心/网络获客部，工号H06380。'
    )
    assert.deepEqual(payload.facts.process, ['设计师服务'])
    assert.ok(payload.facts.advantage.includes('设计师，5年'))
    assert.ok(payload.facts.advantage.some((item) => item.includes('鸿扬家装')))
    assert.equal(payload.facts.product, '鸿扬家装设计师服务')
    assert.equal(payload.facts.location, '长沙')
    assert.deepEqual(payload.facts.audience, ['毛坯'])
    assert.match(payload.facts.pain, /设计师/)
    assert.ok(!payload.facts.pain.includes('户型'))
    assert.ok(!payload.facts.price)
    assert.ok(!payload.facts.quote_type)
    assert.ok(!payload.facts.quote_block)
  })
})
