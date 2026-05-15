**Demo 仓库路径** 是 orchestrator 在服务器上拉取业务代码的本地路径。AI 改代码后会在这个目录里 `git checkout -b cr/xxx` + commit。

1. 默认填 `/opt/doskill/demo`（`provision.sh` 会自动把 demo 仓库 `git clone` 到这里）
2. 如果你换了路径或者自己 clone 在别处，填**服务器上**该仓库的绝对路径
3. 该路径必须满足：
   - 是一个合法的 git 仓库（`git status` 不报错）
   - orchestrator 进程对它有读写权限
   - 主分支干净（没有未提交改动）

**验证**：保存后，扩展会调一次 `/admin/diagnostics`；正常会显示「git 仓库 OK · 当前分支 main · 最近 commit xxx」。报「not a git repository」就回服务器 `ls` 检查路径，`cd` 进去 `git status` 确认能跑通。

**注意**：换路径会立即生效，不需要重启 orchestrator；但已经在跑的 CR 会继续用旧路径。

**git 入门** 不熟的话先看 [Pro Git 中文版](https://git-scm.com/book/zh/v2) 第 1-2 章。
