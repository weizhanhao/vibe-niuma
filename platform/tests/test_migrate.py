"""迁移器测试 —— 之前完全没有迁移方案，只有 create_all()。"""
import pytest
from sqlalchemy import create_engine, inspect, text

from vplatform.core import migrate


@pytest.fixture()
def eng(tmp_path):
    return create_engine(f"sqlite:///{tmp_path}/m.db")


@pytest.fixture()
def mdir(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    return d


def w(d, name, body):
    (d / name).write_text(body, encoding="utf-8")


def test_applies_in_order_and_records_ledger(eng, mdir):
    w(mdir, "0001_init.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    w(mdir, "0002_add_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    assert migrate.upgrade(eng, mdir) == ["0001_init", "0002_add_b"]

    names = set(inspect(eng).get_table_names())
    assert {"a", "b", "schema_migrations"} <= names
    assert sorted(migrate.applied(eng)) == ["0001", "0002"]


def test_second_run_is_a_noop(eng, mdir):
    w(mdir, "0001_init.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    migrate.upgrade(eng, mdir)
    assert migrate.upgrade(eng, mdir) == []       # 幂等


def test_modified_applied_migration_is_rejected(eng, mdir):
    """**已应用的迁移不可变。**

    悄悄改过的迁移会让新环境跑出与老环境不同的 schema，
    这类漂移查起来极其痛苦。校验和当场拦住。
    """
    w(mdir, "0001_init.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    migrate.upgrade(eng, mdir)
    w(mdir, "0001_init.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT);")
    with pytest.raises(RuntimeError, match="内容被修改过"):
        migrate.pending(eng, mdir)


def test_failure_stops_the_chain(eng, mdir):
    """一条失败就停，不继续跑后面的 —— MySQL 没有事务性 DDL，
    继续跑会留下更难收拾的半迁移状态。"""
    w(mdir, "0001_ok.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    w(mdir, "0002_bad.sql", "CREATE TABLE ;;; broken")
    w(mdir, "0003_never.sql", "CREATE TABLE c (id INTEGER PRIMARY KEY);")
    with pytest.raises(Exception):
        migrate.upgrade(eng, mdir)
    names = set(inspect(eng).get_table_names())
    assert "a" in names
    assert "c" not in names                      # 后面的没跑
    assert sorted(migrate.applied(eng)) == ["0001"]


@pytest.mark.parametrize("bad", ["init.sql", "1_x.sql", "0001-x.sql", "0001_X.sql"])
def test_bad_filename_rejected(eng, mdir, bad):
    w(mdir, bad, "SELECT 1;")
    with pytest.raises(ValueError, match="文件名不合规"):
        migrate.discover(mdir)


def test_duplicate_version_rejected(eng, mdir):
    w(mdir, "0001_a.sql", "SELECT 1;")
    w(mdir, "0001_b.sql", "SELECT 1;")
    with pytest.raises(ValueError, match="版本号重复"):
        migrate.discover(mdir)


def test_status_reports_current_and_pending(eng, mdir):
    w(mdir, "0001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    w(mdir, "0002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    migrate.upgrade(eng, mdir)
    w(mdir, "0003_c.sql", "CREATE TABLE c (id INTEGER PRIMARY KEY);")
    st = migrate.status(eng, mdir)
    assert st["current"] == "0002" and st["pending"] == ["0003_c"]


def test_shipped_initial_migration_matches_the_models():
    """**仓库里的 0001_init.sql 必须和当前模型一致。**

    不一致意味着有人改了 models.py 却没加迁移 —— 新环境跑出来的 schema
    会和 create_all 的不一样。
    """
    from sqlalchemy import create_mock_engine
    from vplatform.core.models import Base

    stmts = []
    mock = create_mock_engine(
        "mysql+pymysql://",
        lambda sql, *a, **kw: stmts.append(str(sql.compile(dialect=mock.dialect)).strip()))
    Base.metadata.create_all(mock, checkfirst=False)
    model_tables = {s.split()[2] for s in stmts if s.startswith("CREATE TABLE")}

    shipped = (migrate.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    shipped_tables = {ln.split()[2] for ln in shipped.splitlines()
                      if ln.startswith("CREATE TABLE")}
    assert model_tables == shipped_tables, (
        f"模型与初始迁移不一致。仅模型有：{sorted(model_tables - shipped_tables)}；"
        f"仅迁移有：{sorted(shipped_tables - model_tables)}")

    # **列也要比。**
    # 只比表名的话，给已有表加一列能悄悄溜过去 —— 新环境按 0001 建出来
    # 就少这一列，跑起来才报 Unknown column，而那时候已经上线了。
    def _cols(sql: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        table = None
        for ln in sql.splitlines():
            st = ln.strip()
            if st.startswith("CREATE TABLE"):
                table = st.split()[2]
                out[table] = set()
            elif table and st.startswith(")"):
                table = None
            elif table and st:
                first = st.split()[0].strip("`,")
                # 跳过约束行，只留列定义
                if first.upper() not in ("PRIMARY", "FOREIGN", "UNIQUE",
                                         "CONSTRAINT", "KEY", "INDEX", "CHECK"):
                    out[table].add(first)
        return out

    model_cols = _cols("\n".join(s for s in stmts if s.startswith("CREATE TABLE")))
    shipped_cols = _cols(shipped)
    for t in sorted(model_tables):
        assert model_cols.get(t) == shipped_cols.get(t), (
            f"表 {t} 的列不一致。仅模型有："
            f"{sorted((model_cols.get(t) or set()) - (shipped_cols.get(t) or set()))}；"
            f"仅迁移有："
            f"{sorted((shipped_cols.get(t) or set()) - (model_cols.get(t) or set()))}")
