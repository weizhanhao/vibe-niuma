# Plan 7 — 多仓真原子合并 + compose 预览

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持「一个项目 = 一个文件夹，下面 N 个子目录各有自己的 `.git`」的形态（典型：`frontend/.git` + `backend/.git`）。dev runner 把项目当一体看，AGENTS.md 写在项目顶层；git 层各自管理。合并到 main 走**两阶段原子算法**：要么 N 个子仓全部 ff-merge 成功，要么全部 `git update-ref` 回滚到合并前的 SHA，绝不留中间态。预览容器从「只起前端」升级到「docker compose 起前后端一组」，自动加入 doskill-net，业务员看到的是「真数据 + 你的新 UI / API」。

**Architecture:**
- **项目文件夹**：`/opt/doskill/projects/<name>/` 顶层包含 N 个子目录，每个有自己的 `.git/`，外加一个项目级 `AGENTS.md`、`docker-compose.preview.yml`。
- **子仓发现**：`discover_sub_repos(project_path)` 扫顶层目录，凡是含 `.git/` 的都是一个子仓。返回 `list[Path]`（按名字字典序）。
- **git_manager 多仓改造**：`create_branch` / `commit_all` / `has_changes` / `delete_branch` 全部遍历子仓。每个 cr 分支用同一个 id：`cr/<cr_id>` 在 frontend 和 backend 各建一个。
- **原子合并**（核心）：`merge_to_main_atomic(sub_repos, branch)`：
  - **Phase 1 dry-rebase**：每仓 stash → rebase main，失败就 abort + 用 `git update-ref` 把所有已 rebase 成功的子仓 cr SHA 还原回保存值，抛 `GitConflictError(repo_name, conflict_files)`。
  - **Phase 2 ff-merge**：每仓 checkout main + `merge --ff-only`，失败（极小概率，e.g. main 被并发推）→ 用 `update-ref refs/heads/main` 还原已 merged 仓 + 还原所有 cr 分支。
  - 任一阶段成功路径最后 `git stash drop`（build artifact 是临时产物）。
- **`DockerPreviewAdapter` compose 模式**：读项目根的 `docker-compose.preview.yml`（fallback 到老的「只起 frontend」），`docker compose -p doskill-preview-<id> up -d`，分配的 host 端口写回 PreviewInstance；teardown 时 `docker compose down -v`。
- **SSE log 粒度**：每个子仓的 git 操作都打 log 行（`[frontend] git rebase main ...`）；冲突时报具体哪个仓的哪个文件。

**Tech Stack:** 沿用现有 —— FastAPI + pytest（多仓 fixture 用 tmp_path + 多次 `git init`）；`docker compose` CLI v2（systemd 默认装的就是）。

---

## 前置约定（每个任务都假定已满足）

- Plan 6 已合并 main，扩展端能在 SettingsPanel 配置「项目根路径」和「子仓 git URL 列表」。
- 项目根目录约定：每个子目录顶层有自己的 `.git/`，所有子仓的「主分支」名字统一是 `main`（不支持 `master` 混搭，超出 MVP 范围）。
- `docker-compose.preview.yml` 是**项目作者**的责任（demo 仓库提供一个范例）：必须有一个名为 `frontend` 的 service，host 端口由 doskill 注入 `${DOSKILL_FRONTEND_PORT}`。
- **原子保证只在「本地 main」**：合并不 push remote。push remote 是另一回事（最终一致 + 补偿事务），明确不在本 plan。
- 业务员在 sidebar 看到的「分支」是逻辑分支 `cr/<id>`，物理上在 N 个子仓各有一份。
- 在新分支 `plan7-multi-repo-atomic` 上做。

## File Structure

