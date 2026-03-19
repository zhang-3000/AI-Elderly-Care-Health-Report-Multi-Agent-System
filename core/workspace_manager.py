"""
工作区管理器
统一管理会话数据的文件存储
"""

from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import shutil
from datetime import datetime


class WorkspaceManager:
    """统一管理工作区文件夹和文件"""

    def __init__(self, base_dir: str = "workspace"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str, create: bool = False) -> Path:
        """解析 session 目录，按需创建。"""
        session_dir = self.base_dir / session_id
        if create:
            session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def get_session_dir(self, session_id: str) -> Path:
        """获取指定 session 的文件夹路径"""
        return self._session_dir(session_id, create=True)

    def get_metadata_path(self, session_id: str) -> Path:
        """获取会话元数据文件路径。"""
        return self._session_dir(session_id) / "metadata.json"

    def save_report(self, session_id: str, report_data: Dict[str, Any], format: str = "json") -> Path:
        """保存报告文件"""
        session_dir = self.get_session_dir(session_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.{format}"
        filepath = session_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            if format == "json":
                json.dump(report_data, f, ensure_ascii=False, indent=2)
            else:
                f.write(report_data)
        return filepath

    def save_conversation(self, session_id: str, messages: List[Dict[str, Any]]) -> Path:
        """保存对话历史"""
        session_dir = self.get_session_dir(session_id)
        filepath = session_dir / "conversation.json"
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        return filepath

    def save_user_profile(self, session_id: str, profile: Dict[str, Any]) -> Path:
        """保存用户画像"""
        session_dir = self.get_session_dir(session_id)
        filepath = session_dir / "user_profile.json"
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return filepath

    def list_sessions(self) -> List[str]:
        """列出所有会话"""
        return sorted([d.name for d in self.base_dir.iterdir() if d.is_dir()])

    def get_session_metadata(self, session_id: str) -> Dict[str, Any]:
        """获取会话元数据"""
        session_dir = self._session_dir(session_id)
        metadata_path = self.get_metadata_path(session_id)

        if not session_dir.exists():
            return {}

        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # 生成基础元数据
        files = list(session_dir.glob("*"))
        return {
            "session_id": session_id,
            "created_at": datetime.fromtimestamp(session_dir.stat().st_ctime).isoformat(),
            "files": [f.name for f in files],
            "has_report": any(f.name.startswith("report_") for f in files),
            "has_profile": (session_dir / "user_profile.json").exists()
        }

    def create_metadata(self, session_id: str, metadata: Dict[str, Any]) -> Path:
        """创建会话元数据"""
        session_dir = self.get_session_dir(session_id)
        metadata_path = session_dir / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        return metadata_path

    def update_metadata(self, session_id: str, updates: Dict[str, Any]) -> None:
        """更新会话元数据"""
        metadata = self.get_session_metadata(session_id)
        metadata.update(updates)
        self.create_metadata(session_id, metadata)

    def get_conversation(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """获取对话历史"""
        session_dir = self._session_dir(session_id)
        filepath = session_dir / "conversation.json"
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def get_user_profile(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取用户画像"""
        session_dir = self._session_dir(session_id)
        filepath = session_dir / "user_profile.json"
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def get_reports(self, session_id: str) -> List[Dict[str, Any]]:
        """获取所有报告"""
        session_dir = self._session_dir(session_id)
        reports = []
        for report_file in session_dir.glob("report_*.json"):
            with open(report_file, 'r', encoding='utf-8') as f:
                reports.append(json.load(f))
        return reports

    def get_report_files(self, session_id: str) -> List[Path]:
        """获取会话下所有 JSON 报告文件路径。"""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return []
        return sorted(session_dir.glob("report_*.json"))

    def find_sessions_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """根据 user_id 查找关联的工作区元数据。"""
        matches: List[Dict[str, Any]] = []
        for session_id in self.list_sessions():
            metadata = self.get_session_metadata(session_id)
            if metadata.get("user_id") == user_id:
                matches.append(metadata)
        return matches

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)
            return True
        return False

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        session_dir = self.base_dir / session_id
        return session_dir.exists()
