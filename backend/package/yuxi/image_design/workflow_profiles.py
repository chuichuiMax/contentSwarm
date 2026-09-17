from __future__ import annotations

from .schemas import WORKFLOW_PROFILE_VERSION

WORKFLOW_PROFILES = {
    "style_transfer": {
        "label": "原房换装",
        "roles": [("structure_source", "原房实拍图")],
        "hard_constraints": [
            "以第 1 张原房实拍图为结构与镜头基准",
            "保留墙体、门窗、梁柱、层高、固定设施、空间比例、镜头位置和透视关系",
            "只改变装修风格、材质、家具、软装和灯光",
        ],
        "negative_constraints": ["不得改变户型结构", "不得移动门窗梁柱", "不得改变镜头与透视"],
    },
    "room_adapt": {
        "label": "户型适配",
        "roles": [("style_reference", "设计风格参考图"), ("structure_source", "毛坯实拍图")],
        "hard_constraints": [
            "第 1 张图仅提供设计风格、配色、材质和软装语言",
            "第 2 张毛坯实拍图是墙体、门窗、梁柱、层高、空间比例、镜头和透视的唯一基准",
            "两张图发生冲突时必须以第 2 张图为准",
        ],
        "negative_constraints": ["不得照搬第 1 张图的户型与家具位置", "不得改变第 2 张图的门窗和结构"],
    },
    "cross_space": {
        "label": "跨空间迁移",
        "roles": [("cross_space_style", "跨空间风格参考图")],
        "hard_constraints": [
            "第 1 张图只提供可迁移的风格、配色、材质和光照语言",
            "输出空间类型、布局和附加元素完全服从当前选择",
            "不得迁移参考图原有的空间类型、户型结构和家具布局",
        ],
        "negative_constraints": ["不得生成参考图原空间", "不得沿用参考图家具布局"],
    },
}

STYLE_PROFILES = {
    "现代轻奢": {
        "palette": ["中性暖灰", "米白", "金属点缀"],
        "materials": ["石材", "木饰面", "金属"],
        "traits": ["克制精致", "利落线条"],
    },
    "意式极简": {
        "palette": ["暖灰", "深咖", "米白"],
        "materials": ["岩板", "深色木饰面", "皮革"],
        "traits": ["低饱和", "整体块面"],
    },
    "新中式": {
        "palette": ["木色", "墨色", "米白"],
        "materials": ["木格栅", "石材", "棉麻"],
        "traits": ["现代东方", "留白秩序"],
    },
    "现代法式": {
        "palette": ["奶油白", "浅灰", "黄铜点缀"],
        "materials": ["护墙板", "石膏线", "丝绒"],
        "traits": ["柔和曲线", "精致对称"],
    },
    "极简奶油风": {
        "palette": ["奶油白", "浅米", "原木色"],
        "materials": ["微水泥", "浅色木饰面", "柔软织物"],
        "traits": ["柔和圆角", "温润简洁"],
    },
    "现代简约": {
        "palette": ["白", "灰", "自然木色"],
        "materials": ["木饰面", "玻璃", "哑光涂料"],
        "traits": ["简洁功能", "清晰线条"],
    },
    "侘寂风": {
        "palette": ["土色", "灰褐", "米灰"],
        "materials": ["夯土肌理", "微水泥", "原木"],
        "traits": ["自然不完美", "安静留白"],
    },
    "南洋复古风": {
        "palette": ["深木色", "墨绿", "藤编色"],
        "materials": ["藤编", "深色木材", "花砖"],
        "traits": ["热带复古", "通风轻盈"],
    },
    "美式现代": {
        "palette": ["暖白", "胡桃木", "灰蓝"],
        "materials": ["木饰面", "皮革", "布艺"],
        "traits": ["舒适大方", "经典比例"],
    },
    "日式极简禅风": {
        "palette": ["原木色", "米白", "浅灰"],
        "materials": ["原木", "和纸", "亚麻"],
        "traits": ["低矮尺度", "自然克制"],
    },
}

SPACE_LABELS = {
    "living_room": "客厅",
    "dining_room": "餐厅",
    "kitchen": "厨房",
    "master_bedroom": "主卧",
    "children_room": "次卧/儿童房",
    "study": "书房",
    "master_bathroom": "主卫",
    "bathroom": "公卫",
    "balcony": "阳台",
    "entrance": "玄关",
    "cloakroom": "衣帽间",
    "tea_room": "茶室",
    "audio_visual_room": "影音室",
    "wine_cellar": "酒窖",
    "gym": "健身房",
    "elder_room": "长辈房",
    "guest_room": "客房",
}