```
orchestrator/
  src/orchestrator/
    multi_repo.py                   # 核心：discover_sub_repos, RepoState, merge_to_main_atomic
    git_manager.py                  # 改写：所有方法遍历 sub_repos
    config.py                       # 加 projects_root: str
    adapters/impl/
      docker_preview.py             # compose 模式
      react_vite_stack.py           # build 也遍历子仓的 frontend
      _dev_runner_common.py         # commit_all / has_changes 遍历
      brainstorming_skill.py        # AGENTS.md 路径从子仓 root 改成项目 root
    pipeline.py                     # 用 multi_repo 替单仓调用；SSE log 加子仓前缀
    history_writer.py               # 写到项目 root/.doskill/history/
    models.py                       # 加 ChangeRequest.repos: JSON（哪些子仓动过）
  tests/
    test_multi_repo.py              # 子仓发现 + happy path 合并
    test_atomic_merge_rollback.py   # ★ 关键：phase 1/phase 2 失败回滚
    contract/
      test_docker_preview_compose.py
      test_react_vite_stack_multi.py

demo/                               # 现在的 demo 改成项目结构
  AGENTS.md                         # 顶层
  docker-compose.preview.yml        # 范例
  frontend/.git                     # 各自仓库（在 ECS 上 init）
  backend/.git

deploy/
  main-demo.sh                      # 改用 compose 起 main demo（统一形态）
```

## 关键算法：`merge_to_main_atomic`

```python
# orchestrator/src/orchestrator/multi_repo.py

@dataclass(frozen=True)
class RepoState:
    """合并前快照 —— 失败时回滚到这里。"""
    path: Path
    branch_name: str         # "cr/<id>"
    cr_sha_before: str       # rebase 前 cr 分支指向
    main_sha_before: str     # ff-merge 前 main 分支指向
    stashed: bool            # 是否 stash 了脏工作树

async def merge_to_main_atomic(
    sub_repos: list[Path], branch: str, *, log: LogSink | None = None,
) -> None:
    """两阶段原子合并：N 个子仓全成功才落地，任一失败 update-ref 回滚。
    成功路径：log 输出每仓 ✓；失败路径：log 输出冲突仓 + 已回滚的仓清单 + 抛 GitConflictError。
    """
    # ── 准备：stash + 快照 ────────────────────────────────────────
    states: list[RepoState] = []
    for repo in sub_repos:
        stashed = _stash_if_dirty(repo)
        cr_sha = _rev_parse(repo, branch)
        main_sha = _rev_parse(repo, "main")
        states.append(RepoState(repo, branch, cr_sha, main_sha, stashed))

    try:
        # ── Phase 1: dry-rebase all（探冲突）────────────────────────
        rebased: list[RepoState] = []
        for s in states:
            await _emit(log, f"[{s.path.name}] git rebase main")
            _checkout(s.path, branch)
            rc, out = _rebase(s.path, "main")
            if rc != 0:
                _rebase_abort(s.path)
                # 已 rebase 成功的全部 update-ref 回滚
                for done in rebased:
                    _update_ref(done.path, f"refs/heads/{branch}", done.cr_sha_before)
                    await _emit(log, f"[{done.path.name}] ↩ 已回滚 cr 分支到 {done.cr_sha_before[:8]}")
                raise GitConflictError(f"{s.path.name}: rebase 冲突\n{out}")
            rebased.append(s)

        # ── Phase 2: ff-merge all ────────────────────────────────────
        merged: list[RepoState] = []
        for s in states:
            await _emit(log, f"[{s.path.name}] git merge --ff-only {branch}")
            _checkout(s.path, "main")
            rc, out = _merge_ff_only(s.path, branch)
            if rc != 0:
                # 已 merged 的子仓回滚 main；所有 cr 也回滚
                for done in merged:
                    _update_ref(done.path, "refs/heads/main", done.main_sha_before)
                for s2 in states:
                    _update_ref(s2.path, f"refs/heads/{branch}", s2.cr_sha_before)
                raise GitConflictError(f"phase 2 unexpected: {s.path.name}: {out}")
            merged.append(s)
            await _emit(log, f"[{s.path.name}] ✓ merged")

    finally:
        # ── 收尾：成功 & 失败都 drop stash（build artifact 不要） ─────
        for s in states:
            if s.stashed:
                _stash_drop(s.path)
```

**为什么用 `update-ref` 而不是 `reset --hard`**：
- `update-ref` 只改 ref 指针，**不动工作树** —— 回滚是常数时间，且不影响业务员未提交的本地改动。
- `reset --hard` 会把工作树连带回退，可能丢业务员手工编辑的临时文件。
- `update-ref` 是 git 最底层的写操作，单 ref 修改本身在 git 内部是原子的（fcntl 文件锁）。

---

## Task 1: `multi_repo.py` — 子仓发现 + RepoState

**Files:**
- Create: `orchestrator/src/orchestrator/multi_repo.py`
- Create: `orchestrator/tests/test_multi_repo.py`

