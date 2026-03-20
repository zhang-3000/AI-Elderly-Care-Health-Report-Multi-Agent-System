# AI Elderly Care Backend API Guide

> 更新时间：2026-03-20
>
> 本文档基于当前 FastAPI 实际路由与后端实现整理，适合前端和联调使用。

## 1. 项目能力

当前后端提供以下核心能力：

1. 老人端对话式健康画像采集
2. 基于多 Agent 的报告生成
3. 会话工作区管理
4. 家属账号注册、登录与老人绑定
5. 医生账号登录、全量老人总览与随访管理
6. 基于 token 的访问控制
7. 语音流式转文字

核心文件：

- `api/server.py`：FastAPI 入口与主路由
- `api/auth_service.py`：家属/医生账号、绑定关系、token 签发与校验
- `api/security.py`：权限校验辅助函数
- `api/auth_routes.py`：认证接口
- `api/doctor_routes.py`：医生侧聚合接口
- `api/family_routes.py`：家属侧接口
- `api/elderly_routes.py`：老人本人视角接口
- `core/workspace_manager.py`：工作区文件存储

默认端口：`8001`

## 2. 认证模型

### 2.1 老人端

老人目前不走传统账号密码登录。

调用 `POST /chat/start` 后，后端会：

1. 创建老人 `userId`
2. 创建首个会话 `sessionId`
3. 返回欢迎语
4. 签发老人访问 token

响应示例：

```json
{
  "userId": "f6f493d0-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "sessionId": "f8cc1c47-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "welcomeMessage": "您好！我是AI养老健康助手。",
  "accessToken": "<elderly-token>",
  "userType": "elderly",
  "expiresAt": "2026-09-15T08:00:00+00:00"
}
```

前端必须持久化这个 `accessToken`，并在后续老人受保护接口中带上：

```txt
Authorization: Bearer <elderly-token>
```

### 2.2 家属端

家属走真实注册/登录：

- `POST /auth/family/register`
- `POST /auth/login`

注册时必须绑定至少一位老人。登录成功后会返回家属 token 和已绑定老人列表。

响应示例：

```json
{
  "token": "<family-token>",
  "expires_at": "2026-04-18T08:00:00+00:00",
  "user_name": "张家属",
  "role": "family",
  "family_id": "8cbe1a3d-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "elderly_ids": [
    "f6f493d0-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  ]
}
```

同样使用：

```txt
Authorization: Bearer <family-token>
```

### 2.3 医生端

医生端走独立账号登录：

- `POST /auth/login`

请求体需要显式带：

```json
{
  "phone": "13900139000",
  "password": "doctor123",
  "role": "doctor"
}
```

当前后端不开放医生注册接口，而是在服务启动时读取以下环境变量初始化默认医生账号：

- `DOCTOR_DEFAULT_NAME`
- `DOCTOR_DEFAULT_PHONE`
- `DOCTOR_DEFAULT_PASSWORD`

医生登录成功后返回 doctor token：

```json
{
  "token": "<doctor-token>",
  "expires_at": "2026-04-19T08:00:00+00:00",
  "user_name": "李医生",
  "role": "doctor",
  "elderly_ids": []
}
```

### 2.4 权限规则

- 老人 token 只能访问自己的会话、画像、报告。
- 家属 token 只能访问与当前家属绑定的老人数据。
- 医生 token 可读取全部老人、全部会话、全部报告。
- 医生 token 可维护医生侧随访记录和管理状态，但不可修改画像、删除会话、生成报告。
- `GET /report/{report_id}`、`GET /api/sessions/{session_id}` 等接口都会按报告/会话归属做校验。
- `POST /report/generate` 与 `POST /report/stream` 现在必须提供 `sessionId`，避免生成无归属报告。

## 3. 路由总览

当前实际挂载路由：

