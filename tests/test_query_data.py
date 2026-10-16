import sqlite3

import pytest

from sluice.tools.query_data import QueryDataTool


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "demo.db"
    conn = sqlite3.connect(p)
    conn.execute("create table sales (id integer, city text, amount real)")
    conn.executemany(
        "insert into sales values (?, ?, ?)",
        [(1, "北京", 100.0), (2, "上海", 200.0), (3, "北京", 50.0)],
    )
    conn.commit()
    conn.close()
    return str(p)


def test_select_returns_markdown_table(db_path):
    result = QueryDataTool(db_path).execute({"sql": "select city, amount from sales order by id"})
    assert not result.is_error
    assert "city" in result.content and "北京" in result.content


def test_non_select_rejected(db_path):
    result = QueryDataTool(db_path).execute({"sql": "delete from sales"})
    assert result.is_error
    assert "只读" in result.content


def test_sql_error_is_error_result(db_path):
    result = QueryDataTool(db_path).execute({"sql": "select nope from sales"})
    assert result.is_error


def test_missing_db_file():
    result = QueryDataTool("Z:/no/such/db.db").execute({"sql": "select 1"})
    assert result.is_error


def test_row_cap(db_path):
    conn = sqlite3.connect(db_path)
    conn.executemany("insert into sales values (?, ?, ?)", [(i, "深圳", 1.0) for i in range(100)])
    conn.commit()
    conn.close()
    result = QueryDataTool(db_path).execute({"sql": "select * from sales"})
    assert result.content.count("\n") == 51  # 表头 + 分隔 + 50 行
    assert "仅展示前 50 行" in result.content


def test_with_dml_rejected(db_path):
    result = QueryDataTool(db_path).execute({"sql": "with x as (select 1) delete from sales"})
    assert result.is_error
    assert "只读" in result.content


def test_pipe_in_value_escaped(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("insert into sales values (4, '北|京', 1.0)")
    conn.commit()
    conn.close()
    result = QueryDataTool(db_path).execute({"sql": "select city from sales where id = 4"})
    assert not result.is_error
    assert "北\\|京" in result.content


def test_multi_statement_rejected(db_path):
    result = QueryDataTool(db_path).execute({"sql": "select 1; select 2"})
    assert result.is_error