- [ ] **Step 1: 写失败测试** —
  - `test_discover_returns_dirs_with_dot_git`
  - `test_discover_ignores_top_level_files`
  - `test_discover_returns_sorted_by_name`（决定顺序：rebase / merge 都按字典序，可预期）
  - `test_discover_empty_project_returns_empty_list`
  - `test_repo_state_captures_pre_merge_shas`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `discover_sub_repos(project_path: Path) -> list[Path]`：扫顶层一级目录，凡含 `.git/` 的收集。`sorted()`。
  - `RepoState` dataclass（见上）
  - 底层 git 包装：`_rev_parse / _checkout / _rebase / _rebase_abort / _merge_ff_only / _update_ref / _stash_if_dirty / _stash_drop`（subprocess 包薄）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): multi_repo 子仓发现 + 底层 git 包装`

## Task 2: `merge_to_main_atomic` happy path

**Files:**
- Modify: `orchestrator/src/orchestrator/multi_repo.py`
- Modify: `orchestrator/tests/test_multi_repo.py`

- [ ] **Step 1: 写失败测试** —
  - `test_atomic_merge_2_repos_both_clean_succeeds`：tmp_path 起 2 个仓，各自 main + cr 分支有干净 commit；调 `merge_to_main_atomic`；断言两边 main 都 == cr 的 tip
  - `test_atomic_merge_logs_per_repo_progress`：log sink 收到 `[frontend]` `[backend]` 前缀的行
  - `test_atomic_merge_drops_stash_after_success`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** Phase 1 + Phase 2 主干（无回滚分支）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): merge_to_main_atomic happy path`

## Task 3: ★ Phase 1 冲突回滚

**Files:**
- Modify: `orchestrator/src/orchestrator/multi_repo.py`
- Create: `orchestrator/tests/test_atomic_merge_rollback.py`

- [ ] **Step 1: 写失败测试** —
  - `test_phase1_conflict_in_second_repo_rolls_back_first`：仓 A 干净、仓 B 故意造 rebase 冲突（main 改了同一文件）。调用应抛 `GitConflictError`，断言：仓 A 的 cr/<id> ref 等于 pre-rebase SHA、仓 B 的 cr/<id> 也等于原 SHA（abort 干净）、两边 main 完全没动
  - `test_phase1_first_repo_conflict_rolls_back_nothing`（没东西回滚）
  - `test_phase1_conflict_message_includes_repo_name`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** Phase 1 失败的 update-ref 回滚 + 抛错
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): atomic merge Phase 1 冲突回滚`

## Task 4: ★ Phase 2 失败回滚

**Files:**
- Modify: `orchestrator/src/orchestrator/multi_repo.py`
- Modify: `orchestrator/tests/test_atomic_merge_rollback.py`

- [ ] **Step 1: 写失败测试** —
  - `test_phase2_ff_failure_rolls_back_already_merged_main`：mock `_merge_ff_only` 第二次调用返回非 0（模拟并发 push）。断言：仓 A 的 main 被回滚到 main_sha_before，仓 A、B 的 cr 也被回滚
  - `test_phase2_failure_message_includes_repo_name`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** Phase 2 失败的回滚分支
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): atomic merge Phase 2 回滚`

## Task 5: stash 边界

**Files:**
- Modify: `orchestrator/tests/test_atomic_merge_rollback.py`

- [ ] **Step 1: 写失败测试** —
  - `test_stash_dropped_after_success`
  - `test_stash_dropped_after_phase1_conflict`（即使失败也要 drop —— build artifact 不能留）
  - `test_no_stash_on_clean_tree_skips_drop`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —— `finally` 块里遍历 stashed=True 的子仓 drop
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `test(orchestrator): atomic merge stash 边界`

## Task 6: `git_manager` 改造为多仓代理

**Files:**
- Modify: `orchestrator/src/orchestrator/git_manager.py`
- Modify: `orchestrator/tests/test_git_manager.py`

- [ ] **Step 1: 写失败测试**（在 multi-repo fixture 上） —
  - `test_create_branch_creates_in_all_sub_repos`
  - `test_delete_branch_deletes_in_all`
  - `test_merge_to_main_delegates_to_atomic`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `GitManager.__init__(project_path)` 而不是 `repo_path`，内部 `self._sub_repos = discover_sub_repos(...)`（lazy + 缓存）
  - 所有方法遍历 self._sub_repos
  - `merge_to_main` 直接 delegate 到 `multi_repo.merge_to_main_atomic`
