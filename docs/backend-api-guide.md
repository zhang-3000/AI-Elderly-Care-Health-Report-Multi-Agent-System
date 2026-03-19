# AI Elderly Care Backend API Guide

> 生成时间：2026-03-19
>
> 本文档基于当前后端代码和实际 FastAPI 路由表整理，适合前端对接使用。

## 1. 项目功能概览

这个后端主要提供以下能力：

1. 对话式采集老人健康画像
2. 基于多 Agent 的健康评估与报告生成
3. 会话工作区管理
4. 家属端老人资料查询与编辑
5. 语音流转文字

核心代码位置：

- `api/server.py`：FastAPI 入口与路由
- `code/memory/conversation_manager.py`：对话状态机
- `code/multi_agent_system_v2.py`：多 Agent 编排与画像结构
- `core/workspace_manager.py`：会话工作区存储

## 2. 当前后端实际暴露的接口

当前服务实际挂载的接口如下：

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
- `POST /auth/login`
- `POST /auth/logout`
- `GET /family/elderly-list`
- `GET /family/elderly/{elderly_id}`
- `PUT /family/elderly/{elderly_id}`
- `GET /family/reports/{elderly_id}`
- `POST /report/generate/{elderly_id}`

默认启动端口在代码中配置为 `8001`。

## 3. 通用说明

### 3.1 Base URL

```txt
http://<host>:8001
```

### 3.2 CORS

当前 CORS 全开放：

- `allow_origins = ["*"]`
- `allow_methods = ["*"]`
- `allow_headers = ["*"]`

### 3.3 认证现状

虽然存在 `/auth/login` 和 `/auth/logout`，但除登录接口外，其它接口当前没有真正做鉴权拦截。

## 4. API 文档

## 4.1 健康检查

### `GET /api/health`

返回服务状态。

响应示例：

```json
{
  "status": "healthy",
  "timestamp": "2026-03-19T13:00:00",
  "service": "AI 养老健康助手 API"
}
```

## 4.2 会话工作区

### `GET /api/sessions`

获取工作区中的会话列表。

响应示例：

```json
{
  "sessions": [
    {
      "session_id": "8c1a8c8f-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "user_id": "0c2e4d0d-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "created_at": "2026-03-19T13:00:00",
      "status": "active",
      "title": "评估记录 - 03-19 13:00",
      "has_report": true,
      "has_profile": true
    }
  ]
}
```

### `GET /api/sessions/{session_id}`

获取指定会话的完整工作区数据。

响应示例：

```json
{
  "metadata": {
    "session_id": "xxx",
    "created_at": "2026-03-19T13:00:00"
  },
  "conversation": [
    {
      "role": "user",
      "content": "老人82岁",
      "timestamp": "2026-03-19T13:01:00"
    }
  ],
  "profile": {
    "age": 82,
    "sex": "男"
  },
  "reports": []
}
```

### `POST /api/sessions/{session_id}/profile`

把前端画像存入工作区文件。

请求体：

```json
{
  "age": 82,
  "sex": "男",
  "residence": "城市"
}
```

响应示例：

```json
{
  "success": true
}
```

### `DELETE /api/sessions/{session_id}`

删除某个工作区会话。

响应示例：

```json
{
  "success": true
}
```

## 4.3 聊天评估

### `POST /chat/start`

创建新用户和新会话，返回欢迎语。

请求体：无

响应示例：

```json
{
  "userId": "9e72c4d5-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "sessionId": "0fdf5538-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "welcomeMessage": "您好😊 我想先了解一下您的日常身体和生活情况..."
}
```

### `POST /chat/message`

发送一轮消息并返回 AI 回复。

请求体：

```json
{
  "message": "老人82岁，男，北京农村",
  "sessionId": "0fdf5538-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "context": {}
}
```

响应示例：

```json
{
  "message": "好的，我先记录一下基本信息...",
  "state": "collecting",
  "progress": 0.24,
  "completed": false
}
```

`state` 可能值：

- `greeting`
- `collecting`
- `confirming`
- `generating`
- `completed`
- `follow_up`

### `GET /chat/history/{session_id}`

获取某个会话的聊天历史。

响应示例：

```json
[
  {
    "role": "user",
    "content": "老人82岁",
    "timestamp": "2026-03-19T13:01:00"
  },
  {
    "role": "assistant",
    "content": "好的，请继续告诉我性别和居住地。",
    "timestamp": "2026-03-19T13:01:03"
  }
]
```

### `GET /chat/profile/{session_id}`

获取当前结构化画像。

响应示例：

```json
{
  "age": 82,
  "sex": "男",
  "province": "北京",
  "residence": "农村",
  "education_years": null,
  "marital_status": null,
  "health_limitation": null,
  "badl_bathing": null
}
```

### `GET /chat/progress/{session_id}`

名义上用于获取当前问卷进度。

当前实现是占位版本，固定返回：

```json
{
  "state": "collecting",
  "progress": 0.0,
  "completedGroups": [],
  "pendingGroups": [],
  "missingFields": {}
}
```

前端不要依赖这个接口做真实进度条。

### `GET /chat/stream?message=...&sessionId=...`

聊天 SSE 接口。

返回格式示例：

```txt
data: {"content":"你"}

data: {"content":"好"}

data: [DONE]
```

适合做打字机式逐字显示。

## 4.4 语音转文字

### `WS /ws/stt`

前端先发送启动消息：

```json
{
  "type": "start",
  "lang": "cmn-Hans-CN"
}
```

服务端准备完成后返回：

