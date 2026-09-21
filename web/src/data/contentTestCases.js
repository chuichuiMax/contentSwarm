import beijingQuote002 from '@/assets/content-test-cases/北京报价002.txt?raw'
import chengduQuote003 from '@/assets/content-test-cases/成都报价003.txt?raw'
import guangzhouQuote004 from '@/assets/content-test-cases/广州报价004.txt?raw'
import dailyWork001 from '@/assets/content-test-cases/日常工作001.txt?raw'
import dailyWork002 from '@/assets/content-test-cases/日常工作002.txt?raw'
import dailyWork003 from '@/assets/content-test-cases/日常工作003.txt?raw'
import dailyWork004 from '@/assets/content-test-cases/日常工作004.txt?raw'
import dailyWork005 from '@/assets/content-test-cases/日常工作005.txt?raw'
import dailyWork006 from '@/assets/content-test-cases/日常工作006.txt?raw'
import constructionCraft001 from '@/assets/content-test-cases/施工工艺001.txt?raw'
import quoteJsonTemplate from '@/assets/content-test-cases/施工报价json模板.txt?raw'
import quoteJson005 from '@/assets/content-test-cases/施工报价json005.txt?raw'
import quoteJson006 from '@/assets/content-test-cases/施工报价json006.txt?raw'
import selfIntroduction001 from '@/assets/content-test-cases/自我介绍001.txt?raw'
import selfIntroduction002 from '@/assets/content-test-cases/自我介绍002.txt?raw'
import selfIntroduction003 from '@/assets/content-test-cases/自我介绍003.txt?raw'

export const CONTENT_TEST_CASE_CATEGORIES = [
  { value: 'daily-work', label: '日常工作' },
  { value: 'construction-quote', label: '施工报价' },
  { value: 'construction-craft', label: '施工工艺' },
  { value: 'self-introduction', label: '自我介绍' }
]

export const CONSTRUCTION_QUOTE_TEST_CASE_TYPES = [
  { value: 'CT02', label: '项目单价' },
  { value: 'CT03', label: '单价+面积' },
  { value: 'CT04', label: '工种总价' },
  { value: 'CT05', label: '人工+辅材' }
]

export const CONTENT_TEST_CASES = [
  { id: 'daily-work-001', category: 'daily-work', label: '日常工作 001', content: dailyWork001 },
  { id: 'daily-work-002', category: 'daily-work', label: '日常工作 002', content: dailyWork002 },
  { id: 'daily-work-003', category: 'daily-work', label: '日常工作 003', content: dailyWork003 },
  { id: 'daily-work-004', category: 'daily-work', label: '日常工作 004', content: dailyWork004 },
  { id: 'daily-work-005', category: 'daily-work', label: '日常工作 005', content: dailyWork005 },
  { id: 'daily-work-006', category: 'daily-work', label: '日常工作 006', content: dailyWork006 },
  {
    id: 'quote-json-001',
    category: 'construction-quote',
    label: '长沙单价面积 · 001',
    contentTypeCode: 'CT03',
    contentTypeName: '单价+面积',
    content: quoteJsonTemplate
  },
  {
    id: 'quote-beijing-002',
    category: 'construction-quote',
    label: '北京项目单价 · 002',
    contentTypeCode: 'CT02',
    contentTypeName: '项目单价',
    content: beijingQuote002
  },
  {
    id: 'quote-chengdu-003',
    category: 'construction-quote',
    label: '成都工种总价 · 003',
    contentTypeCode: 'CT04',
    contentTypeName: '工种总价',
    content: chengduQuote003
  },
  {
    id: 'quote-guangzhou-004',
    category: 'construction-quote',
    label: '广州人工辅材 · 004',
    contentTypeCode: 'CT05',
    contentTypeName: '人工+辅材',
    content: guangzhouQuote004
  },
  {
    id: 'quote-json-005',
    category: 'construction-quote',
    label: '长沙单价面积 · 005',
    contentTypeCode: 'CT03',
    contentTypeName: '单价+面积',
    content: quoteJson005
  },
  {
    id: 'quote-json-006',
    category: 'construction-quote',
    label: '长沙人工辅材 · 006',
    contentTypeCode: 'CT05',
    contentTypeName: '人工+辅材',
    content: quoteJson006
  },
  {
    id: 'construction-craft-001',
    category: 'construction-craft',
    label: '施工工艺 001',
    content: constructionCraft001
  },
  {
    id: 'self-introduction-001',
    category: 'self-introduction',
    label: '自我介绍 001',
    content: selfIntroduction001
  },
  {
    id: 'self-introduction-002',
    category: 'self-introduction',
    label: '自我介绍 002',
    content: selfIntroduction002
  },
  {
    id: 'self-introduction-003',
    category: 'self-introduction',
    label: '自我介绍 003',
    content: selfIntroduction003
  }
]