- `GET /api/health`
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/profile`
- `DELETE /api/sessions/{session_id}`
- `POST /chat/start`
- `POST /chat/message`
- `GET /chat/history/{session_id}`
- `GET /chat/progress/{session_id}`
- `GET /chat/profile/{session_id}`
- `GET /chat/stream`
- `WS /ws/stt`
- `POST /report/generate`
- `POST /report/stream`
- `POST /report/generate/{elderly_id}`
- `GET /report/{report_id}`
- `GET /report/{report_id}/export/pdf`
- `POST /auth/family/register`
- `POST /auth/family/bind`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /doctor/elderly-list`
- `GET /doctor/elderly/{elderly_id}`
- `GET /doctor/elderly/{elderly_id}/followups`
- `POST /doctor/elderly/{elderly_id}/followups`
- `PATCH /doctor/elderly/{elderly_id}/management`
- `POST /family/session/start/{elderly_id}`
- `POST /family/session/{session_id}/message`
- `GET /family/session/{session_id}/info`
- `GET /family/elderly-list`
- `GET /family/elderly/{elderly_id}`
- `PUT /family/elderly/{elderly_id}`
- `GET /family/reports/{elderly_id}`
- `GET /elderly/me/profile`
- `GET /elderly/me/reports`
- `GET /elderly/me/reports/{report_id}`

## 4. 鉴权要求

### 4.1 无需鉴权

- `GET /api/health`
- `POST /chat/start`
- `WS /ws/stt`
- `POST /auth/family/register`
- `POST /auth/login`
- `POST /auth/logout`

### 4.2 仅老人本人可访问

- `POST /chat/message`
- `GET /chat/history/{session_id}`
- `GET /chat/progress/{session_id}`
- `GET /chat/profile/{session_id}`
- `GET /chat/stream`
- `GET /elderly/me/profile`
- `GET /elderly/me/reports`
- `GET /elderly/me/reports/{report_id}`

### 4.3 老人本人、已绑定家属或医生可访问

- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `GET /report/{report_id}`
- `GET /report/{report_id}/export/pdf`

### 4.4 仅老人本人或已绑定家属可写

- `POST /api/sessions/{session_id}/profile`
- `DELETE /api/sessions/{session_id}`
- `POST /report/generate`
- `POST /report/stream`
- `POST /report/generate/{elderly_id}`

### 4.5 仅家属账号可访问

- `POST /auth/family/bind`
- `POST /family/session/start/{elderly_id}`
- `POST /family/session/{session_id}/message`
- `GET /family/session/{session_id}/info`
- `GET /family/elderly-list`
- `GET /family/elderly/{elderly_id}`
- `PUT /family/elderly/{elderly_id}`
- `GET /family/reports/{elderly_id}`

### 4.6 仅医生账号可访问

- `GET /doctor/elderly-list`
- `GET /doctor/elderly/{elderly_id}`
- `GET /doctor/elderly/{elderly_id}/followups`
- `POST /doctor/elderly/{elderly_id}/followups`
- `PATCH /doctor/elderly/{elderly_id}/management`

## 5. 接口说明

## 5.1 健康检查

### `GET /api/health`

响应示例：

```json
{
  "status": "healthy",
  "timestamp": "2026-03-19T13:00:00",
  "service": "AI 养老健康助手 API"
}
```

## 5.2 老人对话流程

### `POST /chat/start`

创建老人用户、会话，并返回老人 token。

### `POST /chat/message`

请求体：

```json
{
  "message": "老人82岁，男，北京农村",
  "sessionId": "f8cc1c47-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "context": {}
}
```

响应示例：

```json
{
  "message": "好的，我先记录一下基本信息。",
  "state": "collecting",
  "progress": 0.24,
  "completed": false
}
```

### `GET /chat/history/{session_id}`

返回当前会话历史。

### `GET /chat/progress/{session_id}`

现在返回真实进度，不再是固定占位值。

响应示例：

```json
{
  "state": "collecting",
  "progress": 0.18,
  "completedGroups": ["基本信息"],
  "pendingGroups": ["健康限制", "日常活动（BADL）"],
  "missingFields": {
    "健康限制": ["health_limitation"]
  }
}
```

### `GET /chat/profile/{session_id}`

返回当前结构化画像。

### `GET /chat/stream?message=...&sessionId=...`

老人端 SSE 对话接口。

## 5.3 工作区与会话

### `GET /api/sessions`

返回当前主体可见的会话列表：

- 老人只看到自己的会话
- 家属只看到已绑定老人对应的会话
- 医生可以看到全部会话

### `GET /api/sessions/{session_id}`

返回会话元数据、对话历史、画像和报告列表。

### `POST /api/sessions/{session_id}/profile`

保存工作区画像文件。

说明：

- 老人和已绑定家属可写
- 医生只读，调用会返回 `403`

### `DELETE /api/sessions/{session_id}`