```json
{
  "type": "ready",
  "engine": "google_stt_v2"
}
```

之后前端发送二进制 PCM 音频流。

服务端可能返回以下事件：

```json
{
  "type": "transcript",
  "text": "今天状态不错",
  "isFinal": false
}
```

```json
{
  "type": "speech_event",
  "event": "begin"
}
```

```json
{
  "type": "speech_event",
  "event": "end"
}
```

```json
{
  "type": "error",
  "message": "Google 语音识别失败: ..."
}
```

结束时发送：

```json
{
  "type": "stop"
}
```

## 4.5 认证

### `POST /auth/login`

当前是演示接口，只校验手机号和密码是否为空。

请求体：

```json
{
  "phone": "13800138000",
  "password": "123456"
}
```

响应示例：

```json
{
  "token": "token_13800138000_1773897576.51613",
  "user_name": "用户8000",
  "role": "family"
}
```

### `POST /auth/logout`

响应示例：

```json
{
  "success": true
}
```

## 4.6 家属端

### `GET /family/elderly-list`

获取老人列表。

响应示例：

```json
{
  "data": [
    {
      "elderly_id": "c77f8a17-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "name": "未命名",
      "relation": "家庭成员",
      "completion_rate": 0.8,
      "created_at": "2026-03-19T13:00:00"
    }
  ]
}
```

注意：

- `name` 很多时候可能拿不到真实姓名
- `completion_rate` 目前是演示值
- `created_at` 也是演示生成

### `GET /family/elderly/{elderly_id}`

获取老人详情。

响应示例：

```json
{
  "elderly_id": "c77f8a17-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "profile": {
    "age": 82,
    "sex": "男",
    "province": "北京"
  }
}
```

### `PUT /family/elderly/{elderly_id}`

更新老人画像。

请求体示例：

```json
{
  "age": 82,
  "sex": "男",
  "hypertension": "是",
  "diabetes": "否"
}
```

响应示例：

```json
{
  "success": true
}
```

### `GET /family/reports/{elderly_id}`

名义上获取老人报告列表。

响应示例：

```json
{
  "data": []
}
```

当前实现大概率返回空数组，因为它查找报告的目录和文件名规则与实际保存规则不一致。

## 4.7 报告生成

### `POST /report/generate/{elderly_id}`

按当前实际路由表，这个接口已挂载。

请求体：

```json
{
  "age": 82,
  "sex": "男",
  "residence": "农村"
}
```

但是当前实现不可用。

原因：

- 代码里引用了未定义变量 `orchestrator`
- 调用时会直接抛出 `NameError`

因此前端现阶段不要接这个接口。

## 5. 用户画像字段定义

当前结构化画像字段来自后端 `UserProfile`。

### 5.1 基本信息

- `age`
- `sex`
- `province`
- `residence`
- `education_years`
- `marital_status`

### 5.2 健康限制

- `health_limitation`

### 5.3 BADL

- `badl_bathing`
- `badl_dressing`
- `badl_toileting`
- `badl_transferring`
- `badl_continence`
- `badl_eating`

### 5.4 IADL

- `iadl_visiting`
- `iadl_shopping`
- `iadl_cooking`
- `iadl_laundry`
- `iadl_walking`
- `iadl_carrying`
- `iadl_crouching`
- `iadl_transport`

### 5.5 慢性病

- `hypertension`
- `diabetes`
- `heart_disease`
- `stroke`
- `cataract`
- `cancer`
- `arthritis`

### 5.6 认知功能

- `cognition_time`
- `cognition_month`
- `cognition_season`
- `cognition_place`
- `cognition_calc`
- `cognition_draw`

### 5.7 心理状态

- `depression`
- `anxiety`
- `loneliness`

### 5.8 生活方式

- `smoking`
- `drinking`
- `exercise`
- `sleep_quality`

### 5.9 身体指标

- `weight`
- `height`
- `vision`
- `hearing`

### 5.10 社会支持

- `living_arrangement`
- `cohabitants`
- `financial_status`
- `income`
- `medical_insurance`
- `caregiver`

## 6. 前端对接建议

如果你现在要先把前端搭起来，建议优先接这些接口：

1. `POST /chat/start`
2. `POST /chat/message`
3. `GET /chat/history/{session_id}`
4. `GET /chat/profile/{session_id}`
5. `GET /api/sessions`
6. `GET /api/sessions/{session_id}`
7. `GET /family/elderly-list`
8. `GET /family/elderly/{elderly_id}`
9. `PUT /family/elderly/{elderly_id}`
10. `WS /ws/stt`

建议先不要接这些接口：

1. `GET /chat/progress/{session_id}`，因为现在是占位实现
2. `GET /family/reports/{elderly_id}`，因为当前逻辑大概率拿不到真实报告
3. `POST /report/generate/{elderly_id}`，因为当前实现会报错

## 7. 代码中存在但当前没有挂载的接口

下面这组接口在代码文件里定义过，但当前服务实际没有挂载：

- `POST /family/session/start/{elderly_id}`
- `POST /family/session/{session_id}/message`
- `GET /family/session/{session_id}/info`
- `POST /report/generate`
- `POST /report/stream`
- `GET /report/{report_id}`
- `GET /report/{report_id}/export/pdf`

这意味着前端不要按这些路径开发，除非后端先修复路由覆盖问题。

## 8. 结论

当前后端最适合前端先做的是：

1. 评估对话页
2. 老人档案详情页
3. 家属管理页
4. 会话历史页
5. 语音输入能力

报告生成和家属会话这两块，后端还需要先修一轮，前端再正式接入会更稳。
