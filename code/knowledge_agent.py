"""
KnowledgeAgent - RAG 知识检索与推理 Agent
提供智能的知识检索服务，支持多维度查询
"""

from typing import Dict, Any, List, Optional

try:
    from rag.agent import PageIndexRAGAgent
except Exception:
    PageIndexRAGAgent = None


class KnowledgeAgent:
    """知识检索与推理 Agent - 封装 RAG 功能"""
    
    def __init__(self, rag_agent: PageIndexRAGAgent):
        self.rag = rag_agent
        self.cache = {}  # 查询缓存，避免重复检索
    
    def retrieve(self, query: str, top_k: int = 3, use_cache: bool = True) -> Dict[str, Any]:
        """
        基础检索方法
        
        Args:
            query: 查询字符串
            top_k: 返回结果数量
            use_cache: 是否使用缓存
        
        Returns:
            包含 query, hits, context 的字典
        """
        # 检查缓存
        cache_key = f"{query}_{top_k}"
        if use_cache and cache_key in self.cache:
            return self.cache[cache_key]
        
        try:
            result = self.rag.build_context(query, top_k=top_k)
            if use_cache:
                self.cache[cache_key] = result
            return result
        except Exception as e:
            print(f"⚠️ RAG 检索失败: {e}")
            return {
                "query": query,
                "hits": [],
                "context": ""
            }
    
    def retrieve_for_risk_prevention(
        self, 
        profile: Any, 
        risks: List[Dict],
        top_k: int = 2
    ) -> Dict[str, Any]:
        """
        针对风险预防的知识检索
        
        Args:
            profile: 用户画像
            risks: 风险列表
            top_k: 返回结果数量
        
        Returns:
            包含查询、命中结果、上下文和提取的建议
        """
        if not risks:
            return {"enabled": False, "hits": [], "context": ""}
        
        # 构建查询：优先关注高风险
        high_risks = [r for r in risks if r.get('severity') == '高']
        target_risks = high_risks[:2] if high_risks else risks[:2]
        
        risk_names = [r.get('risk', '') for r in target_risks]
        age = int(profile.age) if profile.age else 0
        query = f"{age}岁 {profile.sex} {' '.join(risk_names)} 预防措施 照护要点"
        
        # 检索
        result = self.retrieve(query, top_k=top_k)
        
        # 提取关键建议
        recommendations = self._extract_recommendations(result['hits'])
        
        return {
            "enabled": True,
            "query": query,
            "target_risks": risk_names,
            "hits": result['hits'],
            "context": result['context'],
            "recommendations": recommendations
        }
    
    def retrieve_for_disease_management(
        self, 
        profile: Any,
        top_k: int = 2
    ) -> Dict[str, Any]:
        """
        针对慢性病管理的知识检索
        
        Args:
            profile: 用户画像
            top_k: 返回结果数量
        
        Returns:
            包含疾病列表、查询、命中结果和管理要点
        """
        diseases = self._extract_diseases(profile)
        if not diseases:
            return {"enabled": False, "diseases": [], "hits": [], "context": ""}
        
        # 构建查询
        query = f"老年人 {' '.join(diseases[:3])} 日常管理 注意事项 健康标准"
        result = self.retrieve(query, top_k=top_k)
        
        return {
            "enabled": True,
            "query": query,
            "diseases": diseases,
            "hits": result['hits'],
            "context": result['context'],
            "management_tips": self._extract_management_tips(result['hits'])
        }
    
    def retrieve_for_functional_training(
        self, 
        status_result: Dict,
        top_k: int = 2
    ) -> Dict[str, Any]:
        """
        针对功能训练的知识检索
        
        Args:
            status_result: 状态判定结果
            top_k: 返回结果数量
        
        Returns:
            包含查询、命中结果和训练方法
        """
        status_name = status_result.get('status_name', '')
        badl_details = status_result.get('badl_details', [])
        iadl_details = status_result.get('iadl_details', [])
        
        if not status_name:
            return {"enabled": False, "hits": [], "context": ""}
        
        # 构建查询
        limitations = []
        if badl_details:
            limitations.extend(badl_details[:2])
        if iadl_details:
            limitations.extend(iadl_details[:2])
        
        query = f"{status_name} {' '.join(limitations)} 功能训练 康复方法 改善"
        result = self.retrieve(query, top_k=top_k)
        
        return {
            "enabled": True,
            "query": query,
            "status": status_name,
            "limitations": limitations,
            "hits": result['hits'],
            "context": result['context'],
            "training_methods": self._extract_training_methods(result['hits'])
        }
    
    def retrieve_for_action_plan(
        self,
        profile: Any,
        action_category: str,
        specific_need: str = "",
        top_k: int = 2
    ) -> Dict[str, Any]:
        """
        针对行动计划的知识检索
        
        Args:
            profile: 用户画像
            action_category: 行动类别（如"跌倒预防"、"营养改善"）
            specific_need: 具体需求描述
            top_k: 返回结果数量
        
        Returns:
            包含查询、命中结果和具体方法
        """
        age = int(profile.age) if profile.age else 0
        query_parts = [
            f"{age}岁老人",
            action_category,
            specific_need,
            "具体方法 实施步骤"
        ]
        query = " ".join([p for p in query_parts if p])
        
        result = self.retrieve(query, top_k=top_k)
        
        return {
            "enabled": True,
            "query": query,
            "category": action_category,
            "hits": result['hits'],
            "context": result['context'],
            "methods": self._extract_methods(result['hits'])
        }
    
    def retrieve_comprehensive(
        self,
        profile: Any,
        status_result: Dict,
        risk_result: Dict,
        factor_result: Dict,
        top_k: int = 3
    ) -> Dict[str, Any]:
        """
        综合知识检索（多维度）
        
        Args:
            profile: 用户画像
            status_result: 状态判定结果
            risk_result: 风险预测结果
            factor_result: 因素分析结果
            top_k: 每个维度的返回结果数量
        
        Returns:
            包含多个维度的检索结果
        """
        # 多维度检索
        risk_knowledge = self.retrieve_for_risk_prevention(
            profile, 
            risk_result.get('short_term_risks', []),
            top_k=top_k
        )
        
        disease_knowledge = self.retrieve_for_disease_management(
            profile,
            top_k=top_k
        )
        
        training_knowledge = self.retrieve_for_functional_training(
            status_result,
            top_k=top_k
        )
        
        # 合并上下文
        combined_context = self._combine_contexts([
            risk_knowledge.get('context', ''),
            disease_knowledge.get('context', ''),
            training_knowledge.get('context', '')
        ])
        
        return {
            "enabled": True,
            "risk_prevention": risk_knowledge,
            "disease_management": disease_knowledge,
            "functional_training": training_knowledge,
            "combined_context": combined_context,
            "total_hits": (
                len(risk_knowledge.get('hits', [])) +
                len(disease_knowledge.get('hits', [])) +
                len(training_knowledge.get('hits', []))
            )
        }
    
    # ========== 辅助方法 ==========
    
    def _extract_diseases(self, profile: Any) -> List[str]:
        """从用户画像中提取慢性病列表"""
        diseases = []
        disease_map = {
            'hypertension': '高血压',
            'diabetes': '糖尿病',
            'heart_disease': '心脏病',
            'stroke': '中风',
            'arthritis': '关节炎',
            'cancer': '肿瘤'
        }
        for field, name in disease_map.items():
            value = str(getattr(profile, field, '')).strip()
            if value in {'是', '有', '患有', '1', 'true', 'True'}:
                diseases.append(name)
        return diseases
    
    def _extract_recommendations(self, hits: List[Dict]) -> List[str]:
        """从检索结果中提取建议"""
        recommendations = []
        keywords = ['建议', '应', '需要', '可以', '推荐', '宜']
        
        for hit in hits:
            excerpt = hit.get('excerpt', '')
            # 查找包含关键词的句子
            for keyword in keywords:
                if keyword in excerpt:
                    # 提取包含关键词的句子片段
                    sentences = excerpt.split('。')
                    for sentence in sentences:
                        if keyword in sentence and len(sentence) > 10:
                            recommendations.append(sentence.strip()[:120])
                            break
                    break
        
        return list(set(recommendations))[:5]  # 去重并限制数量
    
    def _extract_management_tips(self, hits: List[Dict]) -> List[str]:
        """提取管理要点"""
        tips = []
        keywords = ['管理', '控制', '监测', '注意', '定期', '检查']
        
        for hit in hits:
            excerpt = hit.get('excerpt', '')
            for keyword in keywords:
                if keyword in excerpt:
                    sentences = excerpt.split('。')
                    for sentence in sentences:
                        if keyword in sentence and len(sentence) > 10:
                            tips.append(sentence.strip()[:120])
                            break
                    break
        
        return list(set(tips))[:5]
    
    def _extract_training_methods(self, hits: List[Dict]) -> List[str]:
        """提取训练方法"""
        methods = []
        keywords = ['训练', '锻炼', '康复', '运动', '练习', '改善']
        
        for hit in hits:
            excerpt = hit.get('excerpt', '')
            for keyword in keywords:
                if keyword in excerpt:
                    sentences = excerpt.split('。')
                    for sentence in sentences:
                        if keyword in sentence and len(sentence) > 10:
                            methods.append(sentence.strip()[:120])
                            break
                    break
        
        return list(set(methods))[:5]
    
    def _extract_methods(self, hits: List[Dict]) -> List[str]:
        """提取具体方法"""
        methods = []
        keywords = ['方法', '步骤', '措施', '做法', '可以', '通过']
        
        for hit in hits:
            excerpt = hit.get('excerpt', '')
            for keyword in keywords:
                if keyword in excerpt:
                    sentences = excerpt.split('。')
                    for sentence in sentences:
                        if keyword in sentence and len(sentence) > 10:
                            methods.append(sentence.strip()[:120])
                            break
                    break
        
        return list(set(methods))[:5]
    
    def _combine_contexts(self, contexts: List[str]) -> str:
        """合并多个上下文"""
        valid_contexts = [c.strip() for c in contexts if c and c.strip()]
        if not valid_contexts:
            return ""
        
        # 添加分隔标记
        combined = []
        labels = ["【风险预防知识】", "【慢性病管理指南】", "【功能训练建议】"]
        for i, context in enumerate(valid_contexts):
            if i < len(labels):
                combined.append(f"{labels[i]}\n{context}")
            else:
                combined.append(context)
        
        return "\n\n".join(combined)
    
    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