- [ ] **Step 4: GREEN** —— 同时跑老的单仓测试，应有兼容性失败 → 在 fixture 里把 demo 改成「单子仓也是合法的多仓项目」，老测试照常过
- [ ] **Step 5: 提交** — `refactor(orchestrator): git_manager 多仓代理`

## Task 7: `_dev_runner_common.commit_all` 多仓

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/_dev_runner_common.py`
- Modify: `orchestrator/tests/contract/test_dev_runner_common.py`

- [ ] **Step 1: 写失败测试** —
  - `test_commit_all_walks_sub_repos`：dev runner 改了 frontend/file 和 backend/file，调用后两仓都各自有 commit
  - `test_commit_all_skips_clean_sub_repo`：只改了 frontend，backend 不创建空 commit
  - `test_has_changes_true_if_any_sub_repo_dirty`
  - `test_make_run_result_returns_dict_of_commit_shas`（changed=True 时 commit_sha 改为字典 `{repo_name: sha}`）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —— 遍历子仓，每个独立判 has_changes + commit；返回结果用 `{repo: sha}` 字典
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): commit_all 遍历子仓`

## Task 8: `DockerPreviewAdapter` compose 模式

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/docker_preview.py`
- Modify: `orchestrator/tests/contract/test_docker_preview.py`
- Create: `demo/docker-compose.preview.yml`（范例）

**Compose 文件约定**：
```yaml
# demo/docker-compose.preview.yml
services:
  frontend:
    build: ./frontend
    ports:
      - "${DOSKILL_FRONTEND_PORT}:5173"
    environment:
      VITE_API_URL: http://backend:8000
    networks:
      - default
  backend:
    build: ./backend
    environment:
      DATABASE_URL: mysql+pymysql://root:demopass@doskill-mysql:3306/demo
    networks:
      - default
      - doskill-net
networks:
  default:
  doskill-net:
    external: true
```

- [ ] **Step 1: 写失败测试** —
  - `test_serve_uses_compose_when_compose_file_exists`：mock subprocess，断言调 `docker compose -p ... -f docker-compose.preview.yml up -d`
  - `test_serve_injects_frontend_port_env`
  - `test_teardown_calls_compose_down`
  - `test_health_check_waits_for_frontend_service`
  - `test_serve_falls_back_to_single_dockerfile_when_no_compose`（向后兼容）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - 检测 `<project>/docker-compose.preview.yml` 存在 → compose 路径
  - `docker compose -p doskill-preview-<id> --project-directory <project> up -d --build`
  - PreviewInstance.handle = `doskill-preview-<id>`（compose project 名）
  - teardown：`docker compose -p doskill-preview-<id> down -v`
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): DockerPreviewAdapter compose 模式`

## Task 9: `ReactViteStackAdapter.build` 多仓

**Files:**
- Modify: `orchestrator/src/orchestrator/adapters/impl/react_vite_stack.py`
- Modify: `orchestrator/tests/contract/test_react_vite_stack.py`

- [ ] **Step 1: 写失败测试** —
  - `test_build_invokes_npm_build_per_frontend_sub_repo`（多个 frontend-like 仓时 build 每个）
  - `test_build_skips_backend_sub_repo`（按 `package.json` 判别）
  - `test_build_fails_if_any_sub_repo_fails`
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —— 遍历子仓，凡有 `package.json` 的跑 `npm run build`；任一失败收敛到 `BuildResult(ok=False, log=...)`
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): StackAdapter.build 多仓`

## Task 10: `BrainstormingSkill` AGENTS.md 路径调整

**Files:**
- Modify: `orchestrator/src/orchestrator/repo_init.py`（AGENTS.md 写到项目根，不是子仓根）
- Modify: `orchestrator/src/orchestrator/adapters/impl/brainstorming_skill.py`（同上）

- [ ] **Step 1: 修改 prompt** —— `INIT_PROMPT` 加一段「这个项目下有 N 个子仓库（前端 / 后端 / ...），都要扫，但 AGENTS.md 写在项目根目录（不是任一子仓内部）」
- [ ] **Step 2: 实现 + 跑** —— 改 doc_path 计算逻辑，跑 `/init` 在 demo 多仓结构上验证文件落地正确
- [ ] **Step 3: 提交** — `feat(orchestrator): AGENTS.md 写项目根而非子仓根`

## Task 11: 数据库 schema —— `ChangeRequest.repos`

**Files:**
- Modify: `orchestrator/src/orchestrator/models.py`
- Create: 数据库迁移 SQL（手工 ALTER TABLE）
- Modify: `orchestrator/src/orchestrator/repository.py`
- Modify: `orchestrator/tests/test_repository.py`

- [ ] **Step 1: 写失败测试** —
  - `test_create_cr_with_repos_json_field`
  - `test_repos_default_empty_dict`
  - `test_update_repos_dict_after_commit_all_returns`（pipeline 拿到 `{repo: sha}` 后写回）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `ChangeRequest.repos: dict[str, str] | None`（JSON 列）—— 哪些子仓被改了 + 各自 commit SHA
  - `repository.update_repos(request_id, repos_dict)`
  - ALTER TABLE 迁移脚本
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): ChangeRequest.repos JSON 字段`