删除指定工作区会话。

说明：

- 老人和已绑定家属可删
- 医生只读，调用会返回 `403`

## 5.4 报告

### `POST /report/generate`

按给定画像生成报告，必须带 `sessionId`。

请求体：

```json
{
  "sessionId": "f8cc1c47-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "profile": {
    "age": 82,
    "sex": "男",
    "residence": "农村"
  }
}
```

说明：

- 该接口会校验当前 token 是否有权访问这个 `sessionId`
- 医生没有生成权限，调用会返回 `403`
- 生成的报告会记录归属老人，后续查询按归属做鉴权

### `POST /report/stream`

报告流式生成接口，要求与 `POST /report/generate` 相同，也必须带 `sessionId`。

### `POST /report/generate/{elderly_id}`

合并老人已有画像并生成报告。

访问规则：

- 老人只能给自己生成
- 家属只能给已绑定老人生成
- 医生没有生成权限

响应示例：

```json
{
  "reportId": "20260319_130000_123456",
  "sessionId": "f8cc1c47-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "report": {
    "summary": "整体情况需要持续观察。",
    "healthPortrait": {},
    "riskFactors": {
      "shortTerm": [],
      "midTerm": []
    },
    "recommendations": {
      "priority1": [],
      "priority2": [],
      "priority3": []
    },
    "generatedAt": "2026-03-19T13:00:00"
  }
}
```

### `GET /report/{report_id}`

按报告 ID 获取标准化报告。

访问规则：

- 老人只能读取自己的报告
- 家属只能读取已绑定老人报告
- 医生可以读取所有报告

### `GET /report/{report_id}/export/pdf`

当前仍返回：

```json
{
  "detail": "PDF 导出功能待实现"
}
```

但在返回 `501` 之前已经会先做权限校验。

## 5.5 家属认证与绑定

### `POST /auth/family/register`

注册家属并绑定首位老人。

请求体：

```json
{
  "name": "张家属",
  "phone": "13800138000",
  "password": "secret123",
  "elderlyId": "f6f493d0-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "relation": "子女"
}
```

### `POST /auth/family/bind`

为当前家属追加绑定老人。

请求体：

```json
{
  "elderlyId": "another-elderly-id",
  "relation": "配偶"
}
```

### `POST /auth/login`

按角色执行登录。

默认 `role = "family"`，如果是医生端必须显式传 `role = "doctor"`。

家属登录示例：

```json
{
  "phone": "13800138000",
  "password": "secret123"
}
```

医生登录示例：

```json
{
  "phone": "13900139000",
  "password": "doctor123",
  "role": "doctor"
}
```

### `POST /auth/logout`

当前为幂等空操作，统一返回：

```json
{
  "success": true
}
```

## 5.6 家属数据接口

### `GET /family/elderly-list`

返回当前家属已绑定老人列表，不再返回全量老人。

### `GET /family/elderly/{elderly_id}`

返回指定已绑定老人的结构化画像。

### `PUT /family/elderly/{elderly_id}`

更新指定已绑定老人画像。

### `GET /family/reports/{elderly_id}`

返回指定已绑定老人的报告列表。

### `POST /family/session/start/{elderly_id}`

为已绑定老人创建家属侧会话。

### `POST /family/session/{session_id}/message`

发送家属侧对话消息。

### `GET /family/session/{session_id}/info`

获取家属侧会话状态信息。

## 5.7 老人本人视角接口

### `GET /elderly/me/profile`

返回当前老人自己的画像。

### `GET /elderly/me/reports`

返回当前老人自己的报告列表。

### `GET /elderly/me/reports/{report_id}`

返回当前老人自己的指定报告。

## 5.8 医生端接口

### `GET /doctor/elderly-list`

返回医生可查看的老人聚合总览。

响应字段：

- `elderly_id`
- `name`
- `created_at`
- `updated_at`
- `has_profile`
- `has_report`
- `session_count`
- `report_count`
- `latest_session_id`
- `latest_report_id`
- `overview`
- `management`
- `latest_followup`

其中 `overview` 为医生端派生摘要，不会改写老人原始画像，当前包含：

- `chronic_conditions`
- `current_risk_level`
- `functional_status_level`
- `risk_tags`
- `recent_change`
- `main_problems`
- `high_risk_reasons`
- `recommended_actions`
- `latest_report_review`

