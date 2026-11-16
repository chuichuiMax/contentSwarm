/** 旧房焕新题本：与 EDC http://portal.hi-run.net/edc/formview/o6n08s 同源 */
export const RENOVATION_FORM_INTRO_TITLE = '旧房焕新定制需求表'

export const RENOVATION_QUESTIONS = [
  {
    "key": "field003",
    "title": "面积",
    "kind": "text"
  },
  {
    "key": "field005",
    "title": "2、住房户型",
    "kind": "check",
    "options": [
      "两房两厅",
      "三房两厅",
      "四房两厅",
      "五房两厅及以上"
    ]
  },
  {
    "key": "field006",
    "title": "3、房屋现状",
    "kind": "check",
    "options": [
      "自住",
      "出租",
      "闲置"
    ]
  },
  {
    "key": "field007",
    "title": "4、预计改造时间",
    "kind": "text"
  },
  {
    "key": "field008",
    "title": "5、住房使用定位",
    "kind": "check",
    "options": [
      "养老",
      "婚房",
      "陪读房",
      "投资",
      "度假"
    ],
    "other": true
  },
  {
    "key": "field009",
    "title": "6、常住人口",
    "kind": "check",
    "options": [
      "1人",
      "2人",
      "3口之家",
      "4口之家",
      "三代同堂",
      "四代同堂"
    ]
  },
  {
    "key": "field010",
    "title": "7、养宠需求",
    "kind": "check",
    "options": [
      "有宠，需独立空间",
      "有宠，不需独立空间",
      "无宠物"
    ]
  },
  {
    "key": "field011",
    "title": "8、您对焕新装修最关注的是？",
    "kind": "check",
    "options": [
      "环保",
      "外观好看",
      "功能好用",
      "价格",
      "工期",
      "工艺"
    ]
  },
  {
    "key": "field012",
    "title": "9、焕新风格倾向",
    "kind": "check",
    "options": [
      "现代简约风",
      "轻法奶油风",
      "侘寂风",
      "北欧风",
      "新中式风",
      "意式轻奢风",
      "美式风",
      "中古风"
    ],
    "other": true
  },
  {
    "key": "field013",
    "title": "10、您计划焕新预算是多少？",
    "kind": "check",
    "options": [
      "3-5万",
      "5-7万",
      "7-9万",
      "9-12万",
      "12-15万",
      "15-20万",
      "20-30万",
      "30万以上"
    ]
  },
  {
    "key": "field014",
    "title": "11、您是通过哪种渠道了解过鸿扬？",
    "kind": "check",
    "options": [
      "户外广告",
      "亲戚朋友",
      "工地",
      "电话邀约",
      "其他渠道"
    ]
  },
  {
    "key": "field015",
    "title": "您对焕新的需求？",
    "kind": "check",
    "options": [
      "全房焕新",
      "空间焕新（厨房、卫生间等）",
      "项目焕新（墙漆、暖气片、木制品等）"
    ]
  },
  {
    "key": "field016",
    "title": "您对全房弱电的需求？",
    "kind": "check",
    "options": [
      "全房WiFi覆盖",
      "全房WiFi+局部空间网线覆盖",
      "全房WiFi+全房网线覆盖 "
    ]
  },
  {
    "key": "field017",
    "title": "您对全房暖通设备的需求？",
    "kind": "check",
    "options": [
      "风管机",
      "中央空调",
      "全房暖气片",
      "全房地暖",
      "地暖+暖气片",
      "新风",
      "无"
    ]
  },
  {
    "key": "field018",
    "title": "您对智能系统的需求？",
    "kind": "check",
    "options": [
      "智能灯光",
      "电动窗帘",
      "可视监控",
      "背景音乐",
      "智能控温",
      "无"
    ]
  },
  {
    "key": "field019",
    "title": "您对封窗的需求？",
    "kind": "check",
    "options": [
      "室内窗户全换",
      "室内窗户局部更换"
    ]
  }
]

export const RENOVATION_FORM_STEPS = [
  {
    "id": "base",
    "title": "基本信息",
    "questionKeys": [
      "field003",
      "field005",
      "field006",
      "field007",
      "field008",
      "field009",
      "field010",
      "field011"
    ]
  },
  {
    "id": "plan",
    "title": "关注与预算",
    "questionKeys": [
      "field012",
      "field013",
      "field014"
    ]
  },
  {
    "id": "whole",
    "title": "全房需求",
    "questionKeys": [
      "field015",
      "field016",
      "field017",
      "field018",
      "field019"
    ]
  }
]
