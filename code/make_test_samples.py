#!/usr/bin/env python3
"""
从已有的 result/*.json 提取 UserProfile，生成测试用样本文件
不重新读 Excel，速度极快
"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(BASE, "result")
OUTPUT_DIR = os.path.join(BASE, "data", "test_samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)

result_files = sorted(glob.glob(os.path.join(RESULT_DIR, "result_*.json")))
if not result_files:
    print("❌ 没找到 result/*.json，请先运行 generate_3_reports.py")
    sys.exit(1)

FIELD_ZH = {
    "age": "年龄", "sex": "性别", "province": "省份", "residence": "居住地",
    "education_years": "受教育年限", "marital_status": "婚姻状况",
    "health_limitation": "健康限制",
    "badl_bathing": "洗澡", "badl_dressing": "穿衣", "badl_toileting": "上厕所",
    "badl_transferring": "室内活动", "badl_continence": "大小便控制", "badl_eating": "吃饭",
    "iadl_visiting": "串门", "iadl_shopping": "购物", "iadl_cooking": "做饭",
    "iadl_laundry": "洗衣", "iadl_walking": "步行1km", "iadl_carrying": "提重物",
    "iadl_crouching": "蹲起", "iadl_transport": "乘公共交通",
    "hypertension": "高血压", "diabetes": "糖尿病", "heart_disease": "心脏病",
    "stroke": "中风", "cataract": "白内障", "cancer": "癌症", "arthritis": "关节炎",
    "cognition_time": "时间定向", "cognition_month": "月份定向",
    "cognition_season": "季节定向", "cognition_place": "地点定向",
    "cognition_calc": "计算能力", "cognition_draw": "画图能力",
    "depression": "抑郁感", "anxiety": "焦虑感", "loneliness": "孤独感",
    "smoking": "吸烟", "drinking": "饮酒", "exercise": "锻炼", "sleep_quality": "睡眠质量",
    "weight": "体重(kg)", "height": "身高(cm)", "vision": "视力", "hearing": "听力",
    "living_arrangement": "居住安排", "cohabitants": "同住人数",
    "financial_status": "经济状况", "income": "年收入",
    "medical_insurance": "医保", "caregiver": "照护者",
}

index_list = []

for i, fpath in enumerate(result_files, 1):
    fname = os.path.basename(fpath)
    print(f"── 样本 {i}：{fname} ──")

    with open(fpath, "r", encoding="utf-8") as f:
        result = json.load(f)

    # result JSON 顶层结构：直接包含各 agent 输出，以及 user_profile
    profile = result.get("user_profile") or result.get("profile")

    if profile is None:
        # 尝试从顶层直接找 age/sex 这类字段
        if "age" in result:
            profile = result
        else:
            print(f"   ⚠️  找不到 profile 字段，跳过。顶层 keys: {list(result.keys())[:8]}")
            continue

    age  = profile.get("age", "?")
    sex  = profile.get("sex", "?")
    prov = profile.get("province", "?")
    resi = profile.get("residence", "?")

    print(f"   基本信息：{age}岁 {sex} | {prov} {resi}")
    print(f"   婚姻：{profile.get('marital_status','?')} | 教育：{profile.get('education_years','?')}年")

    diseases = [name for field, name in [
        ("hypertension","高血压"),("diabetes","糖尿病"),("heart_disease","心脏病"),
        ("stroke","中风"),("cataract","白内障"),("cancer","癌症"),("arthritis","关节炎")
    ] if str(profile.get(field, "")).strip() in ("1","是","yes","Yes")]
    print(f"   慢性病：{', '.join(diseases) if diseases else '无'}")

    print("   【完整 Profile】")
    for key, val in profile.items():
        if key == "user_type":
            continue
        zh = FIELD_ZH.get(key, key)
        print(f"     {zh:12s}: {val}")
    print()

    # 保存纯 profile JSON（给 ConversationManager 或 run_v2.py 直接用）
    label = fname.replace("result_", "").replace(".json", "")
    out_name = f"sample_{i}_{label}.json"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"label": label, "profile": profile}, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 已保存: data/test_samples/{out_name}")

    index_list.append({"sample_id": i, "label": label, "file": out_name,
                       "age": age, "sex": sex})

# 保存索引
with open(os.path.join(OUTPUT_DIR, "index.json"), "w", encoding="utf-8") as f:
    json.dump(index_list, f, ensure_ascii=False, indent=2)

print(f"\n✅ 共生成 {len(index_list)} 个测试样本 → data/test_samples/")
print("\n使用方法：")
print("  # 命令行跑报告（直接指定 Excel 行号）：")
print("  python3 run_v2.py --row 0")
print()
print("  # 对话测试（chat_cli.py 可手动输入这些信息）：")
for item in index_list:
    print(f"  样本{item['sample_id']} {item['label']}: {item['age']}岁 {item['sex']}")