`latest_report_review` 来自最新报告的复核结果，当前会聚合：

- 一致性检查结果与问题列表
- 安全性提示
- 可执行性检查
- 完整性检查
- 总体质量与建议

`management` 与 `latest_followup` 为医生端独立管理数据，只保存在医生侧表中，不会回写老人画像或改变老人/家属对话流程。

### `GET /doctor/elderly/{elderly_id}`

返回医生视角的单个老人详情，包含：

- 基础标识信息
- 当前结构化画像
- 该老人全部工作区会话元数据
- 该老人全部报告摘要
- `overview`
- `management`
- `followups`

### `GET /doctor/elderly/{elderly_id}/followups`

返回该老人的医生随访记录列表：

```json
{
  "data": [
    {
      "followup_id": "uuid",
      "elderly_user_id": "uuid",
      "doctor_id": "uuid",
      "visit_type": "电话",
      "findings": "近一周夜间起身增多。",
      "recommendations": ["两周内复评步态"],
      "contacted_family": true,
      "arranged_revisit": true,
      "referred": false,
      "next_followup_at": "2026-04-05T10:00:00",
      "notes": "建议继续观察夜间如厕风险",
      "created_at": "2026-03-20T08:00:00+00:00",
      "updated_at": "2026-03-20T08:00:00+00:00"
    }
  ]
}
```

### `POST /doctor/elderly/{elderly_id}/followups`

保存一条医生随访记录。

请求体：

```json
{
  "visitType": "电话",
  "findings": "近一周夜间起身增多，家属反馈步态较前变慢。",
  "recommendations": ["两周内复评步态", "提醒家属加强夜间照护"],
  "contactedFamily": true,
  "arrangedRevisit": true,
  "referred": false,
  "nextFollowupAt": "2026-04-05T10:00:00",
  "notes": "建议继续观察夜间如厕风险"
}
```

说明：

- `visitType` 当前仅支持 `门诊`、`电话`、`上门`
- 保存随访后，会同步刷新医生侧 `management` 的最近随访信息
- 不会修改老人原始画像

### `PATCH /doctor/elderly/{elderly_id}/management`

更新医生端管理状态。

请求体字段均为可选：

```json
{
  "isKeyCase": true,
  "managementStatus": "priority_follow_up",
  "contactedFamily": true,
  "arrangedRevisit": true,
  "referred": false,
  "nextFollowupAt": "2026-04-05T10:00:00"
}
```

说明：

- 这是医生侧管理层，不会覆盖老人画像字段
- 适合前端做“标记重点管理对象”“已联系家属”“已安排复诊/复评”等状态管理

## 5.9 语音转文字

### `WS /ws/stt`

启动消息：

```json
{
  "type": "start",
  "lang": "cmn-Hans-CN"
}
```

准备完成后：

```json
{
  "type": "ready",
  "engine": "google_stt_v2"
}
```

结束时发送：

```json
{
  "type": "stop"
}
```

## 6. 前端接入要点

1. 老人端必须保存 `POST /chat/start` 返回的 `accessToken`。
2. 所有受保护接口都要带 `Authorization: Bearer <token>`。
3. 家属端不能再默认看到全量老人，只能看到绑定老人。
4. `POST /report/generate` 和 `POST /report/stream` 必须提供 `sessionId`。
5. `POST /report/generate/{elderly_id}` 已可用，不再是不可用接口。
6. `GET /chat/progress/{session_id}` 已返回真实进度，可用于前端进度展示。
7. 医生端登录调用 `POST /auth/login` 时必须传 `role: "doctor"`。
8. 如需默认医生账号，部署时必须提供 `DOCTOR_DEFAULT_NAME`、`DOCTOR_DEFAULT_PHONE`、`DOCTOR_DEFAULT_PASSWORD`。

## 7. 当前限制

1. 老人端 token 由 `POST /chat/start` 直接签发，当前没有单独的老人登录页。
2. `POST /auth/logout` 目前不做服务端 token 撤销。
3. `GET /report/{report_id}/export/pdf` 尚未实现 PDF 导出，仅完成鉴权。
4. 医生端当前支持读取全部老人数据，并维护医生侧随访记录与管理状态；仍不支持编辑老人画像、删除会话、生成报告。
5. 医生账号当前不开放注册，只支持环境变量初始化默认账号。
