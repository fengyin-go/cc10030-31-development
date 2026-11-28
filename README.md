# 航次编排协作台

航运调度员、船长和港口代理共同编排航线、分配船舶、登记货物并跟踪每个航次的装载与到港状态。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 船舶档案 | `vessel` | 船舶 | 船名、总吨位、载重吨 |
| 航线编排 | `route` | 航线 | 起运港、目的港、经停港口 |
| 航次编排 | `voyage` | 航次 | 船舶编号、航线编号、计划离港 |
| 货物登记 | `cargo` | 货物 | 货物品名、托运方、件数 |
| 船员排班 | `crew_shift` | 排班单 | 船员姓名、岗位、上船日期 |
| 燃料记录 | `fuel_log` | 燃料记录 | 船舶编号、加油港口、燃料类型 |
| 港口挂靠 | `port_call` | 挂靠记录 | 航次编号、挂靠港口、预计到港 |
| 箱号登记 | `container` | 集装箱 | 箱号、箱型尺寸、封条编号 |
| 气象预警 | `weather_alert` | 预警通知 | 预警海域、预警类型、预警等级 |
| 预警补正回执 | `weather_receipt` | 补正回执 | 回执编号、受影响航次、待核实航次 |
| 预警处置 | `alert_disposition` | 预警处置 | 处置编号、回执编号、处置类型 |
| 事故记录 | `accident_log` | 事故记录 | 发生时间、发生海域、涉事船舶 |
| 船舶检验 | `inspection` | 检验记录 | 船舶编号、检验类型、检验机构 |
| 通关申报 | `clearance` | 申报单 | 航次编号、申报类型、申报港口 |
| 租船合约 | `charter` | 合约单 | 租用船舶、承租方、日租金 |
| 泊位安排 | `berth` | 泊位安排 | 港口名称、泊位编号、靠泊船舶 |
| 舱单编制 | `manifest` | 舱单 | 航次编号、货物清单、托运方汇总 |
| 航海日志 | `voyage_log` | 日志条目 | 航次编号、记录时刻、船位经纬度 |
| 船舶保养 | `maintenance` | 保养记录 | 船舶编号、保养项目、保养周期 |
| 船员证书 | `crew_cert` | 证书记录 | 船员姓名、证书类型、证书编号 |
| 调度指令 | `dispatch_note` | 指令单 | 接收船舶、指令内容、发出时间 |
| 费率核算 | `tariff` | 费率单 | 服务项目、计费单位、标准费率 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 气象预警补正链路

`app/services/alert_workflow.py` 承载「签收 → 空态回执 → 经停补报 → 改派/继续签认」：

1. `签收预警` 后一定会生成补正回执；签收时没有命中航线，回执处于 `待核对`，
   在「预警补正回执」里可以继续办理，不会只剩一条普通回执。
2. 航线 `确认航线`/`调整经停`/`补报经停`、新航次登记、回执上的 `重新核对`
   都会重新核对受影响航次（同一把锁串行化补报与签认的并发）。
3. 只有航线已确认且经停覆盖预警海域的航次才算可确认受影响，才能 `先办改派`
   领取正式处置；无法确认的航次不能先领处置。
4. 受影响航次 `签认航次` 前会被提示区分 `继续签认` 与 `先办改派`；
   没有可用凭据时提示先在预警上 `重新核对经停` 补办出回执。
5. 同一份回执对同一航次只保留一份处置（重复提交/改签会复用或转换原记录），
   已完成航次不再开放处置资格。

动作参数示例：`POST /api/voyage/2/actions`，body 为
`{"action":"先办改派","values":{"预警编号":"W7"}}`。

领域：应用开发
子方向：web前端/全栈开发