## Task 12: Pipeline 集成

**Files:**
- Modify: `orchestrator/src/orchestrator/pipeline.py`
- Modify: `orchestrator/tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试** —
  - `test_pipeline_uses_multi_repo_create_branch`
  - `test_pipeline_writes_repos_dict_after_dev_runner`
  - `test_pipeline_atomic_merge_failure_sets_failed_phase_merging`（mock merge 抛 GitConflictError）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现** —
  - `pipeline.run()` 不再读 `settings.demo_repo_path` 而是 `settings.projects_root + project_name`（来自 CR 关联的项目）
  - `coding` 阶段调 `dev_runner.run` 后接 `make_run_result` 返回 `RunResult(changed, commit_shas: dict)` → `repository.update_repos(...)`
  - SSE log 加子仓前缀（已在 multi_repo 里实现）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(orchestrator): pipeline 接入多仓 + atomic merge`

## Task 13: main-demo.sh compose 化

**Files:**
- Modify: `deploy/main-demo.sh`
- Create: `demo/docker-compose.main.yml`（与 preview 文件结构一致，端口不同）

- [ ] **Step 1: 改脚本** —— 不再分别 build/run 两个容器，统一 `docker compose -f docker-compose.main.yml up -d --build`（`--rebuild` 时强制 rebuild）
- [ ] **Step 2: 跑 deploy 验证** —— main demo 在 :5199 仍然正常工作
- [ ] **Step 3: 提交** — `refactor(deploy): main-demo 改 compose`

## Task 14: 扩展端 — 项目选择 + 子仓列表

**Files:**
- Modify: `extension/src/lib/config.ts`（Plan 6 schema 扩展）
- Modify: `extension/src/ui/panels/SettingsPanel.tsx`
- Create: `extension/tests/project-config.test.tsx`

**Schema 扩展**：
```typescript
server: z.object({
  ...,
  projects: z.array(z.object({
    name: z.string(),                   // "my-product"
    rootPath: z.string(),                // "/opt/doskill/projects/my-product"
    subRepos: z.array(z.object({
      name: z.string(),                  // "frontend" / "backend"
      gitUrl: z.string().optional(),     // 远端 URL（可选，本地 init 时不需要）
    })),
    activeBranch: z.string().default('main'),
  })),
  activeProject: z.string().optional(),  // 当前用哪个项目
})
```

- [ ] **Step 1: 写失败测试** —
  - `test_settings_panel_shows_project_list`
  - `test_can_add_new_project_with_sub_repos`
  - `test_active_project_persists`
  - `test_capture_uses_active_project_repo`（service-worker 创建 CR 时带 project_id）
- [ ] **Step 2: RED**
- [ ] **Step 3: 实现**
- [ ] **Step 4: GREEN**
- [ ] **Step 5: 提交** — `feat(extension): 多项目 + 子仓配置`

## Task 15: 端到端联调（真实多仓）

**Files:**
- Create: `orchestrator/tests/test_e2e_multi_repo.py`
- Modify: `docs/RUNBOOK.md`（新章节 Plan 7 验证）