LAYOUTS = {
    "living_room": {
        "sofa_wall": "一字型沙发靠墙",
        "l_sofa": "L 型沙发配单人椅",
        "face_to_face": "沙发对坐式",
        "free_layout": "无主沙发自由布局",
    },
    "dining_room": {"round_table": "圆桌居中", "island_table": "餐岛一体", "long_table": "长桌靠墙"},
    "kitchen": {"island": "中岛操作台", "l_shape": "L 型橱柜", "u_shape": "U 型橱柜"},
    "master_bedroom": {"bed_center": "床居中对称", "bed_window": "床靠窗", "bed_wall": "床靠墙并保留过道"},
    "children_room": {"bed_desk": "床铺与书桌组合", "bunk": "上下床", "bed_play": "床铺与活动区组合"},
    "study": {"desk_window": "书桌靠窗", "desk_wall": "书桌靠墙", "dual_desk": "双人书桌"},
    "master_bathroom": {"wet_dry": "干湿分离", "double_vanity": "双台盆对称", "bathtub_window": "浴缸靠窗"},
    "bathroom": {"compact": "紧凑一字型", "wet_dry": "干湿分离", "vanity_wall": "台盆靠墙"},
    "balcony": {"lounge": "休闲区", "laundry": "洗衣区", "greenhouse": "阳台花园"},
    "entrance": {"one_wall": "一字玄关柜", "l_shape": "L 型转角柜", "open_entry": "开放式玄关"},
    "cloakroom": {"u_storage": "U 型衣柜", "island_storage": "衣帽岛台", "parallel": "平行衣柜"},
    "tea_room": {"tea_table_center": "茶桌居中", "tea_wall": "茶桌靠墙", "floor_seating": "地台茶席"},
    "audio_visual_room": {"screen_center": "幕布居中", "sofa_screen": "沙发正对幕布", "immersive": "沉浸式环绕布局"},
    "wine_cellar": {"cellar_wall": "酒柜靠墙", "island_cellar": "酒柜与中岛", "tasting_table": "品酒桌居中"},
    "gym": {"mirror_wall": "镜墙训练区", "equipment_wall": "器械靠墙", "free_training": "自由训练区"},
    "elder_room": {"bed_side": "床靠墙并保留宽过道", "bed_center": "床居中", "bed_lounge": "床与休闲椅组合"},
    "guest_room": {"bed_center": "床居中对称", "bed_desk": "床铺与书桌组合", "sofa_bed": "沙发床组合"},
}

ADDONS = {
    "living_room": {
        "lounge_chair": "落地窗旁休闲躺椅",
        "bar_table": "沙发后长条书桌或吧台",
        "storage_wall": "电视墙满墙收纳柜",
        "shelf": "开放式层板展示架",
        "rug_zone": "用地毯划分沙发区",
        "fireplace": "壁炉居中",
    },
    "dining_room": {"sideboard": "餐边柜", "pendant_lights": "组合吊灯", "display_cabinet": "餐具展示柜"},
    "kitchen": {"high_cabinet": "高柜电器区", "breakfast_bar": "早餐吧台", "open_shelf": "开放层板"},
    "master_bedroom": {"bedside_bench": "床尾凳", "vanity": "梳妆台", "tv_wall": "电视背景墙"},
    "children_room": {"study_shelf": "学习收纳墙", "play_corner": "玩耍角", "blackboard": "黑板墙"},
    "study": {"bookcase": "整墙书柜", "reading_chair": "阅读单椅", "printer_cabinet": "打印收纳柜"},
    "master_bathroom": {"bathtub": "独立浴缸", "shower": "淋浴间", "smart_toilet": "智能马桶"},
    "bathroom": {"mirror_cabinet": "镜柜", "shower": "淋浴间", "laundry": "洗烘组合"},
    "balcony": {"laundry_cabinet": "洗衣收纳柜", "plants": "绿植角", "coffee_table": "小茶几"},
    "entrance": {"bench": "换鞋凳", "full_height": "通顶收纳", "mirror": "穿衣镜"},
    "cloakroom": {"glass_door": "玻璃柜门", "island": "中岛抽屉", "jewelry": "首饰收纳"},
    "tea_room": {"tea_cabinet": "茶具柜", "landscape": "山水挂画", "screen": "屏风隔断"},
    "audio_visual_room": {"projector": "投影设备", "acoustic_wall": "吸音墙面", "recliner": "影音躺椅"},
    "wine_cellar": {"wine_rack": "红酒陈列架", "tasting_bar": "品酒吧台", "glass_cabinet": "玻璃酒柜"},
    "gym": {"treadmill": "跑步机", "yoga": "瑜伽垫区", "dumbbell": "哑铃架"},
    "elder_room": {"grab_bar": "安全扶手", "reading_light": "床头阅读灯", "storage": "低位收纳柜"},
    "guest_room": {"luggage": "行李收纳位", "wardrobe": "衣柜", "reading_chair": "阅读单椅"},
}


def public_profiles() -> dict[str, object]:
    return {
        "version": WORKFLOW_PROFILE_VERSION,
        "workflows": {
            key: {"label": value["label"], "roles": value["roles"], "hard_constraints": value["hard_constraints"]}
            for key, value in WORKFLOW_PROFILES.items()
        },
        "styles": [{"id": label, "label": label} for label in STYLE_PROFILES],
        "spaces": [
            {
                "id": space_id,
                "label": label,
                "layouts": [{"id": key, "label": value} for key, value in LAYOUTS[space_id].items()],
                "addons": [{"id": key, "label": value} for key, value in ADDONS[space_id].items()],
            }
            for space_id, label in SPACE_LABELS.items()
        ],
    }
