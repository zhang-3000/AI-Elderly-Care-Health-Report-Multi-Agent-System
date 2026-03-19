"""
家属端数据管理模块
处理家属对老年人信息的编辑、查询、报告版本管理等
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple


class FamilyDataManager:
    """家属端数据管理器"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "users.db")
        self.db_path = db_path

    def get_elderly_profile(self, elderly_id: str) -> Optional[Dict]:
        """获取老年人完整档案"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT profile_data, completion_rate, updated_at
                FROM user_profiles
                WHERE user_id = ?
            """, (elderly_id,))

            result = cursor.fetchone()
            if not result:
                return None

            profile_data = json.loads(result[0]) if result[0] else {}
            return {
                "profile": profile_data,
                "completion_rate": result[1],
                "updated_at": result[2]
            }

        finally:
            conn.close()

    def get_missing_fields(self, elderly_id: str) -> Dict[str, List[str]]:
        """获取缺失的字段列表"""
        profile = self.get_elderly_profile(elderly_id)
        if not profile:
            return {}

        # 所有必填字段
        all_fields = {
            "基本信息": ["age", "sex", "province", "residence", "education_years", "marital_status"],
            "健康限制": ["health_limitation"],
            "基本生活": ["badl_bathing", "badl_dressing", "badl_toileting", "badl_transferring", "badl_continence", "badl_eating"],
            "复杂活动": ["iadl_visiting", "iadl_shopping", "iadl_cooking", "iadl_laundry", "iadl_walking", "iadl_carrying", "iadl_crouching", "iadl_transport"],
            "慢性病": ["hypertension", "diabetes", "heart_disease", "stroke", "cataract", "cancer", "arthritis"],
            "认知功能": ["cognition_time", "cognition_month", "cognition_season", "cognition_place", "cognition_calc", "cognition_draw"],
            "心理状态": ["depression", "anxiety", "loneliness"],
            "生活方式": ["smoking", "drinking", "exercise", "sleep_quality"],
            "身体指标": ["weight", "height", "vision", "hearing"],
            "社会支持": ["living_arrangement", "cohabitants", "financial_status", "income", "medical_insurance", "caregiver"]
        }

        profile_data = profile["profile"]
        missing = {}

        for group_name, fields in all_fields.items():
            missing_fields = [f for f in fields if f not in profile_data or profile_data[f] is None]
            if missing_fields:
                missing[group_name] = missing_fields

        return missing

    def update_elderly_profile(
        self,
        elderly_id: str,
        editor_id: str,
        editor_type: str,
        updates: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        更新老年人档案

        Args:
            elderly_id: 老年人ID
            editor_id: 编辑者ID
            editor_type: 编辑者类型（elderly/family）
            updates: 要更新的字段字典

        Returns:
            (成功标志, 消息)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 获取当前档案
            cursor.execute("""
                SELECT profile_data FROM user_profiles WHERE user_id = ?
            """, (elderly_id,))

            result = cursor.fetchone()
            if not result:
                return False, "用户档案不存在"

            current_profile = json.loads(result[0]) if result[0] else {}

            # 记录修改日志
            for field_name, new_value in updates.items():
                old_value = current_profile.get(field_name)
                if old_value != new_value:
                    log_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO profile_edit_log
                        (log_id, elderly_id, editor_id, editor_type, field_name, old_value, new_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        log_id, elderly_id, editor_id, editor_type,
                        field_name,
                        json.dumps(old_value),
                        json.dumps(new_value)
                    ))

            # 更新档案
            current_profile.update(updates)
            completion_rate = self._calculate_completion_rate(current_profile)

            cursor.execute("""
                UPDATE user_profiles
                SET profile_data = ?, completion_rate = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (json.dumps(current_profile), completion_rate, elderly_id))

            conn.commit()
            return True, "更新成功"

        except Exception as e:
            conn.rollback()
            return False, f"更新失败: {str(e)}"
        finally:
            conn.close()

    def _calculate_completion_rate(self, profile: Dict) -> float:
        """计算档案完整度"""
        all_fields = [
            "age", "sex", "province", "residence", "education_years", "marital_status",
            "health_limitation",
            "badl_bathing", "badl_dressing", "badl_toileting", "badl_transferring", "badl_continence", "badl_eating",
            "iadl_visiting", "iadl_shopping", "iadl_cooking", "iadl_laundry", "iadl_walking", "iadl_carrying", "iadl_crouching", "iadl_transport",
            "hypertension", "diabetes", "heart_disease", "stroke", "cataract", "cancer", "arthritis",
            "cognition_time", "cognition_month", "cognition_season", "cognition_place", "cognition_calc", "cognition_draw",
            "depression", "anxiety", "loneliness",
            "smoking", "drinking", "exercise", "sleep_quality",
            "weight", "height", "vision", "hearing",
            "living_arrangement", "cohabitants", "financial_status", "income", "medical_insurance", "caregiver"
        ]

        filled_count = sum(1 for f in all_fields if f in profile and profile[f] is not None)
        return filled_count / len(all_fields) if all_fields else 0.0

    def get_edit_log(self, elderly_id: str, limit: int = 100) -> List[Dict]:
        """获取修改日志"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT 
                    log_id, editor_id, editor_type, field_name, old_value, new_value, edited_at
                FROM profile_edit_log
                WHERE elderly_id = ?
                ORDER BY edited_at DESC
                LIMIT ?
            """, (elderly_id, limit))

            logs = []
            for row in cursor.fetchall():
                logs.append({
                    "log_id": row[0],
                    "editor_id": row[1],
                    "editor_type": row[2],
                    "field_name": row[3],
                    "old_value": json.loads(row[4]) if row[4] else None,
                    "new_value": json.loads(row[5]) if row[5] else None,
                    "edited_at": row[6]
                })

            return logs

        finally:
            conn.close()

    def generate_report_version(
        self,
        elderly_id: str,
        report_data: Dict,
        completion_rate: float,
        generated_by: str,
        generated_by_type: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        生成新的报告版本

        Args:
            elderly_id: 老年人ID
            report_data: 报告数据
            completion_rate: 完整度
            generated_by: 生成者ID
            generated_by_type: 生成者类型（elderly/family）

        Returns:
            (成功标志, 消息, version_id)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 获取下一个版本号
            cursor.execute("""
                SELECT COUNT(*) FROM report_versions WHERE elderly_id = ?
            """, (elderly_id,))

            version_count = cursor.fetchone()[0]
            version_number = f"v{version_count + 1}.0"

            # 将前一个版本标记为非最新
            cursor.execute("""
                UPDATE report_versions
                SET is_latest = 0
                WHERE elderly_id = ? AND is_latest = 1
            """, (elderly_id,))

            # 创建新版本
            version_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO report_versions
                (version_id, elderly_id, report_data, completion_rate, generated_by, generated_by_type, version_number, is_latest)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                version_id, elderly_id,
                json.dumps(report_data),
                completion_rate,
                generated_by, generated_by_type,
                version_number
            ))

            conn.commit()
            return True, "报告版本生成成功", version_id

        except Exception as e:
            conn.rollback()
            return False, f"生成失败: {str(e)}", None
        finally:
            conn.close()

    def get_report_versions(self, elderly_id: str) -> List[Dict]:
        """获取所有报告版本"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT 
                    version_id, version_number, completion_rate, generated_by_type, generated_at, is_latest
                FROM report_versions
                WHERE elderly_id = ?
                ORDER BY generated_at DESC
            """, (elderly_id,))

            versions = []
            for row in cursor.fetchall():
                versions.append({
                    "version_id": row[0],
                    "version_number": row[1],
                    "completion_rate": row[2],
                    "generated_by_type": row[3],
                    "generated_at": row[4],
                    "is_latest": bool(row[5])
                })

            return versions

        finally:
            conn.close()

    def get_report_version(self, version_id: str) -> Optional[Dict]:
        """获取特定版本的报告"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT report_data, version_number, completion_rate, generated_at
                FROM report_versions
                WHERE version_id = ?
            """, (version_id,))

            result = cursor.fetchone()
            if not result:
                return None

            return {
                "report_data": json.loads(result[0]),
                "version_number": result[1],
                "completion_rate": result[2],
                "generated_at": result[3]
            }

        finally:
            conn.close()

    def delete_report_version(self, version_id: str) -> Tuple[bool, str]:
        """删除报告版本"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 检查是否是最新版本
            cursor.execute("""
                SELECT is_latest FROM report_versions WHERE version_id = ?
            """, (version_id,))

            result = cursor.fetchone()
            if not result:
                return False, "版本不存在"

            if result[0]:
                return False, "不能删除最新版本"

            # 删除版本
            cursor.execute("""
                DELETE FROM report_versions WHERE version_id = ?
            """, (version_id,))

            conn.commit()
            return True, "删除成功"

        except Exception as e:
            conn.rollback()
            return False, f"删除失败: {str(e)}"
        finally:
            conn.close()

    def compare_report_versions(self, version_id_1: str, version_id_2: str) -> Optional[Dict]:
        """对比两个报告版本"""
        report_1 = self.get_report_version(version_id_1)
        report_2 = self.get_report_version(version_id_2)

        if not report_1 or not report_2:
            return None

        return {
            "version_1": report_1,
            "version_2": report_2,
            "differences": self._find_differences(report_1["report_data"], report_2["report_data"])
        }

    def _find_differences(self, data_1: Dict, data_2: Dict) -> List[Dict]:
        """找出两个数据之间的差异"""
        differences = []

        all_keys = set(data_1.keys()) | set(data_2.keys())
        for key in all_keys:
            val_1 = data_1.get(key)
            val_2 = data_2.get(key)

            if val_1 != val_2:
                differences.append({
                    "field": key,
                    "old_value": val_1,
                    "new_value": val_2
                })

        return differences


if __name__ == "__main__":
    # 测试代码
    manager = FamilyDataManager()

    # 获取老年人档案
    profile = manager.get_elderly_profile("test_elderly_id")
    print(f"档案: {profile}")

    # 获取缺失字段
    missing = manager.get_missing_fields("test_elderly_id")
    print(f"缺失字段: {missing}")
