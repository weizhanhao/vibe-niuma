#!/usr/bin/env python3
"""灌一个可演示的空间：两个成员、三个仓、几条不同阶段的需求。

用法：VP_DATABASE_URL=sqlite:///demo.db python scripts/seed_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vplatform.core import db as dbmod
from vplatform.core.models import (
    Finding, Member, MergeJob, Org, Project, ProjectRepo, Requirement, Run, Task,
    TaskTouch, next_requirement_seq,
)


def seed(url: str) -> str:
    dbmod.init_engine(url, create_all=True)
    with dbmod.session_scope() as s:
        if s.query(Project).filter_by(slug="mc").first():
            return "已存在，跳过"

        org = Org(name="Acme")
        s.add(org); s.flush()
        p = Project(org_id=org.id, name="商户中台", slug="mc",
                    target_branch="vibe/dev",
                    secret_refs={"llm": "env:DASHSCOPE_API_KEY"})
        s.add(p); s.flush()
        s.add_all([
            ProjectRepo(project_id=p.id, name="merchant-web", url="https://github.com/acme/merchant-web.git"),
            ProjectRepo(project_id=p.id, name="merchant-api", url="https://github.com/acme/merchant-api.git"),
            ProjectRepo(project_id=p.id, name="merchant-bff", url="https://github.com/acme/merchant-bff.git"),
            Member(project_id=p.id, user_id="chen", display_name="陈曦", role="requester"),
            Member(project_id=p.id, user_id="zhao", display_name="赵敏", role="reviewer"),
            Member(project_id=p.id, user_id="admin", display_name="管理员", role="admin"),
        ])

        def req(title, stage, by="chen", **kw):
            r = Requirement(project_id=p.id, seq=next_requirement_seq(s, p.id),
                            title=title, requested_by=by, stage=stage, **kw)
            s.add(r); s.flush()
            return r

        # 1) 并行开发中 —— 三仓契约解耦
        big = req("订单导出支持自定义字段 + 异步下载", "implement",
                  body="财务每月要手工加工导出结果；大批量导出还会卡住页面。",
                  contracts=["POST /export/orders {fields, filter} → {jobId}",
                             "GET /export/jobs/:jobId → {status, progress, url?}"])
        for key, title, repo, touches, state in [
            ("T1", "导出弹窗加字段选择器", "merchant-web",
             ["src/pages/Orders/ExportDialog.tsx", "src/api/export.ts"], "done"),
            ("T2", "导出任务异步化 + 进度查询", "merchant-api",
             ["app/routers/export.py", "app/tasks/export_job.py"], "running"),
            ("T3", "字段白名单透传 + 下载链接签名", "merchant-bff",
             ["src/routes/export.ts"], "done"),
        ]:
            t = Task(project_id=p.id, requirement_id=big.id, key=key, title=title,
                     repo_names=[repo], state=state)
            s.add(t); s.flush()
            for path in touches:
                s.add(TaskTouch(project_id=p.id, task_id=t.id, path=path, repo_name=repo))

        # 2) 待审核 —— 带两轴复核发现
        pending = req("商品详情页价格改成含税展示", "review", by="zhao")
        t = Task(project_id=p.id, requirement_id=pending.id, key="T1",
                 title="价格渲染改含税", repo_names=["merchant-web"], state="done")
        s.add(t); s.flush()
        run = Run(project_id=p.id, task_id=t.id, branch="cr/2-t1", state="done")
        s.add(run); s.flush()
        s.add_all([
            Finding(project_id=p.id, run_id=run.id, axis="defect", severity="high",
                    category="correctness", path="src/pages/ProductDetail.tsx",
                    start_line=88, claim="taxRate 为 undefined 时价格算成 NaN",
                    failure_scenario="老 SKU 没有 taxRate 字段 → price * (1 + undefined) "
                                     "→ NaN → 页面显示「¥NaN」",
                    kept=True, confidence="high", verdict_reason="有明确失败场景与数据后果"),
            Finding(project_id=p.id, run_id=run.id, axis="norm", severity="low",
                    category="convention", path="src/pages/ProductDetail.tsx",
                    start_line=12, claim="新增常量没放进 constants.ts",
                    kept=False, confidence="high",
                    verdict_reason="纯维护性建议，无具体失败场景"),
        ])

        # 3) 合并队列 —— 一条在跑、一条排队
        m1 = req("登录页加企业微信扫码", "merge", by="chen")
        m2 = req("对账单增加月度汇总", "merge", by="chen")
        s.add_all([
            MergeJob(project_id=p.id, requirement_id=m1.id, repo_name="merchant-web",
                     position=1, state="rebasing"),
            MergeJob(project_id=p.id, requirement_id=m2.id, repo_name="merchant-api",
                     position=1, state="queued"),
        ])

        # 4) wide refactor —— touches 大面积相交是预期的（§8.4）
        wide = req("把 order_no 字段重命名为 order_code", "decompose",
                   sequence_kind="expand")
        for key, seq, title in [("T1", "expand", "新增 order_code 与 order_no 并存"),
                                ("T2", "migrate", "迁移 api 包调用点"),
                                ("T3", "contract", "删除 order_no")]:
            t = Task(project_id=p.id, requirement_id=wide.id, key=key, title=title,
                     repo_names=["merchant-api"], sequence=seq,
                     depends_on=["T1"] if seq != "expand" else [])
            s.add(t); s.flush()
            s.add(TaskTouch(project_id=p.id, task_id=t.id,
                            path="app/models/order.py", repo_name="merchant-api"))

        # 5) 澄清中
        req("订单列表要能按门店筛选", "clarify", by="chen")
        return "已灌入"


if __name__ == "__main__":
    import os
    url = os.environ.get("VP_DATABASE_URL", "sqlite:///demo.db")
    print(seed(url), "→", url)
