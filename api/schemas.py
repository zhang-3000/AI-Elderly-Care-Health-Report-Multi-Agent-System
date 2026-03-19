from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChatStartResponse(BaseModel):
    userId: str
    sessionId: str
    welcomeMessage: str


class ChatMessageRequest(BaseModel):
    message: str
    sessionId: str
    context: Optional[Dict[str, Any]] = None


class ChatMessageResponse(BaseModel):
    message: str
    state: str
    progress: float
    completed: bool = False


class ChatProgressResponse(BaseModel):
    state: str
    progress: float
    completedGroups: List[str] = Field(default_factory=list)
    pendingGroups: List[str] = Field(default_factory=list)
    missingFields: Dict[str, List[str]] = Field(default_factory=dict)


class ChatHistoryMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class ReportGenerateRequest(BaseModel):
    profile: Dict[str, Any] = Field(default_factory=dict)
    sessionId: Optional[str] = None


class RiskItem(BaseModel):
    name: str
    level: str
    description: str
    timeframe: str


class RecommendationItem(BaseModel):
    id: str
    title: str
    description: str
    category: str
    completed: bool = False


class ReportData(BaseModel):
    summary: str
    healthPortrait: Dict[str, Any]
    riskFactors: Dict[str, List[RiskItem]]
    recommendations: Dict[str, List[RecommendationItem]]
    generatedAt: str


class ReportGenerateByElderlyResponse(BaseModel):
    reportId: str
    sessionId: str
    report: ReportData


class AgentStatusEvent(BaseModel):
    agent: str
    status: str
    message: Optional[str] = None


class LoginRequest(BaseModel):
    phone: str
    password: str
