# 订单管理 mini 应用（demo）

AI 原生低代码平台 MVP 中「被改的目标产品」，同时是 `ReactViteStackAdapter` 与集成测试的测试夹具。

## 一键启动

```bash
cd demo
docker compose up --build
```

- 前端：http://localhost:5173
- 后端 API：http://localhost:8000/api/health
- MySQL：localhost:3307（root / demopass）

## 路由表

| 路由 | 页面组件 | 文件 |
|---|---|---|
| `/` | Dashboard | `frontend/src/pages/Dashboard.tsx` |
| `/orders` | OrderList | `frontend/src/pages/OrderList.tsx` |
| `/orders/:id` | OrderDetail | `frontend/src/pages/OrderDetail.tsx` |
| `/settings` | Settings | `frontend/src/pages/Settings.tsx` |

路由表定义在 `frontend/src/router.tsx` 的 `routes` 导出 —— StackAdapter 的契约锚点，勿随意改动。

## 本地开发（不走容器）

后端：
```bash
docker run -d --name demo-mysql -e MYSQL_ROOT_PASSWORD=demopass -p 3307:3306 mysql:8
cd backend && pip install -e ".[dev]"
DATABASE_URL=mysql+pymysql://root:demopass@localhost:3307/demo uvicorn demo_backend.main:app --port 8000
```

前端：
```bash
cd frontend && npm install && npm run dev
```

## 测试

后端（需 MySQL 在 localhost:3307，且存在 demo_test 库）：
```bash
cd backend && TEST_DATABASE_URL=mysql+pymysql://root:demopass@localhost:3307/demo_test pytest
```

前端：
```bash
cd frontend && npm test
```