- [ ] **Step 1: 端到端 fixture** —
  - tmp_path 起项目根，内含 `frontend/` `backend/` 各 git init + 初始 commit
  - 起一条 CR，mock 一个 fake dev runner 修改两个仓的文件
  - 走完整 pipeline 到 preview-ready（mock DockerPreviewAdapter）
  - merge 调真实 `merge_to_main_atomic`
  - 断言：两仓 main 都收到了 cr commits、`ChangeRequest.repos` 字段有正确的 SHA 字典
- [ ] **Step 2: 失败路径 E2E** —
  - 同上 fixture，但让 backend cr 分支与 backend main 冲突
  - merge 时应抛 conflict、CR 进 fail_phase=merging、两仓的 main + cr 都回到 pre-merge SHA
- [ ] **Step 3: 真机验证（可选）** —— 在 ECS 上准备多仓 demo，走一遍真实流程
- [ ] **Step 4: 提交** — `test(orchestrator): Plan 7 多仓 E2E`

---

## 验收标准（Plan 7 完成定义）

- [ ] `discover_sub_repos` 准确发现项目下所有含 `.git/` 的子目录
- [ ] `create_branch` / `delete_branch` 在所有子仓同步创建/删除 `cr/<id>`
- [ ] `commit_all` 遍历子仓，只 commit 有变化的，不创建空 commit
- [ ] **`merge_to_main_atomic` 幂等 + 原子**：
  - happy path 两仓都成功 → 两 main 都前进、两 cr 都被 ff merged
  - Phase 1 任一冲突 → 抛 GitConflictError，**所有子仓的 main 和 cr 都和合并前完全一致**（含 reflog 不留临时痕迹）
  - Phase 2 任一失败 → 同上完全一致
  - 任一路径结束后工作树无残留 stash
- [ ] `DockerPreviewAdapter` 在含 `docker-compose.preview.yml` 的项目走 compose 路径；无文件时回退老的 `docker build frontend` 路径
- [ ] AGENTS.md 写在项目根（不在任一子仓内部），覆盖所有子仓的内容描述
- [ ] 扩展端可在 SettingsPanel 配置 N 个项目、切换 activeProject
- [ ] 业务员视角：sidebar 显示「frontend ✓ / backend ✓ 已合并」或「backend ✗ rebase 冲突，已全部回滚」
- [ ] `pytest` 全绿（含 atomic 回滚的 4 个关键测试）；`git status` 干净
- [ ] RUNBOOK 文档化多仓部署流程
- [ ] **关键性能**：2 子仓的原子合并 < 5s（不算 LLM 时间）

---

## 风险与显式不做

- **跨仓 commit 顺序**（API 先 backend 后 frontend）：LLM 在 dev runner 阶段自己决定文件改动顺序；本 plan 不强制 commit 顺序。如果出现 API 变更但 UI 没跟上的情况，业务员在预览时会看到 backend 报错 —— 这是验收时 catch 的，不在合并层兜底。
- **跨仓 dependency**（frontend 引用 backend 的类型）：超出 MVP。约定共享 schema 单独一个子仓或通过 OpenAPI 生成。
- **push remote 的原子性**：明确不在本 plan。本地 main 原子已经满足业务员「要么看到新版，要么看到老版」的诉求。要 push remote 留给 Plan 8。
- **删除子仓 / 改名**：项目 lifecycle 管理（增删改子仓）通过扩展 SettingsPanel 手工操作，不在 atomic 合并范围内。
- **多 cr 并发**：当前 quota=5，但同一项目并发跑多条 cr 时，atomic merge 在 ff-only 检查时会拦下后到的，业务员看到 fail_phase=merging 重试即可。本 plan 不做更复杂的并发协调。

---

## 需要用户提供（运行 Plan 7 前的一次性清单）

1. 决定：`projects_root` 路径默认 `/opt/doskill/projects/`，可改。
2. demo 仓库结构调整：现在是单仓 `demo/`，要重新组织成 `demo/{frontend, backend}/` 各自 git init。需要确认是否在 plan 期间一次性迁移好（推荐）。
3. `docker-compose.preview.yml` 的范例由 Plan 7 提供，但每个新项目接入时**项目作者需要自己写一份**。Plan 6 的帮助内容里要加一篇 `compose-file.md` 教怎么写。
4. 远端 git 仓库托管：本 plan 不 push remote，但 SettingsPanel 字段里要不要预留「git URL」？plan 默认预留（schema 已有 `gitUrl` 字段）但 Plan 7 范围内不消费。
