export const CONTENT_TYPE_NAME_TO_CODE = {
  工艺施工展示: 'CT05',
  工艺展示: 'CT05',
  装修报价清单: 'CT02',
  报价清单: 'CT02',
  装修避坑分享: 'CT03',
  避坑分享: 'CT03',
  装修省钱攻略: 'CT04',
  省钱攻略: 'CT04',
  装修案例分享: 'CT01',
  案例分享: 'CT01',
  装修知识科普: 'CT06',
  知识科普: 'CT06',
  人设自荐: 'CT07',
  装修人设自荐: 'CT07'
}

export const CREATION_TYPE_NAMES = {
  CT01: '装修案例分享',
  CT02: '装修报价清单',
  CT03: '装修避坑分享',
  CT04: '装修省钱攻略',
  CT05: '工艺施工展示',
  CT06: '装修知识科普',
  CT07: '人设自荐'
}

export const CREATION_TYPE_OPTIONS = Object.entries(CREATION_TYPE_NAMES).map(([value, label]) => ({
  value,
  label
}))

export const contentTypeSelectOptions = (types = []) => {
  const options = []
  const seen = new Set()
  for (const item of types) {
    if (!item || item.enabled === false) continue
    const code = CONTENT_TYPE_NAME_TO_CODE[item.name]
    if (!code || seen.has(code)) continue
    seen.add(code)
    options.push({ value: code, label: item.name })
  }
  return options.length ? options : CREATION_TYPE_OPTIONS
}
